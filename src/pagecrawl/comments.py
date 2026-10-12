"""Crawl bình luận của một bài viết bằng cách mở trực tiếp permalink.

Facebook có hai kiểu trang bài, và tool xử lý cả hai:

1. **Bài trong nhóm / bài thường** — mở permalink thì bài hiện trong một modal
   (dialog), bình luận nằm bên trong modal.
2. **Bài ảnh** (``/photo/?fbid=...``) — không có modal. Ảnh chiếm cột trái,
   phần bình luận nằm ở **cột phải** ngay trong trang.

Vì vậy thay vì cố định selector modal, tool **đánh dấu vùng chứa bình luận** bằng
thuộc tính tạm ``data-pc-scope`` rồi mọi truy vấn đều đi qua nó. Cách đánh dấu:
có modal thì lấy modal; không có thì lấy phần tử nhỏ nhất chứa toàn bộ bình luận.

Điểm quan trọng đã kiểm chứng trên bài thật:

- Mặc định Facebook sắp xếp bình luận theo "Phù hợp nhất" (chỉ hiện bình luận bạn
  bè và bình luận nhiều tương tác). Muốn lấy hết phải đổi sang **"Tất cả bình luận"**.
- Phải cuộn bằng ``mouse.wheel`` với con trỏ đặt *bên trong* vùng bình luận.
- Reply nằm *ngang hàng* với bình luận gốc trong DOM, phân biệt bằng class hash
  sẽ bị đổi theo phiên bản Facebook. Cách ổn định là dựa vào **mức thụt lề**.
- Facebook **gỡ bỏ** bình luận đã cuộn qua khỏi DOM, nên phải gộp kết quả mọi vòng.
"""

import asyncio
import random
import re
import time
from typing import Any, Callable

from pagecrawl.models import Comment

MODAL = '[role="dialog"][aria-modal="true"]'
# Selector của vùng chứa bình luận (được gắn động bởi _SCOPE_JS).
SCOPE = '[data-pc-scope="1"]'

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


# Nhãn sắp xếp hiện tại (nằm trong vùng bình luận, hoặc cả trang với bài ảnh).
_SORT_LABEL_JS = """(sel) => {
  const scope = document.querySelector(sel);
  const read = (root) => [...root.querySelectorAll('[role="button"]')]
    .map((b) => (b.innerText || '').trim())
    .filter((t) => /Phù hợp nhất|Tất cả bình luận|Mới nhất/.test(t));
  if (scope) {
    const hit = read(scope);
    if (hit.length) return hit[0];
  }
  const all = read(document);
  return all.length ? all[0] : '';
}"""

# Đánh dấu vùng chứa bình luận. Có modal thì lấy modal; không có (bài ảnh) thì lấy
# phần tử nhỏ nhất chứa toàn bộ bình luận — cột phải của trang xem ảnh.
_SCOPE_JS = """() => {
  document.querySelectorAll('[data-pc-scope]').forEach((n) => n.removeAttribute('data-pc-scope'));
  const modal = document.querySelector('[role="dialog"][aria-modal="true"]');
  if (modal) {
    modal.setAttribute('data-pc-scope', '1');
    return {layout: 'modal', n: -1};
  }
  const marked = %s;
  const all = [...document.querySelectorAll('[role="article"]')].filter(marked);
  const leaves = all.filter((a) => ![...a.querySelectorAll('[role="article"]')].some(marked));
  // Cần tối thiểu 2 bình luận: nếu mới hiện đúng 1 bình luận thì phần tử nhỏ nhất
  // chứa nó chính là nó, đánh dấu vào đó sẽ thu hẹp scope quá mức.
  if (leaves.length < 2) return {layout: '', n: leaves.length};
  let node = leaves[0];
  while (
    node.parentElement &&
    node.parentElement !== document.documentElement &&
    !leaves.every((l) => node.contains(l))
  ) {
    node = node.parentElement;
  }
  node.setAttribute('data-pc-scope', '1');
  return {layout: 'page', n: leaves.length};
}"""


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

# Đã cuộn tới cuối phần bình luận chưa. Vùng cuộn có thể nằm bên trong scope
# (bài trong modal) hoặc là phần tử cha của scope (bài ảnh: cột phải cuộn chung
# với cả trang), nên phải dò từ scope lên tới body.
_BOTTOM_JS = """(sel) => {
  const scope = document.querySelector(sel);
  if (!scope) return true;
  let node = scope;
  while (node && node !== document.body) {
    if (node.scrollHeight > node.clientHeight + 40) {
      return node.scrollTop + node.clientHeight >= node.scrollHeight - 80;
    }
    node = node.parentElement;
  }
  const doc = document.scrollingElement || document.documentElement;
  return doc.scrollTop + doc.clientHeight >= doc.scrollHeight - 80;
}"""


# Còn nút mở trả lời đang gập không.
_HAS_REPLY_BTN_JS = """(sel) => {
  const scope = document.querySelector(sel);
  if (!scope) return false;
  return [...scope.querySelectorAll('[role="button"]')].some(
    (b) => /^Xem \\d+ (câu trả lời|phản hồi)/i.test((b.innerText || '').trim()));
}"""

# Bấm mọi nút mở trả lời đang hiện trong vùng bình luận.
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


class CommentCrawler:
    """Mở một bài và lấy hết bình luận (kể cả trả lời).

    Dùng *tab riêng* thay vì tab đang mở feed: tab feed gắn handler bắt response để
    lấy bài, mà vùng bình luận phun ra rất nhiều response khi cuộn — chạy chung sẽ
    nghẽn. Tách tab cũng giữ nguyên trang feed cho người dùng.
    """

    def __init__(self, source: Any, *, max_rounds: int = 40) -> None:
        self.source = source
        self.max_rounds = max_rounds
        self._page: Any = None
        self.layout = ""
        self.scope_comments = 0

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

    async def _mark_scope(self) -> str:
        """Đánh dấu vùng chứa bình luận. Trả về 'modal', 'page' hoặc rỗng."""
        page = self.page
        if page is None:
            return ""
        try:
            info = await page.evaluate(_SCOPE_JS % _is_marked_js())
        except Exception:
            return ""
        layout = str((info or {}).get("layout") or "")
        self.scope_comments = int((info or {}).get("n") or 0)
        if layout:
            self.layout = layout
        return layout

    async def open_post(self, permalink: str) -> bool:
        page = await self._ensure_page()
        if page is None:
            return False
        await page.goto(permalink, wait_until="domcontentloaded", timeout=60000)

        # Chờ vùng bình luận dựng xong rồi mới đổi chế độ. Đổi sớm quá thì
        # Facebook giữ luôn cursor phân trang cũ, kết quả chỉ lấy được một phần
        # bình luận và không có nút "Xem N câu trả lời".
        #
        # Chờ theo điều kiện chứ không ngủ cố định: vùng bình luận dựng xong thì
        # đi tiếp ngay, không cần chờ hết thời gian đặt trước.
        ready = False
        for _ in range(40):
            if await self._mark_scope():
                try:
                    await page.locator(SCOPE).wait_for(state="visible", timeout=700)
                    ready = True
                    break
                except Exception:
                    pass
            await self._pause(0.4, 0.8)
        if not ready:
            return False

        await self._switch_to_all_comments()
        # Chờ tới khi nhãn đổi sang "Tất cả bình luận" là được.
        await self._wait_sort_changed()
        # Facebook có thể vẽ lại vùng bình luận sau khi đổi chế độ.
        await self._mark_scope()
        await self._focus_scope()
        return True

    async def _wait_sort_changed(self, timeout: float = 12.0) -> None:
        """Chờ nhãn sắp xếp chuyển sang "Tất cả bình luận" (thay vì ngủ cứng)."""
        page = self.page
        if page is None:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                text = await page.evaluate(_SORT_LABEL_JS, SCOPE)
            except Exception:
                text = ""
            if "Tất cả bình luận" in (text or ""):
                # Cho Facebook một nhịp để vẽ lại danh sách bình luận.
                await self._pause(0.6, 1.2)
                return
            await self._pause(0.4, 0.9)

    async def _switch_to_all_comments(self) -> None:
        """Đổi từ "Phù hợp nhất" sang "Tất cả bình luận" (bắt buộc)."""
        page = self.page
        if page is None:
            return
        try:
            label = await page.locator(SCOPE).get_by_text(SORT_CURRENT[0], exact=True).first.inner_text(timeout=6000)
        except Exception:
            # Bài ảnh: nút sắp xếp nằm ngoài vùng bình luận, tìm trong cả trang.
            try:
                label = await page.get_by_text(SORT_CURRENT[0], exact=True).first.inner_text(timeout=6000)
            except Exception:
                return
        if "Tất cả bình luận" in label:
            return  # đã ở chế độ cần lấy
        try:
            try:
                await page.locator(SCOPE).get_by_text(SORT_CURRENT[0], exact=True).first.click(timeout=10000)
            except Exception:
                await page.get_by_text(SORT_CURRENT[0], exact=True).first.click(timeout=10000)
            await self._pause(0.8, 1.6)
            await page.get_by_text("Tất cả bình luận", exact=True).first.click(timeout=10000)
            await self._pause(1.5, 2.5)
        except Exception:
            return

    async def _focus_scope(self) -> None:
        """Đưa con trỏ chuột vào vùng bình luận để cuộn đúng chỗ.

        Phải kẹp toạ độ vào trong khung nhìn: ở bài ảnh, cột bình luận nằm bên
        phải và có thể thấp hơn màn hình — con trỏ nằm ngoài khung nhìn thì lệnh
        cuộn không vào đâu cả.
        """
        page = self.page
        if page is None:
            return
        try:
            box = await page.locator(SCOPE).bounding_box(timeout=8000)
        except Exception:
            return
        if not box:
            return
        size = page.viewport_size or {"width": 1280, "height": 900}
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        x = max(12, min(x, size["width"] - 12))
        y = max(12, min(y, size["height"] - 12))
        await page.mouse.move(x, y)

    # -------------------------------------------------------------- đọc DOM

    async def _rows(self) -> list[dict]:
        page = self.page
        if page is None:
            return []
        try:
            data = await page.evaluate(_LIST_JS % _is_marked_js(), SCOPE)
        except Exception:
            return []
        rows = data.get("rows") or []
        return rows

    # ----------------------------------------------------------------- tải

    async def _scroll(self, steps: int = 6) -> None:
        """Cuộn tới đáy vùng bình luận để Facebook nạp thêm.

        Mỗi bước chờ trình duyệt render xong rồi mới cuộn tiếp, nhưng nếu đã chạm
        đáy thì dừng ngay — cuộn thêm chỉ tốn thời gian chứ không tải thêm được gì.
        """
        page = self.page
        if page is None:
            return
        for index in range(steps):
            if index and await self._at_bottom():
                return
            await page.mouse.wheel(0, random.randint(560, 700))
            await page.wait_for_timeout(random.randint(240, 340))

    async def _open_all_replies(self) -> int:
        """Bấm mọi nút "Xem N câu trả lời" đang có trong vùng bình luận."""
        page = self.page
        if page is None:
            return 0
        try:
            clicked = int(await page.evaluate(_OPEN_ALL_REPLIES_JS, SCOPE))
        except Exception:
            return 0
        if clicked:
            await page.wait_for_timeout(random.randint(1200, 1600))
        return clicked

    async def _has_collapsed_replies(self) -> bool:
        """Còn nút "Xem N câu trả lời" đang gập không (nghĩa là còn bình luận ẩn)."""
        page = self.page
        if page is None:
            return False
        try:
            return bool(await page.evaluate(_HAS_REPLY_BTN_JS, SCOPE))
        except Exception:
            return False

    async def _at_bottom(self) -> bool:
        """Đã cuộn tới cuối vùng bình luận chưa (Facebook đang tải thêm ở đáy)."""
        page = self.page
        if page is None:
            return True
        try:
            return bool(await page.evaluate(_BOTTOM_JS, SCOPE))
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
            # Facebook vẽ lại vùng bình luận, làm mất dấu đánh dấu — đánh lại nếu mất.
            if self.page is not None and await self.page.locator(SCOPE).count() == 0:
                await self._mark_scope()
                await self._focus_scope()
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
                # Đã chạm đáy và không còn nút "Xem N câu trả lời" nghĩa là đã lấy
                # hết phần trang này cho — dừng luôn, không chờ vô ích.
                if await self._at_bottom() and not await self._has_collapsed_replies():
                    break
                continue
            if await self._at_bottom():
                # Đã tới đáy: Facebook thường cần vài giây để nạp, chờ có điều
                # kiện thay vì ngủ cứng rồi thử lại từ đầu.
                await self._pause(1.2, 2.4)
            idle += 1
            # Không thêm gì nhiều vòng liên tiếp thì dừng, kể cả khi chưa đạt
            # target — Facebook đôi khi báo nhiều hơn thực tế hiển thị.
            if idle >= 3:
                break
            if time.monotonic() >= deadline:
                break

        absorb(await self._rows())
        return self._to_comments(list(merged.values()))

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
            # Không bỏ bình luận trống: Facebook có bình luận chỉ có avatar và
            # nút "Trả lời", không có chữ (người dùng xoá nội dung). Bỏ đi thì mất
            # dữ liệu mà không ai biết, nên giữ lại với nội dung rỗng.
            text = (row.get("text") or "").strip().lstrip("·•-–— ").strip()
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


# Nghỉ giữa các bài để không bị coi là spam. Khi mọi thứ êm, nghỉ ngắn; gặp dấu
# hiệu bất thường (Facebook báo nhiều hơn thực tế, bài lỗi, bị chặn) thì nghỉ
# dài hơn. Vẫn luôn có nghỉ, chỉ là không ngủ cứng 4-9 giây mọi bài.
GAP_OK = (2.0, 4.0)
GAP_SUSPECT = (8.0, 14.0)
GAP_BAD = (20.0, 35.0)


async def crawl_comments(
    source: Any,
    store: Any,
    *,
    collection_id: int,
    min_comments: int = 2,
    limit: int | None = None,
    retries: int = 2,
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
        for attempt in range(1, max(1, retries + 1) + 1):
            try:
                opened = await crawler.open_post(permalink)
            except Exception as exc:
                opened = False
                if on_progress:
                    on_progress(f"  lỗi mở bài: {exc}")
            if not opened:
                if attempt > max(1, retries + 1):
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
            # Facebook thường báo nhiều hơn số bình luận thật (đã kiểm chứng: một
            # bài ghi 123 nhưng chỉ mở được 37). Tải lại trang mà không thu thêm
            # được gì thì thừa — vừa mất thời gian, vừa tăng số lần tải trang.
            if gained == 0:
                if on_progress:
                    on_progress(
                        f"  lấy được {len(merged)}/{target} — Facebook không hiện thêm, "
                        "coi như đủ"
                    )
                break
            if attempt <= max(1, retries + 1):
                if on_progress:
                    on_progress(
                        f"  mới lấy {len(merged)}/{target} (+{gained}) — tải lại trang bài thử lại"
                    )
                await crawler.close()
                await asyncio.sleep(random.uniform(1.5, 3.0))

        items = list(merged.values())
        saved = store.save_comments(collection_id, post["post_id"], items)
        done += 1
        if on_progress:
            reply_n = sum(1 for c in items if c.depth > 0)
            on_progress(f"  xong bài này: lưu {saved} bình luận ({reply_n} trả lời)")

        # Chọn mức nghỉ dựa trên tình hình thật: Facebook còn bị "đói" bình luận
        # không (lần cuối vẫn thu thêm được), có bị chặn không. Không dùng tỉ lệ
        # lấy/được-báo vì con số của Facebook vốn không đáng tin.
        if source.blocked:
            if on_progress:
                on_progress("Facebook đang phản ứng — nghỉ lâu trước khi tiếp tục")
            gap = GAP_BAD
        elif gained > 0 or not target:
            gap = GAP_OK
        else:
            gap = GAP_SUSPECT
        await crawler.close()
        await asyncio.sleep(random.uniform(*gap))

    await crawler.close()
    if on_progress:
        on_progress(f"xong {done}/{total} bài trong collection")
    return done