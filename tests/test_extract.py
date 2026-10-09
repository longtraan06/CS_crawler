from pagecrawl.extract import cursor_has_next, extract_posts
from pagecrawl.models import Post, content_hash


FEED = {
    "data": {
        "feed": {
            "edges": [
                {
                    "node": {
                        "id": "post_1",
                        "author": {"id": "u1", "name": "Trang A"},
                        "created_time": "2026-10-02T08:15:00Z",
                        "time_label": "2 giờ",
                        "like_count": 120,
                        "comment_count": 14,
                        "message": "xin chào",
                        "attachments": [
                            {"type": "photo", "url": "https://cdn.example/a.jpg"},
                            {"type": "avatar", "url": "https://cdn.example/avatar.png"},
                        ],
                        "comments": {
                            "count": 14,
                            "data": [
                                {
                                    "id": "c1",
                                    "type": "comment",
                                    "message": "hay quá",
                                    "created_time": "2026-10-02T09:00:00Z",
                                    "from": {"name": "B"},
                                    "like_count": 1,
                                }
                            ],
                        },
                    }
                }
            ],
            "page_info": {"end_cursor": "CUR", "has_next_page": True},
        }
    }
}


def test_graphql_feed_skips_comments_and_avatars():
    posts = extract_posts(FEED, base_url="https://cdn.example/api")
    assert len(posts) == 1
    post = posts[0]
    assert post.post_id == "post_1"
    assert post.author == "Trang A"
    assert post.created_at == "2026-10-02T08:15:00Z"
    assert post.time_label == "2 giờ"
    assert post.like_count == 120
    assert post.comment_count == 14
    assert post.text == "xin chào"
    assert post.image_urls == ["https://cdn.example/a.jpg"]
    assert cursor_has_next(FEED) is True


def test_cursor_empty_when_feed_ends():
    payload = {"posts": [], "next_cursor": "", "has_next_page": False}
    assert cursor_has_next(payload) is False


def test_rest_shape_and_nested_counts():
    payload = {
        "posts": [
            {
                "post_id": "p9",
                "from": {"name": "Nam"},
                "timestamp": 1760000000,
                "likes": {"summary": {"total_count": 4}},
                "comments": {"count": 2},
                "content": "nội dung",
                "images": [
                    {"url": "https://cdn.example/small.jpg", "width": 100},
                    {"url": "https://cdn.example/big.jpg", "width": 900},
                ],
            }
        ],
        "next_cursor": "n",
    }
    posts = extract_posts(payload)
    assert len(posts) == 1
    post = posts[0]
    assert post.author == "Nam"
    assert post.created_at == "2025-10-09T08:53:20Z"
    assert post.like_count == 4
    assert post.comment_count == 2
    assert post.image_urls == ["https://cdn.example/big.jpg"]


def test_unstable_id_and_hash_changes_with_like():
    payload = {
        "author": "Trang",
        "created_time": "2026-01-01T00:00:00Z",
        "permalink": "https://site.example/posts/1",
        "message": "abc",
        "like_count": 1,
        "comment_count": 0,
    }
    post = extract_posts(payload)[0]
    assert post.id_unstable is True
    changed = post.model_copy(update={"like_count": 2})
    assert content_hash(post) != content_hash(changed)


def test_missing_counts_are_zero():
    posts = extract_posts({"id": "x", "message": "chỉ chữ", "author": "A"})
    assert posts[0].like_count == 0
    assert posts[0].comment_count == 0
    assert posts[0].image_urls == []


def test_album_subattachments():
    payload = {
        "id": "alb",
        "author": {"name": "P"},
        "message": "album",
        "like_count": 1,
        "comment_count": 0,
        "attachments": [
            {
                "type": "album",
                "subattachments": {
                    "data": [
                        {"type": "photo", "url": "/media/b.jpg"},
                        {"type": "photo", "url": "/media/c.jpg"},
                    ]
                },
            }
        ],
    }
    post = extract_posts(payload, base_url="http://127.0.0.1:9/api/feed")[0]
    assert post.image_urls == ["http://127.0.0.1:9/media/b.jpg", "http://127.0.0.1:9/media/c.jpg"]


def test_hash_stable():
    post = Post(post_id="1", text="a", like_count=1, comment_count=2, image_urls=["b", "a"])
    again = Post(post_id="1", text="a", like_count=1, comment_count=2, image_urls=["a", "b"])
    assert content_hash(post) == content_hash(again)


def test_fb_story_permalink_from_payload():
    payload = {
        "data": {
            "node": {
                "__typename": "Story",
                "post_id": "Uzpf123",
                "creation_time": 1760000000,
                "actors": [{"name": "Page"}],
                "message": {"text": "bài có link sẵn", "ranges": []},
                "permalink_url": "https://www.facebook.com/permalink.php?story_fbid=1&id=2",
            }
        }
    }
    post = extract_posts(payload)[0]
    assert post.permalink == "https://www.facebook.com/permalink.php?story_fbid=1&id=2"


def test_fb_story_permalink_built_for_group_when_missing():
    payload = {
        "data": {
            "node": {
                "__typename": "Story",
                "post_id": "1137195462176515",
                "creation_time": 1760000000,
                "actors": [{"name": "Page"}],
                "message": {"text": "không có link trong payload", "ranges": []},
            }
        }
    }
    post = extract_posts(payload, base_url="https://www.facebook.com/groups/congdongsinhvien.hcmuit")[0]
    assert post.permalink == (
        "https://www.facebook.com/groups/congdongsinhvien.hcmuit/permalink/1137195462176515/"
    )


def test_fb_story_permalink_built_for_page_when_missing():
    payload = {
        "data": {
            "node": {
                "__typename": "Story",
                "post_id": "123456",
                "creation_time": 1760000000,
                "actors": [{"name": "UIT.Fanpage"}],
                "message": {"text": "bài của page", "ranges": []},
            }
        }
    }
    post = extract_posts(payload, base_url="https://www.facebook.com/UIT.Fanpage")[0]
    assert post.permalink == "https://www.facebook.com/UIT.Fanpage/posts/123456"


def test_post_link_from_various_keys():
    payload = {
        "data": {
            "story": {
                "post_id": "p1",
                "author": {"name": "A"},
                "created_time": "2026-10-02T08:15:00Z",
                "time_label": "2 giờ",
                "like_count": 1,
                "comment_count": 0,
                "message": "nội dung",
                "share_url": "/groups/demo/permalink/1/",
            }
        }
    }
    post = extract_posts(payload, base_url="https://www.facebook.com")[0]
    assert post.permalink == "https://www.facebook.com/groups/demo/permalink/1/"
