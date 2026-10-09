import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

POSTS = [
    {
        "id": "post_1",
        "author": {"id": "page_1", "name": "Trang Demo"},
        "created_time": "2026-10-01T08:00:00Z",
        "time_label": "1 ngày",
        "like_count": 120,
        "comment_count": 14,
        "message": "Bài một có ảnh",
        "permalink": "/post/post_1",
        "attachments": [{"type": "photo", "url": "/media/a.jpg"}, {"type": "avatar", "url": "/media/avatar.png"}],
        "comments": {
            "count": 14,
            "data": [
                {
                    "id": "c1",
                    "type": "comment",
                    "message": "không phải bài viết",
                    "created_time": "2026-10-01T09:00:00Z",
                    "from": {"name": "Khách"},
                    "like_count": 1,
                }
            ],
        },
    },
    {
        "id": "post_2",
        "author": {"name": "Trang Demo"},
        "created_time": "2026-10-01T09:00:00Z",
        "time_label": "23 giờ",
        "like_count": 3,
        "comment_count": 0,
        "message": "Bài hai hai ảnh",
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
    },
    {
        "id": "post_3",
        "author": {"name": "Trang Demo"},
        "created_time": "2026-10-01T10:00:00Z",
        "time_label": "22 giờ",
        "likes": {"count": 8},
        "comments": {"count": 2},
        "message": "Bài ba không ảnh",
    },
    {
        "id": "post_4",
        "author": {"name": "Người khác"},
        "created_time": "2026-10-02T01:00:00Z",
        "time_label": "7 giờ",
        "like_count": 1,
        "comment_count": 1,
        "message": "Bài bốn\nxuống dòng",
        "image_urls": ["/media/a.jpg"],
    },
    {
        "id": "post_5",
        "author": {"name": "Trang Demo"},
        "created_time": "2026-10-02T02:00:00Z",
        "time_label": "6 giờ",
        "like_count": 5,
        "comment_count": 0,
        "message": "",
        "permalink": "/post/post_5",
    },
    {
        "id": "post_6",
        "author": {"name": "Trang Demo"},
        "created_time": "2026-10-02T03:00:00Z",
        "time_label": "5 giờ",
        "like_count": 9,
        "comment_count": 4,
        "message": "Bài sáu hết feed",
        "attachments": [{"type": "photo", "url": "/media/b.jpg"}],
    },
]


def feed_payload(cursor: str) -> dict:
    try:
        index = int(cursor or "0")
    except ValueError:
        index = 0
    chunk = POSTS[index : index + 2]
    nxt = index + 2
    has_next = nxt < len(POSTS)
    return {
        "data": {
            "feed": {
                "edges": [{"node": post} for post in chunk],
                "page_info": {
                    "end_cursor": str(nxt) if has_next else "",
                    "has_next_page": has_next,
                },
            }
        }
    }


LOGIN_HTML = """<!DOCTYPE html>
<html lang="vi">
<head><meta charset="utf-8"><title>Đăng nhập</title></head>
<body>
  <form method="post" action="/login">
    <label>Tài khoản <input name="username" value="demo"></label>
    <label>Mật khẩu <input name="password" type="password" value="demo"></label>
    <button type="submit">Đăng nhập</button>
  </form>
</body>
</html>
"""

FEED_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <title>Trang Demo</title>
  <style>
    body { margin: 0; font-family: sans-serif; }
    [data-feed] { height: 70vh; overflow: auto; }
    article { min-height: 45vh; border-bottom: 1px solid #ccc; padding: 12px; }
    [data-field=text] { white-space: pre-wrap; }
  </style>
</head>
<body>
  <div data-feed id="feed"></div>
  <script>
    let cursor = "";
    let loading = false;
    let done = false;
    const feed = document.querySelector("[data-feed]");
    function render(payload) {
      const edges = (((payload || {}).data || {}).feed || {}).edges || [];
      for (const edge of edges) {
        const post = edge.node;
        const article = document.createElement("article");
        article.setAttribute("data-post-id", post.id);
        const author = (post.author && post.author.name) || "";
        const text = post.message || "";
        const permalink = post.permalink || "";
        article.innerHTML =
          '<div data-field="author"></div>' +
          '<time></time><div data-field="time"></div>' +
          '<div data-field="likes"></div><div data-field="comments"></div>' +
          '<div data-field="text"></div>';
        article.querySelector("[data-field=author]").textContent = author;
        article.querySelector("time").setAttribute("datetime", post.created_time || "");
        article.querySelector("[data-field=time]").textContent = post.time_label || "";
        article.querySelector("[data-field=likes]").textContent = String(post.like_count || (post.likes && post.likes.count) || 0);
        article.querySelector("[data-field=comments]").textContent = String(post.comment_count || (post.comments && post.comments.count) || 0);
        article.querySelector("[data-field=text]").textContent = text;
        if (permalink) {
          const link = document.createElement("a");
          link.href = permalink;
          link.setAttribute("data-permalink", "");
          link.textContent = "link";
          article.appendChild(link);
        }
        const urls = [];
        for (const item of post.attachments || []) {
          if (item.url) urls.push(item.url);
          const subs = (item.subattachments || {}).data || [];
          for (const sub of subs) if (sub.url) urls.push(sub.url);
        }
        for (const url of post.image_urls || []) urls.push(url);
        for (const url of urls) {
          const img = document.createElement("img");
          img.setAttribute("data-photo", "");
          img.src = url;
          article.appendChild(img);
        }
        feed.appendChild(article);
      }
    }
    async function load() {
      if (loading || done) return;
      loading = true;
      const response = await fetch("/api/feed?cursor=" + encodeURIComponent(cursor));
      const payload = await response.json();
      render(payload);
      const info = payload.data.feed.page_info;
      cursor = info.end_cursor || "";
      done = !info.has_next_page;
      loading = false;
    }
    const sentinel = document.createElement("div");
    sentinel.setAttribute("data-sentinel", "");
    feed.appendChild(sentinel);
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) load();
    }, { root: feed, rootMargin: "80px" });
    observer.observe(sentinel);
    feed.addEventListener("scroll", () => {
      if (feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 80) load();
    });
    load();
  </script>
</body>
</html>
"""

POST_HTML = """<!DOCTYPE html>
<html lang="vi">
<head><meta charset="utf-8"><title>Bài 5</title></head>
<body>
  <article data-post-id="post_5">
    <div data-field="text">Nội dung chỉ có trên permalink</div>
  </article>
</body>
</html>
"""


def _logged_in(handler: BaseHTTPRequestHandler) -> bool:
    cookie = handler.headers.get("Cookie") or ""
    return "session=ok" in cookie


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str, extra: list[tuple[str, str]] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/page/demo"}:
            if not _logged_in(self):
                self._send(200, LOGIN_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            self._send(200, FEED_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/feed":
            if not _logged_in(self):
                self._send(401, b'{"error":"auth"}', "application/json")
                return
            cursor = parse_qs(parsed.query).get("cursor", [""])[0]
            body = json.dumps(feed_payload(cursor), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/post/post_5":
            self._send(200, POST_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path.startswith("/media/"):
            self._send(200, PNG, "image/png")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/login":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or "0")
        if length:
            self.rfile.read(length)
        self.send_response(302)
        self.send_header("Set-Cookie", "session=ok; Path=/")
        self.send_header("Location", "/page/demo")
        self.end_headers()


def serve_mock(port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
