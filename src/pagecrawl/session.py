import asyncio
import contextlib

import uvicorn

from pagecrawl.browser import PlaywrightSource
from pagecrawl.crawl import crawl_feed
from pagecrawl.monitor import create_app
from pagecrawl.state import AppState
from pagecrawl.store import Store


def _config(app: object, port: int) -> uvicorn.Config:
    return uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=2,
    )


async def run_crawl_job(
    state: AppState,
    store: Store,
    profile_path,
    idle_timeout: float,
) -> None:
    # Đọc headless từ state mỗi lần chạy để đổi chế độ giữa các phiên crawl.
    source = PlaywrightSource(profile_path, headless=state.headless)
    try:
        await source.start(state.page_url)
        state.page = source.page
        state.set_status("waiting_login")
        mode = "headless" if source.headless else "có giao diện"
        state.log(f"đã mở Chromium ({mode}) — đăng nhập nếu cần, rồi bấm Bắt đầu crawl")
        watcher = asyncio.create_task(_watch_session(source, state))
        await state.start_event.wait()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        if state.stop_event.is_set() and state.status == "waiting_login":
            state.stop_reason = state.stop_reason or "user_stop"
            state.set_status("done")
        else:
            await crawl_feed(source, store, state, idle_timeout=idle_timeout)
        _finish(store, state, state.status)
        state.log(
            f"xong: mới={state.new_count} cập nhật={state.updated_count} bỏ qua={state.skip_count} ({state.stop_reason})"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state.stop_reason = str(exc)
        state.set_status("error")
        state.log(f"lỗi: {exc}")
        _finish(store, state, "error")
    finally:
        await source.close()
        state.page = None


def _finish(store: Store, state: AppState, status: str) -> None:
    if state.run_id is None:
        return
    store.finish_run(
        state.run_id,
        status,
        state.stop_reason,
        state.new_count,
        state.skip_count,
        state.updated_count,
        state.rounds,
    )


async def run_session(
    page_url: str,
    db_path,
    profile_path,
    *,
    refresh: bool = False,
    port: int = 8787,
    headless: bool = False,
    idle_timeout: float = 8.0,
    keep_monitor: bool = True,
    state: AppState | None = None,
    collection: str = "mặc định",
) -> int:
    store = Store(db_path)
    state = state or AppState()
    state.attach()
    state.controls = False
    col = store.create_collection(collection)
    state.collection_id = col["id"]
    state.collection_name = col["name"]
    state.page_url = page_url
    state.refresh = refresh
    state.idle_timeout = idle_timeout
    state.headless = headless
    state.run_id = store.create_run(page_url)
    app = create_app(state, store)
    server = uvicorn.Server(_config(app, port))
    server_task = asyncio.create_task(server.serve())
    exit_code = 0
    try:
        await _wait_until_started(server, port)
        state.log(f"monitor http://127.0.0.1:{port}")
        await run_crawl_job(state, store, profile_path, idle_timeout)
        if state.status == "error":
            exit_code = 1
        aborted_early = state.stop_reason == "user_stop" and state.rounds == 0 and state.new_count == 0
        if keep_monitor and state.shutdown_event is not None and not aborted_early:
            state.log("Monitor vẫn mở. Bấm Dừng hoặc nhấn Ctrl+C để thoát.")
            await state.shutdown_event.wait()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        exit_code = 1
        state.stop_reason = str(exc)
        state.set_status("error")
        state.log(f"lỗi: {exc}")
        _finish(store, state, "error")
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await server_task
        store.close()
    return exit_code


def _make_comment_opener(state: AppState, store: Store, profile_path, headless: bool):
    """Tạo hàm chạy nền để lấy bình luận từ web monitor."""
    from pagecrawl.browser import PlaywrightSource
    from pagecrawl.comments import crawl_comments

    async def opener(collection_id: int, min_comments: int, limit: int | None) -> None:
        source: PlaywrightSource | None = None
        try:
            def say(line: str) -> None:
                state.log(line)
                if state.comment_total:
                    state.comment_current = line

            progress = store.comment_progress(collection_id, min_comments)
            state.comment_total = progress["pending"]
            source = PlaywrightSource(profile_path, headless=headless)
            await source.start("https://www.facebook.com/")
            done = await crawl_comments(
                source,
                store,
                collection_id=collection_id,
                min_comments=min_comments,
                limit=limit,
                stop=state.should_stop_comments,
                on_progress=say,
            )
            state.comment_done = done
            state.comment_error = store.comment_progress(collection_id, min_comments)["error"]
            state.log(f"lấy bình luận xong: {done} bài")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.log(f"lỗi lấy bình luận: {exc}")
        finally:
            if source is not None:
                await source.close()
            state.comment_running = False
            state.comment_current = ""
            state.emit_status()

    return opener


async def serve_ui(db_path, profile_path, *, port: int = 8787, headless: bool = False) -> None:
    store = Store(db_path)
    state = AppState()
    state.attach()
    state.controls = True
    state.headless = headless
    default_col = store.get_collection(state.collection_id)
    if default_col is not None:
        state.collection_name = default_col["name"]
    state.set_status("idle")

    async def opener(url: str, refresh: bool, idle_timeout: float, headless: bool) -> None:
        try:
            state.headless = headless
            state.run_id = store.create_run(url)
            await run_crawl_job(state, store, profile_path, idle_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.stop_reason = str(exc)
            state.set_status("error")
            state.log(f"lỗi: {exc}")

    app = create_app(
        state,
        store,
        opener=opener,
        comment_opener=_make_comment_opener(state, store, profile_path, headless),
    )
    server = uvicorn.Server(_config(app, port))
    try:
        await server.serve()
    finally:
        store.close()


async def _wait_until_started(server: uvicorn.Server, port: int) -> None:
    started = server.started
    if hasattr(started, "wait"):
        await asyncio.wait_for(started.wait(), 10)
        return
    for _ in range(200):
        if server.started:
            return
        await asyncio.sleep(0.05)
    raise RuntimeError(f"không mở được monitor tại cổng {port}")


async def _watch_session(source: PlaywrightSource, state: AppState) -> None:
    last = None
    try:
        while not state.start_event.is_set():
            ready = await source.session_hint()
            if ready != last:
                last = ready
                state.session_ready = ready
                state.log("session còn — feed đã hiện" if ready else "chưa thấy feed (đăng nhập nếu cần)")
                state.emit_status()
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        return
