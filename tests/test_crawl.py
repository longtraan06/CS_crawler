import asyncio
from pathlib import Path

from pagecrawl.crawl import crawl_feed
from pagecrawl.models import Post
from pagecrawl.state import AppState
from pagecrawl.store import Store


class FakeSource:
    def __init__(self, rounds: list[tuple[list[Post], bool | None]]) -> None:
        self.rounds = rounds
        self.index = 0
        self.scrolls = 0
        self.fetches: list[str] = []

    async def prepare_round(self, timeout: float) -> bool:
        return True

    async def expand(self) -> None:
        return None

    async def collect_batch(self) -> tuple[list[Post], bool | None]:
        if self.index >= len(self.rounds):
            return [], None
        item = self.rounds[self.index]
        self.index += 1
        return item[0], item[1]

    async def scroll(self) -> None:
        self.scrolls += 1

    async def fetch_text(self, url: str) -> str:
        self.fetches.append(url)
        return "Nội dung chỉ có trên permalink"


def _post(post_id: str, text: str = "nội dung", likes: int = 1, images: list[str] | None = None, permalink: str | None = None) -> Post:
    return Post(
        post_id=post_id,
        author="Trang Demo",
        created_at="2026-10-02T08:15:00Z",
        time_label="2 giờ",
        like_count=likes,
        comment_count=2,
        text=text,
        image_urls=images or [],
        permalink=permalink,
    )


def _run(tmp_path: Path, source: FakeSource, refresh: bool = False) -> tuple[AppState, Store]:
    store = Store(tmp_path / "crawler.db", tmp_path / "posts.jsonl")
    state = AppState()
    state.attach()
    state.page_url = "http://example.test/page"
    state.refresh = refresh
    state.run_id = store.create_run(state.page_url)

    async def go() -> None:
        await crawl_feed(source, store, state, idle_timeout=0.1)

    asyncio.run(go())
    return state, store


def test_stops_when_cursor_ends_without_extra_scroll(tmp_path: Path):
    source = FakeSource([([_post("a"), _post("b")], True), ([_post("c")], False)])
    state, store = _run(tmp_path, source)
    assert state.stop_reason == "cursor_empty"
    assert state.new_count == 3
    assert state.skip_count == 0
    assert source.scrolls == 1
    saved = store.get_post("b")
    assert saved["author"] == "Trang Demo"
    assert saved["like_count"] == 1
    assert saved["comment_count"] == 2
    assert saved["text"] == "nội dung"
    lines = (tmp_path / "posts.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    store.close()


def test_stops_after_three_empty_rounds(tmp_path: Path):
    source = FakeSource([([_post("a")], True)])
    state, store = _run(tmp_path, source)
    assert state.stop_reason == "no_new_posts"
    assert state.rounds == 4
    assert source.scrolls == 3
    store.close()


def test_skips_posts_already_stored(tmp_path: Path):
    store = Store(tmp_path / "crawler.db")
    store.upsert("http://example.test/page", _post("a", text="cũ"))
    source = FakeSource([([_post("a", text="cũ"), _post("b")], False)])
    state = AppState()
    state.attach()
    state.page_url = "http://example.test/page"
    state.run_id = store.create_run(state.page_url)

    async def go() -> None:
        await crawl_feed(source, store, state, idle_timeout=0.1)

    asyncio.run(go())
    assert state.new_count == 1
    assert state.skip_count == 1
    assert store.get_post("a")["text"] == "cũ"
    store.close()


def test_refresh_updates_only_when_hash_changes(tmp_path: Path):
    store = Store(tmp_path / "crawler.db")
    store.upsert("http://example.test/page", _post("a", likes=1))
    store.upsert("http://example.test/page", _post("b", likes=1))
    source = FakeSource([([_post("a", likes=9), _post("b", likes=1)], False)])
    state = AppState()
    state.attach()
    state.page_url = "http://example.test/page"
    state.refresh = True
    state.run_id = store.create_run(state.page_url)

    async def go() -> None:
        await crawl_feed(source, store, state, idle_timeout=0.1)

    asyncio.run(go())
    assert state.updated_count == 1
    assert state.skip_count == 1
    assert state.new_count == 0
    assert store.get_post("a")["like_count"] == 9
    store.close()


def test_empty_text_is_filled_from_permalink(tmp_path: Path):
    source = FakeSource([([_post("p", text="", permalink="http://example.test/post/p")], False)])
    state, store = _run(tmp_path, source)
    assert source.fetches == ["http://example.test/post/p"]
    assert store.get_post("p")["text"] == "Nội dung chỉ có trên permalink"
    store.close()


def test_stop_event_ends_loop(tmp_path: Path):
    source = FakeSource([([_post("a")], True)])
    store = Store(tmp_path / "crawler.db")
    state = AppState()
    state.attach()
    state.page_url = "http://example.test/page"
    state.run_id = store.create_run(state.page_url)
    state.stop_event.set()

    async def go() -> None:
        await crawl_feed(source, store, state, idle_timeout=0.1)

    asyncio.run(go())
    assert state.stop_reason == "user_stop"
    assert state.new_count == 0
    store.close()


def test_permalink_is_stored_and_exposed(tmp_path: Path):
    """Link bài viết phải được lưu và trả về cho dashboard."""
    url = "https://www.facebook.com/groups/demo/permalink/999/"
    source = FakeSource([([_post("p1", permalink=url)], False)])
    state, store = _run(tmp_path, source)
    assert state.new_count == 1
    saved = store.get_post("p1")
    assert saved["permalink"] == url
    listed = store.list_posts(limit=10)
    assert listed["posts"][0]["permalink"] == url
    store.close()
