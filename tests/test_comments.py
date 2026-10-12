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


# ------------------------------------------------- quy đổi DOM -> Comment


def _row(cid: str, left: int, order: int, text: str = "nội dung", hidden: bool = False):
    return {
        "comment_id": cid,
        "author": "A",
        "text": text,
        "created_at": "Thứ Hai, 10 Tháng 8, 2026 lúc 09:57",
        "like_count": 0,
        "hidden": hidden,
        "left": left,
        "order": order,
    }


def _crawler():
    from pagecrawl.comments import CommentCrawler

    return CommentCrawler(source=None)


def test_indent_decides_reply_levels():
    """Bình luận gốc sát mép (left=0), trả lời thụt vào (left=54, 108...)."""
    crawler = _crawler()
    rows = [
        _row("c1", 0, 0),
        _row("c2", 54, 1),
        _row("c3", 54, 2),
        _row("c4", 0, 3),
        _row("c5", 54, 4),
    ]
    out = {c.comment_id: c for c in crawler._to_comments(rows)}
    assert out["c1"].depth == 0 and out["c1"].parent_id is None
    assert out["c2"].depth == 1 and out["c2"].parent_id == "c1"
    assert out["c3"].depth == 1 and out["c3"].parent_id == "c1"
    assert out["c4"].depth == 0 and out["c4"].parent_id is None
    assert out["c5"].depth == 1 and out["c5"].parent_id == "c4"


def test_nested_reply_gets_depth_two():
    """Trả lời của một trả lời thụt sâu hơn nữa."""
    crawler = _crawler()
    rows = [_row("c1", 0, 0), _row("c2", 54, 1), _row("c3", 108, 2)]
    out = {c.comment_id: c for c in crawler._to_comments(rows)}
    assert out["c3"].depth == 2
    assert out["c3"].parent_id == "c2"


def test_row_order_follows_dom_not_insertion():
    crawler = _crawler()
    rows = [_row("c1", 0, 2), _row("c2", 0, 0), _row("c3", 54, 1)]
    out = crawler._to_comments(rows)
    assert [c.comment_id for c in out] == ["c2", "c3", "c1"]


def test_hidden_comments_are_skipped_but_blank_kept():
    """Bình luận bị ẩn thì bỏ; bình luận trống thì giữ lại (không mất dữ liệu)."""
    crawler = _crawler()
    rows = [
        _row("c1", 0, 0),
        _row("c2", 0, 1, text=""),
        _row("c3", 0, 2, hidden=True),
    ]
    out = {c.comment_id: c for c in crawler._to_comments(rows)}
    assert "c1" in out
    assert "c2" in out and out["c2"].text == ""
    assert "c3" not in out


def test_leading_dot_is_stripped_from_text():
    """Bài nhóm hay để lại dấu '·' ở đầu nội dung."""
    crawler = _crawler()
    out = crawler._to_comments([_row("c1", 0, 0, text="· Xin chào các bạn")])
    assert out[0].text == "Xin chào các bạn"


def test_duplicated_comment_ids_kept_once():
    crawler = _crawler()
    rows = [_row("c1", 0, 0), _row("c1", 0, 1)]
    assert len(crawler._to_comments(rows)) == 1


def test_both_layouts_supported():
    """Hai kiểu trang: có modal và bài ảnh (bình luận nằm trong trang)."""
    from pagecrawl import comments

    assert comments.MODAL == '[role="dialog"][aria-modal="true"]'
    assert comments.SCOPE == '[data-pc-scope="1"]'
    # _SCOPE_JS phải xử lý được cả hai: ưu tiên modal, không có thì dùng trang
    assert 'aria-modal' in comments._SCOPE_JS
    assert 'data-pc-scope' in comments._SCOPE_JS
    # _BOTTOM_JS phải dò cả phần tử cha (bài ảnh nằm trong cột cuộn chung)
    assert 'parentElement' in comments._BOTTOM_JS


# ------------------------------------------------------ chống chậm / an toàn


def test_gap_is_always_at_least_two_seconds():
    """Luôn phải có nhịp nghỉ giữa các bài — không được về 0 vì tốc độ."""
    from pagecrawl import comments

    for name in ("GAP_OK", "GAP_SUSPECT", "GAP_BAD"):
        low, high = getattr(comments, name)
        assert low >= 2.0, f"{name} nghỉ quá ngắn"
        assert high > low


def test_extraction_runs_off_the_event_loop():
    """Rút bài từ response phải chạy ở thread, không chặn event loop."""
    import inspect

    from pagecrawl import browser

    src = inspect.getsource(browser.PlaywrightSource._read_response_inner)
    assert "to_thread" in src


def test_response_backpressure_bounded():
    """Không để dồn task vô hạn khi cuộn nhanh."""
    import asyncio
    import tempfile
    from pathlib import Path

    from pagecrawl.browser import PlaywrightSource

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = PlaywrightSource(Path(tmp) / "p", headless=True)
            assert source._pending == 0
            seen: list = []

            async def fake(resp: object) -> None:
                seen.append(resp)

            source._read_response = fake  # type: ignore[assignment]
            source._pending = 50
            source._on_response("x")  # vượt ngưỡng thì bỏ qua
            await asyncio.sleep(0)
            assert seen == []
            source._pending = 0
            source._on_response("y")
            await asyncio.sleep(0)
            assert seen == ["y"]

    asyncio.run(run())


def test_no_hidden_post_limit(tmp_path):
    """Không được lặng lẽ cắt ở 500 bài — mặc định phải lấy hết collection."""
    store = _store(tmp_path)
    for i in range(620):
        store.upsert("http://x/p", _post(f"p{i}", 3), collection_id=1)
    assert len(store.list_posts_needing_comments(1, 2)) == 620
    assert len(store.list_posts_needing_comments(1, 2, limit=None)) == 620
    assert len(store.list_posts_needing_comments(1, 2, limit=0)) == 620
    assert len(store.list_posts_needing_comments(1, 2, limit=25)) == 25
    store.close()


def test_progress_counts_all_posts_not_limited(tmp_path):
    """comment_progress phải đếm đủ, không bị giới hạn theo batch."""
    store = _store(tmp_path)
    for i in range(120):
        store.upsert("http://x/p", _post(f"p{i}", 3), collection_id=1)
    assert store.comment_progress(1, 2)["pending"] == 120
    store.close()


def test_retries_parameter_is_honoured():
    """Số lần thử lại phải do người dùng chọn được."""
    import inspect

    from pagecrawl.comments import crawl_comments

    params = inspect.signature(crawl_comments).parameters
    assert "retries" in params
    assert params["retries"].default >= 0