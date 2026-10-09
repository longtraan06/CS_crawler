import asyncio
import json
import socket
import urllib.request

from pagecrawl.mocksite import serve_mock
from pagecrawl.session import run_session
from pagecrawl.state import AppState
from pagecrawl.store import Store


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_status(state: AppState, status: str) -> None:
    async def wait() -> None:
        for _ in range(200):
            if state.status == status and state.page is not None:
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"timeout waiting for {status}, got {state.status} {state.stop_reason}")

    return wait()


async def _login_and_start(state: AppState, port: int) -> None:
    await _wait_status(state, "waiting_login")
    page = state.page
    assert await page.evaluate("navigator.webdriver") is False
    assert await page.evaluate("navigator.plugins.length") > 0
    if await page.locator("input[type=password]").count():
        await page.click("button[type=submit]")
    await page.wait_for_selector("[data-post-id]", timeout=15000)

    def send() -> None:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/start", method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise AssertionError(response.status)

    await asyncio.to_thread(send)


async def _run_once(url: str, db, profile, port: int) -> AppState:
    state = AppState()
    task = asyncio.create_task(
        run_session(
            url,
            db,
            profile,
            port=port,
            headless=True,
            idle_timeout=3,
            keep_monitor=False,
            state=state,
        )
    )
    await _login_and_start(state, port)
    code = await task
    assert code == 0, state.stop_reason
    return state


def test_mock_site_end_to_end(tmp_path):
    mock = serve_mock(0)
    try:
        mock_port = mock.server_address[1]
        url = f"http://127.0.0.1:{mock_port}/page/demo"
        db = tmp_path / "crawler.db"
        profile = tmp_path / "profile"

        async def scenario() -> tuple[AppState, AppState]:
            first = await _run_once(url, db, profile, _free_port())
            second = await _run_once(url, db, profile, _free_port())
            return first, second

        first, second = asyncio.run(scenario())
        store = Store(db)
        try:
            result = store.list_posts(page_url=url, limit=20)
        finally:
            store.close()
        by_id = {post["post_id"]: post for post in result["posts"]}
        assert set(by_id) == {"post_1", "post_2", "post_3", "post_4", "post_5", "post_6"}
        assert by_id["post_1"]["author"] == "Trang Demo"
        assert by_id["post_1"]["like_count"] == 120
        assert by_id["post_1"]["comment_count"] == 14
        assert by_id["post_1"]["text"] == "Bài một có ảnh"
        assert by_id["post_1"]["image_urls"] == [f"http://127.0.0.1:{mock_port}/media/a.jpg"]
        assert by_id["post_2"]["image_urls"] == [
            f"http://127.0.0.1:{mock_port}/media/b.jpg",
            f"http://127.0.0.1:{mock_port}/media/c.jpg",
        ]
        assert by_id["post_3"]["image_urls"] == []
        assert by_id["post_3"]["like_count"] == 8
        assert by_id["post_4"]["text"] == "Bài bốn\nxuống dòng"
        assert by_id["post_5"]["text"] == "Nội dung chỉ có trên permalink"
        assert by_id["post_6"]["comment_count"] == 4
        assert first.new_count == 6
        assert second.new_count == 0
        assert second.skip_count == 6
        assert "không phải bài viết" not in json.dumps(result, ensure_ascii=False)
        assert by_id["post_1"]["permalink"] == f"http://127.0.0.1:{mock_port}/post/post_1"
    finally:
        mock.shutdown()
