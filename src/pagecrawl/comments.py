"""Crawl bình luận của một bài viết bằng cách mở trực tiếp permalink.

Facebook hiển thị bài trong một modal (dialog). Bên trong modal có nút sắp xếp
bình luận, mặc định là "Phù hợp nhất" — chế độ này chỉ hiện bình luận của bạn bè
và bình luận nhiều tương tác. Muốn lấy hết phải đổi sang "Tất cả bình luận".

Ba điểm quan trọng đã kiểm chứng trên bài thật:

1. Modal bài viết là ``[role="dialog"][aria-modal="true"]``. Trang có tới hai
   phần tử ``role="dialog"``; chọn sai sẽ thao tác nhầm lên trang nền.
2. Phải cuộn bằng ``mouse.wheel`` với con trỏ đặt *bên trong* modal. Cuộn mặc
   định sẽ cuộn trang nền; gán thẳng ``scrollTop`` chỉ tải được một đợt rồi dừng.
3. Reply nằm *ngang hàng* với bình luận gốc trong DOM, phân biệt bằng class
   hash sẽ bị đổi theo phiên bản Facebook. Cách ổn định là đo trước/sau: ghi
   danh sách id hiện có, bấm nút "Xem N câu trả lời", rồi các id mới xuất hiện
   ngay sau bình luận cha chính là nhóm trả lời của nó.
"""

import asyncio
import random
import re
import time
from typing import Any, Callable

from pagecrawl.models import Comment

MODAL = '[role="dialog"][aria-modal="true"]'

# Nhãn dùng để đổi chế độ sắp xếp bình luận.
SORT_CURRENT = ("Phù hợp nhất", "Mới nhất", "Tất cả bình luận", "Most relevant", "All comments")

# Dấu hiệu một ô bình luận (mỗi bình luận có nút "Ẩn hoặc báo cáo bình luận này").
_MARK = "Ẩn hoặc báo cáo bình luận này"

# Bình luận đã bị Facebook ẩn — bỏ qua theo yêu cầu.
_HIDDEN = re.compile(r"bình luận này đã bị ẩn|comment này đã bị ẩn|nội dung này đã bị ẩn", re.I)


def _is_marked_js() -> str:
    return (
        "el => /"
        + _MARK
        + "/.test([...el.querySelectorAll('[aria-label]')]"
        ".map(x => x.getAttribute('aria-label') || '').join('|'))"
    )


async def page_wait(page: Any, ms: int) -> None:
    """Chờ trong thời gian của trình duyệt (giống wait_for_timeout nhưng an toàn hơn)."""
    if page is None:
        await asyncio.sleep(ms / 1000)
        return
    try:
        await page.wait_for_timeout(ms)
    except Exception:
        await asyncio.sleep(ms / 1000)


# Liệt kê bình luận: chỉ lấy ô "lá" để loại chính bài viết và các ô bao bọc.
_LIST_JS = """(sel) => {
  const dlg = document.querySelector(sel);
  if (!dlg) return {error: 'khong thay modal'};
  const marked = %s;
  const all = [...dlg.querySelectorAll('[role="article"]')].filter(marked);
  const leaves = all.filter(a => ![...a.querySelectorAll('[role="article"]')].some(marked));
  const dlgBox = dlg.getBoundingClientRect();

  const pickId = (a) => {
    for (const link of a.querySelectorAll('a[href]')) {
      const href = link.getAttribute('href') || '';
      if (href.includes('comment_id=')) {
        const clean = href.replace(/&__cft__.*/, '');
        try {
          return new URL(clean, location.origin).searchParams.get('comment_id') || '';
        } catch (e) { return ''; }
      }
    }
    return '';
  };

  const rows = leaves.map((a) => {
    const ariaAll = [...a.querySelectorAll('[aria-label]')].map(n => n.getAttribute('aria-label') || '');
    let when = ariaAll.find(l => / lúc \\d{1,2}:\\d{2}$/.test(l)) || '';
    let likes = 0;
    for (const l of ariaAll) {
      const m = l.match(/^(\\d+)\\s*cảm xúc/);
      if (m) { likes = parseInt(m[1], 10); break; }
    }

    // Tác giả: link đầu tiên không rỗng. Ở bài trong nhóm, link mang comment_id
    // lại là link thời gian ("8 tuần") chứ không phải link tác giả.
    const links = [...a.querySelectorAll('a[href]')];
    const timeLink = links.find(l => (l.getAttribute('href') || '').includes('comment_id='));
    const timeLabel = timeLink ? (timeLink.innerText || '').trim() : '';
    const authorLink = links.find(l => l !== timeLink && (l.innerText || '').trim());
    const author = authorLink ? (authorLink.innerText || '').trim() : '';
    if (!when) when = timeLabel;

    // text: bỏ nút bấm, bỏ đúng link tác giả và link thời gian.
    const clone = a.cloneNode(true);
    clone.querySelectorAll('[role="button"]').forEach(n => n.remove());
    clone.querySelectorAll('a[href]').forEach((l) => {
      const href = l.getAttribute('href') || '';
      const label = (l.innerText || '').trim();
      if (href.includes('comment_id=') || (author && label === author)) l.remove();
    });
    clone.querySelectorAll('[aria-label]').forEach(n => {
      const l = n.getAttribute('aria-label') || '';
      if (/ lúc \\d{1,2}:\\d{2}$/.test(l) || l.includes('báo cáo bình luận')) n.remove();
    });
    let text = (clone.innerText || '').replace(/\\s+/g, ' ').trim();

    const hidden = /bình luận này đã bị ẩn/i.test(a.innerText || '');
    // thụt lề: bình luận gốc sát mép, trả lời thụt vào. Facebook không gắn lớp
    // riêng cho trả lời (lớp đổi theo phiên bản) nhưng luôn thụt lề.
    const box = a.getBoundingClientRect();
    return {
      comment_id: pickId(a),
      author, text, created_at: when, like_count: likes, hidden,
      left: Math.round(box.left - dlgBox.left),
      has_replies: /^Xem \\d+ (câu trả lời|phản hồi)/i.test(a.innerText || ''),
      order: all.indexOf(a),
    };
  });
  return { rows, total_articles: all.length };
}"""

# Đã cuộn tới cuối phần bình luận chưa.
_BOTTOM_JS = """(sel) => {
  const dlg = document.querySelector(sel);
  if (!dlg) return true;
  let best = null;
  dlg.querySelectorAll('div').forEach((d) => {
    if (d.scrollHeight > d.clientHeight + 100 && (!best || d.scrollHeight > best.scrollHeight)) best = d;
  });
  if (!best) return true;
  return best.scrollTop + best.clientHeight >= best.scrollHeight - 40;
}"""


# Đánh dấu thứ tự ô bình luận để bấm nút mở trả lời đúng chỗ.
_TAGS_JS = """(sel) => {
  const dlg = document.querySelector(sel);
  if (!dlg) return [];
  const marked = %s;
  const all = [...document.querySelectorAll('[role="article"]')];
  const out = [];
  all.forEach((a, idx) => {
    if (!marked(a)) return;
    if ([...a.querySelectorAll('[role="article"]')].some(marked)) return;
    const btn = [...a.querySelectorAll('[role="button"]')]
      .find(b => /^Xem \\d+ (câu trả lời|phản hồi)/i.test((b.innerText || '').trim()));
    if (!btn) return;
    let id = '';
    for (const link of a.querySelectorAll('a[href]')) {
      const href = link.getAttribute('href') || '';
      if (href.includes('comment_id=')) { id = href; break; }
    }
    out.push({ idx, id });
  });
  return out;
}"""

# Bấm mọi nút mở trả lời đang hiện (dùng khi không cần biết cha-con).
_OPEN_ALL_REPLIES_JS = """(sel) => {
  const dlg = document.querySelector(sel);
  if (!dlg) return 0;
  const btns = [...dlg.querySelectorAll('[role="button"]')]
    .filter((b) => /^Xem \\d+ (câu trả lời|phản hồi)/i.test((b.innerText || '').trim()));
  let n = 0;
  for (const b of btns) {
    try { b.click(); n++; } catch (e) { /* bo qua */ }
  }
  return n;
}"""

# Bấm nút mở trả lời của ô bình luận tại vị trí idx.
_CLICK_REPLY_JS = """(pair) => {
  const a = document.querySelectorAll('[role="article"]')[pair.idx];
  if (!a) return false;
  const btn = [...a.querySelectorAll('[role="button"]')]
    .find(b => /^Xem \\d+ (câu trả lời|phản hồi)/i.test((b.innerText || '').trim()));
  if (!btn) return false;
  btn.click();
  return true;
}"""


class CommentCrawler:
    """Mở một bài và lấy hết bình luận (kể cả trả lời) bên trong modal.

    Crawler dùng *tab riêng* thay vì tab đang mở feed: tab feed gắn handler bắt
    response để lấy bài, mà vùng bình luận phun ra rất nhiều response khi cuộn —
    chạy chung sẽ nghẽn. Tách tab cũng giữ nguyên trang feed cho người dùng.
    """

    def __init__(self, source: Any, *, max_rounds: int = 40) -> None:
        self.source = source
        self.max_rounds = max_rounds
        self._page: Any = None

    @property
    def page(self) -> Any:
        return self._page

    async def _ensure_page(self) -> Any:
        if self._page is not None:
            return self._page
        context = getattr(self.source, "context", None)
        if context is None:
            self._page = getattr(self.source, "page", None)
        else:
            self._page = await context.new_page()
        return self._page

    async def close(self) -> None:
        if self._page is not None and self._page is not getattr(self.source, "page", None):
            try:
                await self._page.close()
            except Exception:
                pass
        self._page = None

    # ------------------------------------------------------------------ mở

    async def open_post(self, permalink: str) -> bool:
        page = await self._ensure_page()
        if page is None:
            return False
        await page.goto(permalink, wait_until="domcontentloaded", timeout=60000)
        await self._pause(2.5, 4.5)
        try:
            await page.wait_for_selector(MODAL, timeout=30000)
        except Exception:
            return False

        # Phải chờ vùng bình luận dựng xong rồi mới đổi chế độ. Đổi sớm quá thì
        # Facebook giữ luôn cursor phân trang cũ, kết quả chỉ lấy được một phần
        # bình luận và không có nút "Xem N câu trả lời".
        try:
            await page.locator(MODAL).get_by_text(SORT_CURRENT[0], exact=True).first.wait_for(
                state="visible", timeout=30000
            )
        except Exception:
            return False
        await self._pause(2.0, 3.5)

        await self._switch_to_all_comments()
        await self._pause(2.0, 3.0)
        await self._move_mouse_into_modal()
        return True

    async def _switch_to_all_comments(self) -> None:
        """Đổi từ "Phù hợp nhất" sang "Tất cả bình luận" (bắt buộc)."""
        page = self.page
        if page is None:
            return
        try:
            label = await page.locator(MODAL).get_by_text(SORT_CURRENT[0], exact=True).first.inner_text(timeout=6000)
        except Exception:
            return
        if "Tất cả bình luận" in label:
            return  # đã ở chế độ cần lấy
        try:
            await page.locator(MODAL).get_by_text(SORT_CURRENT[0], exact=True).first.click(timeout=10000)
            await self._pause(0.8, 1.6)
            await page.get_by_text("Tất cả bình luận", exact=True).first.click(timeout=10000)
            await self._pause(1.5, 2.5)
        except Exception:
            return

    async def _move_mouse_into_modal(self) -> None:
        page = self.page
        if page is None:
            return
        try:
            box = await page.locator(MODAL).bounding_box(timeout=8000)
        except Exception:
            return
        if not box:
            return
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    # -------------------------------------------------------------- đọc DOM

    async def _rows(self) -> list[dict]:
        page = self.page
        if page is None:
            return []
        try:
            data = await page.evaluate(_LIST_JS % _is_marked_js(), MODAL)
        except Exception:
            return []
        rows = data.get("rows") or []
        return rows

    async def _tags(self) -> list[dict]:
        page = self.page
        if page is None:
            return []
        try:
            return await page.evaluate(_TAGS_JS % _is_marked_js(), MODAL) or []
        except Exception:
            return []

    async def _click_reply(self, idx: int) -> bool:
        page = self.page
        if page is None:
            return False
        try:
            return bool(await page.evaluate(_CLICK_REPLY_JS, {"idx": idx}))
        except Exception:
            return False

    # ----------------------------------------------------------------- tải

    async def _scroll(self) -> None:
        """Cuộn tới đáy vùng bình luận để Facebook nạp thêm.

        Cuộn nhiều bước, mỗi bước chờ trình duyệt render xong rồi mới cuộn tiếp.
        """
        page = self.page
        if page is None:
            return
        for _ in range(6):
            await page.mouse.wheel(0, random.randint(560, 700))
            await page.wait_for_timeout(random.randint(300, 400))

    async def _open_all_replies(self) -> int:
        """Bấm mọi nút "Xem N câu trả lời" đang có trong modal."""
        page = self.page
        if page is None:
            return 0
        try:
            clicked = int(await page.evaluate(_OPEN_ALL_REPLIES_JS, MODAL))
        except Exception:
            return 0
        if clicked:
            await page.wait_for_timeout(random.randint(1200, 1600))
        return clicked

    async def _at_bottom(self) -> bool:
        """Đã cuộn tới cuối vùng bình luận chưa (Facebook đang tải thêm ở đáy)."""
        page = self.page
        if page is None:
            return True
        try:
            return bool(await page.evaluate(_BOTTOM_JS, MODAL))
        except Exception:
            return True

    async def collect(
        self,
        target: int = 0,
        on_progress: Callable[[int], None] | None = None,
        budget: float = 60.0,
    ) -> list[Comment]:
        """Lấy bình luận. Trả về list đã gắn parent_id và depth.

        Facebook nạp bình luận không đều và lại *gỡ bỏ* các bình luận đã cuộn qua
        khỏi DOM. Vì vậy phải gộp kết quả của mọi vòng lại chứ không đọc DOM ở
        cuối. Nếu biết ``target`` (số bình luận Facebook hiển thị) thì cứ cuộn
        tới khi đạt hoặc hết giờ.
        """
        last_count = -1
        idle = 0
        reported = -1
        merged: dict[str, dict] = {}
        order = {"n": 0}
        deadline = time.monotonic() + budget

        def absorb(rows: list[dict]) -> int:
            for row in rows:
                cid = row.get("comment_id") or ""
                if not cid:
                    continue
                seen = merged.get(cid)
                if seen is None:
                    item = dict(row)
                    item["order"] = order["n"]
                    order["n"] += 1
                    merged[cid] = item
                else:
                    if (row.get("text") or "") and not (seen.get("text") or ""):
                        seen["text"] = row["text"]
                    if not (seen.get("author") or "") and (row.get("author") or ""):
                        seen["author"] = row["author"]
                    if not (seen.get("created_at") or "") and (row.get("created_at") or ""):
                        seen["created_at"] = row["created_at"]

        for _ in range(self.max_rounds):
            await self._scroll()
            absorb(await self._rows())
            if on_progress is not None and len(merged) != reported:
                reported = len(merged)
                on_progress(reported)

            # Mở nốt các trả lời còn đang gập (chế độ "Tất cả bình luận" thường đã
            # hiện sẵn, nhưng vẫn bấm cho chắc).
            await self._open_all_replies()

            total = len(merged)
            if target and total >= target:
                break
            if total > last_count:
                last_count = total
                idle = 0
                continue
            if await self._at_bottom():
                await self._pause(1.5, 3.5)
            idle += 1
            # Không thêm gì nhiều vòng liên tiếp thì dừng, kể cả khi chưa đạt
            # target — Facebook đôi khi báo nhiều hơn thực tế hiển thị.
            if idle >= 4:
                break
            if time.monotonic() >= deadline:
                break

        absorb(await self._rows())
        return self._to_comments(list(merged.values()))

    async def _parent_id(self, idx: int) -> str:
        """Tìm comment_id của ô tại vị trí idx (dùng làm cha cho các reply mới)."""
        page = self.page
        if page is None:
            return ""
        try:
            return str(await page.evaluate(
                """(pair) => {
                  const a = document.querySelectorAll('[role="article"]')[pair.idx];
                  if (!a) return '';
                  for (const link of a.querySelectorAll('a[href]')) {
                    const href = link.getAttribute('href') || '';
                    if (href.includes('comment_id=')) {
                      try { return new URL(href.replace(/&__cft__.*/, ''), location.origin)
                             .searchParams.get('comment_id') || ''; } catch (e) { return ''; }
                    }
                  }
                  return '';
                }""",
                {"idx": idx},
            ) or "")
        except Exception:
            return ""

    # -------------------------------------------------------------- chuyển đổi

    def _to_comments(self, rows: list[dict]) -> list[Comment]:
        """Chuyển dữ liệu DOM thành Comment, gắn cha-con theo mức thụt lề.

        Facebook hiện trả lời ngay dưới bình luận gốc và thụt lề vào trong. Các
        bình luận cùng một mức thụt lề là cùng một tầng; bình luận của một tầng
        luôn nằm sau tầng trên nó. Từ đó suy ra cha mà không cần dựa vào tên lớp
        (tên lớp của Facebook đổi theo phiên bản).
        """
        rows = sorted(rows, key=lambda r: int(r.get("order") or 0))
        seen: set[str] = set()
        ordered: list[tuple[dict, str | None, int]] = []

        levels: list[int] = []
        for row in rows:
            left = int(row.get("left") or 0)
            if left not in levels:
                levels.append(left)
        levels.sort()

        last_at: dict[int, str] = {}
        for row in rows:
            cid = row.get("comment_id") or ""
            if not cid or cid in seen:
                continue
            left = int(row.get("left") or 0)
            level = levels.index(left)
            parent = last_at.get(level - 1) if level > 0 else None
            if parent is None and level > 0:
                # Lỡ thiếu tầng trên thì bám tầng sâu nhất đã thấy trước đó.
                for deeper in range(level - 1, -1, -1):
                    if deeper in last_at:
                        parent = last_at[deeper]
                        break
            seen.add(cid)
            last_at[level] = cid
            ordered.append((row, parent, level))

        out: list[Comment] = []
        for row, parent, depth in ordered:
            if row.get("hidden"):
                continue  # bình luận bị ẩn — bỏ qua
            text = (row.get("text") or "").strip().lstrip("·•-–— ").strip()
            if not text:
                continue
            out.append(
                Comment(
                    comment_id=row["comment_id"],
                    post_id="",
                    parent_id=parent,
                    depth=depth,
                    author=(row.get("author") or "").strip(),
                    text=text,
                    created_at=(row.get("created_at") or "").strip(),
                    like_count=int(row.get("like_count") or 0),
                )
            )
        return out

    async def _pause(self, low: float, high: float) -> None:
        await asyncio.sleep(random.uniform(low, high))


# Nghỉ giữa các bài để không bị coi là spam.
GAP_MIN = 4.0
GAP_MAX = 9.0


async def crawl_comments(
    source: Any,
    store: Any,
    *,
    collection_id: int,
    min_comments: int = 2,
    limit: int | None = None,
    idle_timeout: float = 8.0,
    stop: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> int:
    """Lần lượt mở từng bài của collection để lấy bình luận.

    Chạy tuần tự, nghỉ vài giây giữa mỗi bài. Bài đánh dấu 'done' được bỏ qua nên
    dừng giữa chừng rồi chạy tiếp sẽ tiếp tục từ chỗ còn dang dở.
    """
    pending = store.list_posts_needing_comments(
        collection_id, min_comments=min_comments, limit=limit or 500
    )
    if not pending:
        return 0

    crawler = CommentCrawler(source)
    total = len(pending)
    done = 0

    for index, post in enumerate(pending, start=1):
        if source.blocked:
            if on_progress:
                on_progress("Facebook đang chặn — dừng lại. Thử lại sau.")
            break
        if stop is not None and stop():
            if on_progress:
                on_progress("đã dừng theo yêu cầu — chạy lại sẽ tiếp tục từ bài kế tiếp")
            break
        if on_progress:
            on_progress(
                f"[{index}/{total}] mở bài của {post['author'][:30]} "
                f"({post['comment_count']} bình luận)"
            )
        permalink = post["permalink"]
        target = int(post["comment_count"] or 0)
        items: list[Comment] = []
        merged: dict[str, Comment] = {}
        # Facebook đôi khi chỉ nạp một phần bình luận. Thử lại vài lần, mỗi lần
        # gộp thêm vào kết quả cũ, tới khi lấy đủ bằng số Facebook hiển thị.
        for attempt in range(1, 4):
            try:
                opened = await crawler.open_post(permalink)
            except Exception as exc:
                opened = False
                if on_progress:
                    on_progress(f"  lỗi mở bài: {exc}")
            if not opened:
                if attempt == 3:
                    store.mark_comments_error(collection_id, post["post_id"], "không mở được trang bài")
                    done += 1
                break

            seen = {"n": 0}

            def report(count: int) -> None:
                seen["n"] = count
                if on_progress:
                    on_progress(f"  đang lấy bình luận: {count}")

            try:
                items = await crawler.collect(target=target, on_progress=report)
            except Exception as exc:
                items = []
                if on_progress:
                    on_progress(f"  lỗi lấy bình luận: {exc}")

            before = len(merged)
            for item in items:
                merged.setdefault(item.comment_id, item)
            gained = len(merged) - before
            if not target or len(merged) >= target:
                break
            if attempt < 3:
                if on_progress:
                    on_progress(
                        f"  mới lấy {len(merged)}/{target} (+{gained}) — tải lại trang bài thử lại"
                    )
                await crawler.close()
                await asyncio.sleep(random.uniform(2.5, 5.0))

        items = list(merged.values())
        saved = store.save_comments(collection_id, post["post_id"], items)
        done += 1
        if on_progress:
            reply_n = sum(1 for c in items if c.depth > 0)
            on_progress(f"  xong bài này: lưu {saved} bình luận ({reply_n} trả lời)")
        await crawler.close()
        await asyncio.sleep(random.uniform(GAP_MIN, GAP_MAX))

    await crawler.close()
    if on_progress:
        on_progress(f"xong {done}/{total} bài trong collection")
    return done