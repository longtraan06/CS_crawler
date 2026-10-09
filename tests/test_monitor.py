from fastapi.testclient import TestClient

from pagecrawl.models import Post
from pagecrawl.monitor import create_app
from pagecrawl.state import AppState
from pagecrawl.store import Store


def test_dashboard_and_start_gate(tmp_path):
    state = AppState()
    store = Store(tmp_path / "crawler.db")
    app = create_app(state, store)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Bắt đầu crawl" in page.text
        assert "Mở trang" in page.text
        missing = client.post("/api/open", json={"url": "https://example.test/page"})
        assert missing.status_code == 404
        blocked = client.post("/api/start")
        assert blocked.status_code == 409
        state.status = "waiting_login"
        started = client.post("/api/start")
        assert started.status_code == 200
        assert state.start_event.is_set()
        client.post("/api/stop")
        assert state.stop_event.is_set()
        listed = client.get("/api/posts")
        assert listed.json()["total"] == 0
    store.close()


def test_open_job_from_ui(tmp_path):
    state = AppState()
    state.attach()
    state.controls = True
    state.set_status("idle")
    store = Store(tmp_path / "crawler.db")
    seen = {}

    async def opener(url: str, refresh: bool, idle_timeout: float, headless: bool) -> None:
        seen["url"] = url
        seen["refresh"] = refresh
        seen["idle"] = idle_timeout
        seen["headless"] = headless

    app = create_app(state, store, opener=opener)
    with TestClient(app) as client:
        bad = client.post("/api/open", json={"url": "ftp://files"})
        assert bad.status_code == 400
        ok = client.post(
            "/api/open",
            json={
                "url": "https://example.test/page",
                "refresh": True,
                "idle_timeout": 5,
                "headless": True,
            },
        )
        assert ok.status_code == 200
        assert ok.json()["headless"] is True
        assert state.status == "opening"
        assert state.page_url == "https://example.test/page"
        assert state.refresh is True
        assert state.headless is True
        assert client.get("/api/state").json()["headless"] is True
        again = client.post("/api/open", json={"url": "https://example.test/other"})
        assert again.status_code == 409
    assert seen["url"] == "https://example.test/page"
    assert seen["refresh"] is True
    assert seen["idle"] == 5
    assert seen["headless"] is True
    store.close()


def test_open_defaults_to_headed(tmp_path):
    """Không gửi headless thì phải mở cửa sổ thật (mặc định an toàn)."""
    state = AppState()
    state.attach()
    state.controls = True
    state.set_status("idle")
    store = Store(tmp_path / "crawler.db")
    seen = {}

    async def opener(url: str, refresh: bool, idle_timeout: float, headless: bool) -> None:
        seen["headless"] = headless

    app = create_app(state, store, opener=opener)
    with TestClient(app) as client:
        assert client.post("/api/open", json={"url": "https://example.test/page"}).status_code == 200
    assert seen["headless"] is False
    assert state.headless is False
    store.close()


def test_posts_api_returns_permalink(tmp_path):
    state = AppState()
    store = Store(tmp_path / "crawler.db")
    url = "https://www.facebook.com/groups/demo/permalink/1/"
    store.upsert(
        "http://example.test/page",
        Post(post_id="p1", author="A", text="bài", permalink=url),
    )
    app = create_app(state, store)
    with TestClient(app) as client:
        posts = client.get("/api/posts").json()
        assert posts["posts"][0]["permalink"] == url
        assert "crawled_comments" not in posts["posts"][0]
        assert client.get("/api/posts/p1/comments").status_code == 404
    store.close()
