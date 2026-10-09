from fastapi.testclient import TestClient

from pagecrawl.models import Post
from pagecrawl.monitor import create_app
from pagecrawl.state import AppState
from pagecrawl.store import Store


def _post(post_id: str) -> Post:
    return Post(post_id=post_id, author="Trang", text="noi dung")


def test_store_collections(tmp_path):
    store = Store(tmp_path / "crawler.db")
    try:
        default = store.list_collections()
        assert [c["name"] for c in default] == ["mặc định"]

        created = store.create_collection("UIT")
        again = store.create_collection("UIT")
        assert again["id"] == created["id"]

        store.upsert("http://example.test/page", _post("a"))
        store.upsert("http://example.test/page", _post("b"), collection_id=created["id"])

        listed = {c["name"]: c["post_count"] for c in store.list_collections()}
        assert listed == {"mặc định": 1, "UIT": 1}

        only_uit = store.list_posts(collection_id=created["id"])
        assert only_uit["total"] == 1
        assert only_uit["posts"][0]["post_id"] == "b"
        assert only_uit["posts"][0]["collection_id"] == created["id"]

        everything = store.list_posts()
        assert everything["total"] == 2
    finally:
        store.close()


def test_existing_db_migrates(tmp_path):
    db = tmp_path / "crawler.db"
    store = Store(db)
    store.upsert("http://example.test/page", _post("old"))
    store.close()

    reopened = Store(db)
    try:
        post = reopened.get_post("old")
        assert post["collection_id"] == 1
    finally:
        reopened.close()


def test_monitor_collection_endpoints(tmp_path):
    state = AppState()
    store = Store(tmp_path / "crawler.db")
    store.upsert("http://example.test/page", _post("a"))
    uit = store.create_collection("UIT")
    store.upsert("http://example.test/page", _post("b"), collection_id=uit["id"])
    app = create_app(state, store)

    with TestClient(app) as client:
        listed = client.get("/api/collections").json()
        assert listed["current"] == 1
        assert {c["name"] for c in listed["collections"]} == {"mặc định", "UIT"}

        bad = client.post("/api/collections", json={"name": "  "})
        assert bad.status_code == 400

        created = client.post("/api/collections", json={"name": "Page khac"})
        assert created.status_code == 200

        state.status = "idle"
        missing = client.post("/api/select", json={"collection_id": 999})
        assert missing.status_code == 404

        chosen = client.post("/api/select", json={"collection_id": uit["id"]})
        assert chosen.status_code == 200
        assert state.collection_id == uit["id"]
        assert state.collection_name == "UIT"

        posts = client.get("/api/posts").json()
        assert posts["total"] == 1
        assert posts["posts"][0]["post_id"] == "b"

        state.status = "running"
        busy = client.post("/api/select", json={"collection_id": 1})
        assert busy.status_code == 409
        state.status = "idle"

        explicit = client.get(f"/api/posts?collection_id={uit['id']}").json()
        assert explicit["total"] == 1
    store.close()
