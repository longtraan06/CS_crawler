"""Test phần lấy bình luận: model, store, trạng thái, resume và API."""
from pathlib import Path

from fastapi.testclient import TestClient

from pagecrawl.models import Comment, Post
from pagecrawl.monitor import create_app
from pagecrawl.state import AppState
from pagecrawl.store import Store


def _post(post_id="p1", comments=5):
    return Post(
        post_id=post_id,
        author="A",
        text="bài",
        comment_count=comments,
        permalink=f"https://www.facebook.com/x/posts/{post_id}/",
    )


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "crawler.db")


def test_filter_by_min_comments_and_pending(tmp_path):
    """Chỉ lấy bài có đủ ngưỡng bình luận, có link, và chưa lấy."""
    store = _store(tmp_path)
    store.upsert("http://x/p", _post("a", 9), collection_id=1)
    store.upsert("http://x/p", _post("b", 2), collection_id=1)
    store.upsert("http://x/p", _post("c", 1), collection_id=1)
    no_link = _post("d", 7)
    no_link.permalink = None
    store.upsert("http://x/p", no_link, collection_id=1)

    todo = store.list_posts_needing_comments(1, min_comments=2)
    assert {r["post_id"] for r in todo} == {"a", "b"}
    store.close()


def test_done_posts_are_skipped_next_run(tmp_path):
    """Bài đã lấy xong không bị lặp lại khi chạy tiếp (resume)."""
    store = _store(tmp_path)
    store.upsert("http://x/p", _post("a", 9), collection_id=1)
    store.upsert("http://x/p", _post("b", 4), collection_id=1)

    first = store.list_posts_needing_comments(1, 2)
    assert {r["post_id"] for r in first} == {"a", "b"}
    # bài mới hơn được xử lý trước
    assert first[0]["post_id"] == "b"
    store.save_comments(1, "b", [Comment(comment_id="c1", text="a")])

    todo = store.list_posts_needing_comments(1, 2)
    assert [r["post_id"] for r in todo] == ["a"]
    progress = store.comment_progress(1, 2)
    assert progress["done"] == 1
    assert progress["pending"] == 1
    assert progress["saved"] == 1
    store.close()


def test_save_twice_does_not_duplicate(tmp_path):
    store = _store(tmp_path)
    store.upsert("http://x/p", _post("a", 9), collection_id=1)
    items = [Comment(comment_id="c1", author="X", text="một")]
    assert store.save_comments(1, "a", items) == 1
    assert store.save_comments(1, "a", items) == 1
    assert store.list_comments(1, "a")["total"] == 1
    store.close()


def test_comment_updates_keep_single_row(tmp_path):
    store = _store(tmp_path)
    store.upsert("http://x/p", _post("a", 9), collection_id=1)
    store.save_comments(1, "a", [Comment(comment_id="c1", text="bản cũ")])
    store.save_comments(1, "a", [Comment(comment_id="c1", text="bản mới")])
    rows = store.list_comments(1, "a")["comments"]
    assert len(rows) == 1
    assert rows[0]["text"] == "bản mới"
    store.close()


def test_error_status_retried_and_cleared(tmp_path):
    store = _store(tmp_path)
    store.upsert("http://x/p", _post("a", 9), collection_id=1)
    store.mark_comments_error(1, "a", "không mở được")
    assert store.comment_progress(1, 2)["error"] == 1
    # lỗi vẫn được thử lại
    assert [r["post_id"] for r in store.list_posts_needing_comments(1, 2)] == ["a"]
    store.save_comments(1, "a", [Comment(comment_id="c1", text="ok")])
    assert store.comment_progress(1, 2)["error"] == 0
    store.close()


def test_reset_clears_comments_and_status(tmp_path):
    store = _store(tmp_path)
    store.upsert("http://x/p", _post("a", 9), collection_id=1)
    store.save_comments(1, "a", [Comment(comment_id="c1", text="x")])
    assert store.reset_comments(1, "a") == 1
    assert store.list_comments(1, "a")["total"] == 0
    assert store.comment_progress(1, 2)["done"] == 0
    assert [r["post_id"] for r in store.list_posts_needing_comments(1, 2)] == ["a"]
    store.close()


def test_comments_scoped_per_collection(tmp_path):
    store = _store(tmp_path)
    store.create_collection("tin-tuc")
    store.upsert("http://x/p", _post("a", 9), collection_id=1)
    store.upsert("http://x/p", _post("a", 9), collection_id=2)
    store.save_comments(2, "a", [Comment(comment_id="c1", text="tin tuc")])
    assert store.list_comments(1, "a")["total"] == 0
    assert store.list_comments(2, "a")["total"] == 1
    store.close()


def test_deleting_collection_removes_its_comments(tmp_path):
    store = _store(tmp_path)
    col = store.create_collection("xoá")
    store.upsert("http://x/p", _post("a", 9), collection_id=int(col["id"]))
    store.save_comments(int(col["id"]), "a", [Comment(comment_id="c1", text="x")])
    store.delete_collection(int(col["id"]))
    assert store.list_comments(int(col["id"]), "a")["total"] == 0
    store.close()


def test_comment_parent_and_depth_survive(tmp_path):
    store = _store(tmp_path)
    store.upsert("http://x/p", _post("a", 9), collection_id=1)
    store.save_comments(1, "a", [
        Comment(comment_id="c1", author="X", text="gốc"),
        Comment(comment_id="c2", parent_id="c1", depth=1, author="Y", text="trả lời"),
        Comment(comment_id="c3", parent_id="c2", depth=2, author="Z", text="trả lời lại"),
    ])
    rows = {c["comment_id"]: c for c in store.list_comments(1, "a")["comments"]}
    assert rows["c1"]["depth"] == 0 and rows["c1"]["parent_id"] is None
    assert rows["c2"]["parent_id"] == "c1" and rows["c2"]["depth"] == 1
    assert rows["c3"]["parent_id"] == "c2" and rows["c3"]["depth"] == 2
    store.close()


def test_comments_api(tmp_path):
    state = AppState()
    state.attach()
    state.controls = True
    state.set_status("idle")
    store = _store(tmp_path)
    store.upsert("http://x/p", _post("a", 5), collection_id=1)
    app = create_app(state, store)
    with TestClient(app) as client:
        progress = client.get("/api/comments/progress").json()
        assert progress["pending"] == 1

        store.save_comments(1, "a", [
            Comment(comment_id="c1", author="X", text="gốc"),
            Comment(comment_id="c2", parent_id="c1", depth=1, author="Y", text="đáp"),
        ])
        data = client.get("/api/comments?post_id=a").json()
        assert data["total"] == 2
        assert data["post"]["comments_saved"] == 2
        assert data["post"]["comments_status"] == "done"
        assert [c["depth"] for c in data["comments"]] == [0, 1]

        posts = client.get("/api/posts").json()["posts"][0]
        assert posts["comments_status"] == "done"
        assert posts["comments_saved"] == 2

        assert client.post("/api/comments/reset", json={"post_id": "a"}).json()["reset"] == 1
        assert client.get("/api/comments/progress").json()["pending"] == 1
    store.close()


def test_comments_start_requires_ui(tmp_path):
    """Không chạy ui thì bấm lấy bình luận phải báo lỗi rõ ràng."""
    state = AppState()
    state.attach()
    state.set_status("idle")
    store = _store(tmp_path)
    app = create_app(state, store)
    with TestClient(app) as client:
        response = client.post("/api/comments/start", json={"min_comments": 2})
        assert response.status_code == 404
    store.close()