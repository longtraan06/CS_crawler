import asyncio
from typing import Protocol

from pagecrawl.models import Post, content_hash
from pagecrawl.state import AppState
from pagecrawl.store import Store


class FeedSource(Protocol):
    async def prepare_round(self, timeout: float) -> bool: ...

    async def expand(self) -> None: ...

    async def collect_batch(self) -> tuple[list[Post], bool | None]: ...

    async def scroll(self) -> None: ...

    async def fetch_text(self, url: str) -> str: ...


async def crawl_feed(
    source: FeedSource,
    store: Store,
    state: AppState,
    *,
    idle_timeout: float = 8.0,
    stale_limit: int = 3,
) -> None:
    seen: set[str] = set()
    stale = 0
    state.set_status("running")
    while not state.stop_event.is_set():
        if getattr(source, "blocked", False):
            state.stop_reason = "rate_limited"
            state.set_status("done")
            state.log("bị giới hạn tốc độ — nghỉ một lát rồi chạy lại")
            return
        if getattr(source, "needs_login", False):
            _need_login(state)
            return
        ready = await source.prepare_round(idle_timeout)
        if getattr(source, "needs_login", False):
            _need_login(state)
            return
        if state.stop_event.is_set():
            break
        if not ready:
            state.set_status("stalled")
        await source.expand()
        posts, has_next = await source.collect_batch()
        state.rounds += 1
        fresh: list[Post] = []
        for post in posts:
            if post.post_id in seen:
                continue
            seen.add(post.post_id)
            fresh.append(post)
        if fresh:
            stale = 0
            state.set_status("running")
        else:
            stale += 1
        for post in fresh:
            if not post.text and post.permalink:
                filled = (await source.fetch_text(post.permalink)).strip()
                if filled:
                    post.text = filled
            _persist(store, state, post)
        state.touch_run(store)
        if has_next is False:
            state.stop_reason = "cursor_empty"
            state.set_status("done")
            return
        if stale >= stale_limit:
            state.stop_reason = "no_new_posts"
            state.set_status("done")
            return
        await source.scroll()
        await asyncio.sleep(0.2)
    state.stop_reason = state.stop_reason or "user_stop"
    state.set_status("done")


def _need_login(state: AppState) -> None:
    state.stop_reason = "login_required"
    state.set_status("waiting_login")
    state.log("Facebook đưa ra trang đăng nhập — đăng nhập lại rồi chạy tiếp")


def _persist(store: Store, state: AppState, post: Post) -> None:
    existing = store.get_post(post.post_id, collection_id=state.collection_id)
    digest = content_hash(post)
    if existing is not None and (not state.refresh or existing["content_hash"] == digest):
        state.skip_count += 1
        state.emit({"type": "post", "post": _public(existing), "skipped": True, "updated": False})
        state.log(f"skip {post.post_id}")
        return
    saved = store.upsert(state.page_url, post, collection_id=state.collection_id)
    updated = existing is not None
    if updated:
        state.updated_count += 1
    else:
        state.new_count += 1
    state.emit({"type": "post", "post": _public(saved), "skipped": False, "updated": updated})
    state.log(("cập nhật " if updated else "lưu ") + post.post_id)


def _public(row: dict) -> dict:
    return {
        "post_id": row["post_id"],
        "author": row["author"],
        "created_at": row["created_at"],
        "time_label": row["time_label"],
        "like_count": row["like_count"],
        "comment_count": row["comment_count"],
        "text": row["text"],
        "image_urls": row["image_urls"],
        "permalink": row.get("permalink"),
        "collection_id": row.get("collection_id", 1),
    }