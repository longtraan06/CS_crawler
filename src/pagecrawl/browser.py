import asyncio
import random
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright_stealth import Stealth

from pagecrawl.extract import (
    acceptable_image,
    cursor_has_next,
    extract_posts,
    merge_posts,
    parse_count,
    parse_json_payloads,
)
from pagecrawl.models import Post

# Chỉ mở nội dung bài bị cắt ("Xem thêm"). Không đụng tới bình luận: nút bình luận
# ở feed là link tới /permalink/<id> nên bấm sẽ làm Facebook tải lại trang.
_EXPAND_JS = r"""() => {
  const text = (node) => ((node.innerText || node.textContent || "") + "").replace(/\s+/g, " ").trim();
  const COMPOSER = /(viết|write|gửi|post|đăng)\s*(một\s*)?bình luận|bình luận bằng (file|ảnh|nhãn dán|biểu tượng|gif|sticker)|^(bình luận|comment)$/i;
  const safe = [...document.querySelectorAll('[role="button"], button')].filter((node) => {
    if (node.offsetParent === null) return false;
    if (node.tagName === "A") {
      const href = node.getAttribute("href") || "";
      if (href && href !== "#" && !href.startsWith("javascript:")) return false;
    }
    const t = text(node);
    return t && t.length < 60 && !COMPOSER.test(t);
  });
  const more = safe.filter((node) => /^(xem thêm|see more)$/i.test(text(node)));
  for (const node of more.sort(() => Math.random() - 0.5).slice(0, 4)) node.click();
}"""

_STEALTH = Stealth(
    navigator_languages=False,
    navigator_languages_override=None,
    navigator_platform=False,
    navigator_platform_override=None,
    webgl_vendor=False,
)

# Headless mặc định của Playwright là chromium_headless_shell: UA chứa
# "HeadlessChrome", không có window.chrome, không có plugin -> Facebook đánh dấu
# ngay. channel="chromium" ép chạy đúng binary như bản headed, chỉ đổi chế độ
# render (new headless) nên fingerprint gần như giống hệt.
_LOCALE = "vi-VN"
_TIMEZONE = "Asia/Ho_Chi_Minh"
_ARGS = [
    "--disable-blink-features=AutomationControlled",
    f"--lang={_LOCALE}",
]


class PlaywrightSource:
    def __init__(self, profile: Path, headless: bool) -> None:
        self.profile = Path(profile)
        self.headless = headless
        self.queue: asyncio.Queue = asyncio.Queue()
        self._signal = asyncio.Event()
        self._playwright = None
        self.context = None
        self.page = None
        self.blocked = False
        self.feed_url = ""
        self.recovered_navigations = 0
        self.needs_login = False
        self._pending = 0

    async def start(self, url: str) -> None:
        from playwright.async_api import async_playwright

        self.profile.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self.context = await self._playwright.chromium.launch_persistent_context(
            str(self.profile),
            channel="chromium",
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            locale=_LOCALE,
            timezone_id=_TIMEZONE,
            args=_ARGS,
        )
        await _STEALTH.apply_stealth_async(self.context)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.page.on("response", self._on_response)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self.feed_url = self.page.url

    def _is_feed_page(self) -> bool:
        """Trang hiện tại có còn đúng trang feed đã mở không (bỏ qua query/hash)."""
        if self.page is None or not self.feed_url:
            return True
        try:
            here = _page_key(self.page.url)
            want = _page_key(self.feed_url)
        except Exception:
            return True
        return here == want

    async def ensure_feed(self) -> None:
        """Bấm nhầm làm Facebook điều hướng sang trang khác -> quay lại trang feed."""
        if self.page is None or not self.feed_url:
            return
        if self._is_feed_page() and not self._login_visible():
            self.needs_login = False
            return
        if self._login_visible():
            # Facebook đưa ra trang đăng nhập: dừng lại chờ người dùng đăng nhập
            # thay vì cứ quay lại feed (tránh lặp vô hạn).
            self.needs_login = True
            return
        try:
            self.recovered_navigations += 1
            if not self.page.url.startswith("about:blank"):
                try:
                    await self.page.go_back(wait_until="domcontentloaded", timeout=20000)
                    await self.page.wait_for_timeout(1500)
                    if self._is_feed_page() and not self._login_visible():
                        return
                except Exception:
                    pass
            await self.page.goto(self.feed_url, wait_until="domcontentloaded", timeout=45000)
            await self.page.wait_for_timeout(2500)
            if self._login_visible():
                self.needs_login = True
        except Exception:
            return

    def _login_visible(self) -> bool:
        if self.page is None:
            return False
        try:
            url = self.page.url or ""
        except Exception:
            return False
        if "/login" in url or "/checkpoint" in url:
            return True
        return False

    def _on_response(self, response: object) -> None:
        # Tránh dồn quá nhiều tác vụ khi cuộn nhanh: response chưa xử lý xong thì
        # bỏ qua response mới thay vì xếp hàng. Dữ liệu feed vẫn được lấy đủ vì
        # mỗi lần cuộn đều trả về một đợt bài mới.
        if self._pending >= 24:
            return
        self._pending += 1
        asyncio.create_task(self._read_response(response))

    async def _read_response(self, response: object) -> None:
        try:
            await self._read_response_inner(response)
        finally:
            self._pending -= 1

    async def _read_response_inner(self, response: object) -> None:
        try:
            if response.status == 429 or "/checkpoint/" in response.url:
                self.blocked = True
                return
            if response.status != 200:
                return
            if response.request.resource_type not in {"xhr", "fetch"}:
                return
            ctype = (response.headers.get("content-type") or "").lower()
            if not _maybe_json(ctype, response.url):
                return
            body = await response.body()
        except Exception:
            return
        # Giải nén và rút bài ra khỏi event loop: đây là phần tốn CPU, chạy thẳng
        # sẽ chặn cuộn trang và làm chậm cả trình duyệt. Chuyển sang thread không
        # đổi gì về phía Facebook — vẫn đúng số request, đúng thứ tự.
        try:
            posts, flag = await asyncio.to_thread(_extract_docs, body, response.url)
        except Exception:
            return
        if not posts and flag is None:
            return
        await self.queue.put((merge_posts(posts), flag))
        self._signal.set()

    async def session_hint(self) -> bool:
        if self.page is None:
            return False
        try:
            url = self.page.url
            selectors = [
                "[data-feed]",
                "[data-post-id]",
                "[role=feed]",
                "div[data-pagelet='GroupFeed']",
                "div[aria-label*='Feed']",
                "div[aria-label*='Bài viết']",
                "div[aria-label*='Posts']",
                "div[role=main] [role=article]",
            ]
            if "/groups/" in url:
                selectors = [
                    "[role=feed]",
                    "div[data-pagelet='GroupFeed']",
                    "div[aria-label*='Feed']",
                    "div[aria-label*='Bài viết']",
                    "div[aria-label*='Posts']",
                    "[data-feed]",
                    "[data-post-id]",
                    "div[role=main] [role=article]",
                    "[role=article]",
                ]
            selector_parts = []
            for s in selectors:
                safe = s.replace("\\", "\\\\").replace('"', '\\"')
                selector_parts.append(f'document.querySelector("{safe}")')
            selector = " || ".join(selector_parts)
            return bool(
                await self.page.evaluate(
                    """(sel) => {
                      const pw = document.querySelector("input[type=password]");
                      const loginVisible = !!(pw && pw.offsetParent !== null);
                      const feed = eval(sel);
                      return !loginVisible && !!feed;
                    }""",
                    selector,
                )
            )
        except Exception:
            return False

    async def prepare_round(self, timeout: float) -> bool:
        await self.ensure_feed()
        if self.page is not None and "/checkpoint/" in self.page.url:
            self.blocked = True
            return False
        if not self.queue.empty():
            return True
        self._signal.clear()
        if not self.queue.empty():
            return True
        try:
            await asyncio.wait_for(self._signal.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def expand(self) -> None:
        """Mở nội dung bài bị cắt ("Xem thêm").

        Không đụng tới bình luận: nút bình luận trong feed là link tới
        /permalink/<id>, bấm vào sẽ làm Facebook tải lại trang.
        """
        if self.page is None:
            return
        await self.ensure_feed()
        try:
            await self.page.evaluate(_EXPAND_JS)
        except Exception:
            return
        if not self._is_feed_page():
            await self.page.wait_for_timeout(1500)
            await self.ensure_feed()

    async def collect_batch(self) -> tuple[list[Post], bool | None]:
        await self.ensure_feed()
        posts: list[Post] = []
        flag: bool | None = None
        while not self.queue.empty():
            batch_posts, has_next = self.queue.get_nowait()
            posts.extend(batch_posts)
            if has_next is not None:
                flag = has_next
        posts.extend(await self._dom_posts())
        return merge_posts(posts), flag

    async def scroll(self) -> None:
        if self.page is None:
            return
        try:
            metrics = await self.page.evaluate(
                """() => {
                  const feed = document.querySelector("[data-feed]");
                  if (feed && feed.scrollHeight > feed.clientHeight + 10) {
                    return {feed: true, y: feed.scrollTop, h: feed.scrollHeight, vh: feed.clientHeight};
                  }
                  return {feed: false, y: window.scrollY, h: document.documentElement.scrollHeight, vh: window.innerHeight};
                }"""
            )
        except Exception:
            return
        remaining = metrics["h"] - metrics["y"] - metrics["vh"]
        if remaining <= 10:
            return
        if metrics["feed"]:
            delta = max(metrics["vh"] * 0.9, 400)
            await self._eval_scroll(metrics["feed"], metrics["y"] + delta)
            return
        steps = random.randint(2, 4)
        traveled = 0.0
        for index in range(steps):
            chunk = (remaining - traveled) / (steps - index)
            traveled += chunk * random.uniform(0.75, 1.0)
            await self._eval_scroll(False, metrics["y"] + min(traveled, remaining))
            await asyncio.sleep(random.uniform(0.35, 0.9))
            # Đã có dữ liệu về thì dừng cuộn, không ngủ thêm — cũng giống cách
            # người dùng thật dừng lại khi bài đã hiện đủ. Tốc độ tải về phía
            # Facebook không đổi, chỉ bớt thời gian ngồi không.
            if not self.queue.empty():
                return
        if random.random() < 0.08:
            await asyncio.sleep(random.uniform(3.0, 7.0))

    async def _eval_scroll(self, is_feed: bool, top: float) -> None:
        if self.page is None:
            return
        try:
            if is_feed:
                await self.page.evaluate(
                    "(top) => { const feed = document.querySelector('[data-feed]'); if (feed) feed.scrollTo(0, top); }",
                    top,
                )
            else:
                await self.page.evaluate("(top) => window.scrollTo({top, behavior: 'smooth'})", top)
        except Exception:
            return

    async def fetch_text(self, url: str) -> str:
        if self.context is None:
            return ""
        page = await self.context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            text = await page.evaluate(
                """() => {
                  const node = document.querySelector("[data-field=text], article");
                  return node ? node.innerText : "";
                }"""
            )
            return text or ""
        except Exception:
            return ""
        finally:
            await page.close()

    async def close(self) -> None:
        try:
            if self.context is not None:
                await self.context.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass

    async def _dom_posts(self) -> list[Post]:
        if self.page is None:
            return []
        try:
            await self.page.evaluate(
                """() => {
                  for (const img of document.querySelectorAll("[data-post-id] img[data-photo]")) {
                    img.scrollIntoView({block: "center"});
                  }
                }"""
            )
            raw = await self.page.evaluate(
                """() => {
                  const best = (img) => {
                    const srcset = img.getAttribute("srcset");
                    if (srcset) {
                      let bestUrl = "";
                      let bestW = -1;
                      for (const part of srcset.split(",")) {
                        const bits = part.trim().split(/\\s+/);
                        if (!bits[0]) continue;
                        const width = bits[1] && bits[1].endsWith("w") ? parseInt(bits[1], 10) : 0;
                        if (width >= bestW) {
                          bestW = width;
                          bestUrl = bits[0];
                        }
                      }
                      if (bestUrl) return bestUrl;
                    }
                    return img.currentSrc || img.src || img.getAttribute("data-src") || img.getAttribute("src") || "";
                  };
                  return [...document.querySelectorAll("[data-post-id]")].map((node) => ({
                    post_id: node.getAttribute("data-post-id") || "",
                    author: (node.querySelector("[data-field=author]") || {}).innerText || "",
                    created_at: (node.querySelector("time") || {}).getAttribute?.("datetime") || "",
                    time_label: (node.querySelector("[data-field=time]") || {}).innerText || "",
                    like_count: (node.querySelector("[data-field=likes]") || {}).innerText || "0",
                    comment_count: (node.querySelector("[data-field=comments]") || {}).innerText || "0",
                    text: (node.querySelector("[data-field=text]") || {}).innerText || "",
                    image_urls: [...node.querySelectorAll("img[data-photo]")].map(best).filter(Boolean),
                    permalink: (node.querySelector("a[data-permalink]") || {}).href || ""
                  }));
                }"""
            )
        except Exception:
            return []
        posts: list[Post] = []
        base = self.page.url if self.page is not None else ""
        for item in raw or []:
            post_id = str(item.get("post_id") or "").strip()
            if not post_id:
                continue
            created = str(item.get("created_at") or "").strip() or None
            label = str(item.get("time_label") or "").strip() or None
            posts.append(
                Post(
                    post_id=post_id,
                    author=str(item.get("author") or "").strip(),
                    created_at=created,
                    time_label=label,
                    like_count=parse_count(item.get("like_count")),
                    comment_count=parse_count(item.get("comment_count")),
                    text=str(item.get("text") or "").strip(),
                    image_urls=_unique_images(item.get("image_urls") or [], base),
                    permalink=_abs_or_none(str(item.get("permalink") or ""), base),
                )
            )
        return posts


def _extract_docs(body: bytes, base_url: str) -> tuple[list[Post], bool | None]:
    """Rút bài viết ra khỏi một response (chạy ở thread, không chạm event loop)."""
    docs = parse_json_payloads(body)
    if not docs:
        return [], None
    posts: list[Post] = []
    flag: bool | None = None
    for doc in docs:
        found = extract_posts(doc, base_url=base_url)
        if not found:
            continue
        posts.extend(found)
        has_next = cursor_has_next(doc)
        if has_next is True:
            flag = True
        elif has_next is False and flag is None:
            flag = False
    return posts, flag


def _maybe_json(ctype: str, url: str) -> bool:
    if "json" in ctype:
        return True
    if "/api/graphql" in url:
        return True
    if "x-javascript" in ctype:
        return True
    return False


def _page_key(url: str) -> str:
    """Chuẩn hóa URL để so sánh: bỏ query, fragment và dấu / cuối."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _unique_images(urls: list, base: str) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for url in urls:
        absolute = urljoin(base, str(url)) if url else ""
        if not absolute or not acceptable_image(absolute) or absolute in seen:
            continue
        seen.add(absolute)
        clean.append(absolute)
    return clean


def _abs_or_none(url: str, base: str) -> str | None:
    text = url.strip()
    if not text:
        return None
    return urljoin(base, text)
