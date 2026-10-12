# pagecrawl

Tool lấy bài viết từ một nhóm hoặc page Facebook — những bài **đang hiện** trên màn hình.

Mở trình duyệt, đăng nhập, bấm crawl. Tool tự cuộn và lưu lại:

- nội dung, tác giả, thời gian đăng
- số like, số bình luận
- ảnh trong bài
- **link bài viết** (để mở lại bằng tay)

Dữ liệu lưu vào SQLite. Có giao diện web theo dõi trên máy tính.

> Tool **không** lấy nội dung bình luận khi crawl bài. Phần bình luận là một chế độ
> riêng, xem ở mục 6.
> Chỉ dùng cho nhóm/page mà bạn có quyền xem. Facebook có thể chặn nếu bạn
> crawl quá nhanh hoặc quá nhiều.

---

## 1. Cần cài gì

| Cần | Ghi chú |
|---|---|
| Python 3.11 trở lên | kiểm tra bằng lệnh `python --version` |
| Một tài khoản Facebook | dùng để đăng nhập, tool không tự đăng nhập |

Chạy được trên Windows, macOS và Linux.

---

## 2. Cài đặt

Mở terminal (PowerShell trên Windows) ở thư mục dự án, gõ từng lệnh:

```bash
# 1. Tạo môi trường ảo
python -m venv .venv

# 2. Bật môi trường ảo
#    Windows:
.venv\Scripts\activate
#    macOS / Linux:
source .venv/bin/activate

# 3. Cài thư viện
pip install -e .

# 4. Cài trình duyệt Chromium cho Playwright
playwright install chromium
```

Xong. Không cài thêm gì.

---

## 3. Chạy

### Cách 1 — có giao diện web (khuyên dùng)

```bash
pagecrawl ui
```

Mở trình duyệt vào **http://127.0.0.1:8787**. Mọi thao tác nằm trên trang này.

### Cách 2 — gõ lệnh, không cần web

```bash
pagecrawl run --url "https://www.facebook.com/groups/ten-nhom-cua-ban"
```

Lệnh này cũng mở web monitor ở cổng 8787 để bạn bấm "Bắt đầu crawl".

### Lệnh khác

```bash
# Xem lại bài đã lưu, in ra terminal
pagecrawl report --url "https://www.facebook.com/groups/ten-nhom-cua-ban"

# Lấy bình luận cho các bài trong một collection
pagecrawl comments --collection "mặc định" --min-comments 2

# Mở web ở cổng khác
pagecrawl ui --port 9000

# Chạy ẩn cửa sổ trình duyệt
pagecrawl ui --headless

# Thử tool với trang web giả, không cần Facebook
pagecrawl mock
```

Thoát chương trình bằng `Ctrl+C`.

---

## 4. Dùng giao diện web

Trang monitor có 3 phần: thanh công cụ trên, nhật ký bên trái, danh sách bài ở giữa.

### Bước 1 — nhập link

Dán link nhóm hoặc page vào ô **https://www.facebook.com/groups/...**, rồi chọn:

| Ô | Ý nghĩa |
|---|---|
| **Cập nhật bài đã lưu nếu nội dung đổi** | tick nếu muốn tool kiểm tra lại bài cũ. Không tick thì tool bỏ qua bài đã có, chạy nhanh hơn. |
| **Chờ mỗi vòng** | số giây nghỉ giữa mỗi lần cuộn. Tăng lên (ví dụ 12) thì chạy chậm và nhẹ hơn với Facebook. |
| **Trình duyệt** | `Ẩn (headless)` = không hiện cửa sổ. `Hiện cửa sổ` = bạn nhìn thấy và can thiệp được. |

### Bước 2 — bấm **Mở trang**

Chromium mở ra và tự đi tới link của bạn.

Lần đầu chạy: **đăng nhập Facebook ngay trong cửa sổ đó**. Lần sau không cần đăng nhập lại.

Bấm **Bắt đầu crawl** khi đã thấy trang feed hiện ra.

### Bước 3 — theo dõi

Thanh số trên đầu đếm: **Mới**, **Cập nhật**, **Bỏ qua**, **Vòng**, **Đã lưu**.
Cột bên trái là nhật ký, ghi lại từng bước.

Bài mới hiện dần xuống giữa trang. Mỗi bài có nút **Xem bài trên Facebook**.

Bấm **Dừng** để dừng. Tool tự dừng luôn nếu quét mà không có bài mới.

> Chế độ `Ẩn (headless)` không hiện cửa sổ nên **không đăng nhập được bằng tay**.
> Lần đầu hãy chọn `Hiện cửa sổ` để đăng nhập, các lần sau mới dùng headless.

---

## 5. Collection

Collection = một nhóm bài riêng biệt trong cùng database.

Ví dụ: `tin-tuc`, `hoc-tap`, `tuyen-dung`. Một bài có thể nằm trong nhiều collection.

Trên thanh công cụ:

- **Tạo & chọn** — gõ tên rồi bấm để tạo collection mới và chuyển sang
- **Xóa** — xoá collection đang chọn (không xoá được collection mặc định)
- **Tìm** — lọc bài theo tên tác giả hoặc nội dung

Khi chạy lại với collection khác, dùng:

```bash
pagecrawl run --url "..." --collection "tin-tuc"
```

---

## 6. Lấy bình luận (chế độ riêng)

Sau khi đã crawl bài, có thể lấy thêm bình luận của các bài đó.

### Cách 1 — trên web

Trên thanh công cụ có:

| Nút | Tác dụng |
|---|---|
| **Từ [2] bình luận** | chỉ lấy bài có số bình luận **từ mức này trở lên** (mặc định 2, đổi được) |
| **Lấy bình luận** | bắt đầu lấy cho collection đang chọn |
| **Dừng bình luận** | dừng giữa chừng |
| **Xoá tất cả** | xoá bình luận đã lưu, đặt lại để lấy lại |

Trên mỗi thẻ bài có nút **Bình luận** — bấm vào để xem bình luận đã lưu ngay tại chỗ.
Cột bên trái hiện tiến độ: `đã lấy 3/31 bài · 21 bình luận`.

### Cách 2 — bằng lệnh

```bash
pagecrawl comments --collection "tin-tuc" --min-comments 2
```

Thêm `--limit 5` để chỉ lấy 5 bài, `--headed` để hiện cửa sổ trình duyệt.

Mặc định lấy **hết** bài đang chờ trong collection, không giới hạn. Lúc bắt đầu
tool sẽ báo trước còn bao nhiêu bài và ước chừng bao nhiêu phút.

### Cách chạy cho an toàn

- Chạy **tuần tự từng bài**, nghỉ vài giây giữa mỗi bài.
- Mỗi bài là **một lần tải trang riêng**. Collection càng nhiều bài càng lâu.
- **Dừng được giữa chừng.** Bài nào lấy xong sẽ được đánh dấu, chạy lại sẽ bỏ qua
  và tiếp tục từ bài còn dang dở — không lặp lại bài đã xong.
- Bài nào lỗi sẽ ghi `comments_status = error`, lần sau chạy lại thử tiếp.
- **Xoá và lấy lại:** bấm **Xoá tất cả** rồi **Lấy bình luận**.

### Tốc độ và độ đầy đủ

Facebook đôi khi chỉ nạp một phần bình luận trong lần mở trang đầu. Tool tải
lại trang tối đa `--retries` lần (mặc định 2) và gộp kết quả, nên càng thử lại
nhiều càng đầy đủ nhưng càng lâu:

| `--retries` | Thời gian mỗi bài | Bình luận lấy được |
|---|---|---|
| `0` | khoảng 16 giây | ít hơn một chút |
| `1` | khoảng 24 giây | nhiều hơn |
| `2` (mặc định) | khoảng 30 giây | nhiều nhất |

Cần nhanh thì dùng `--retries 0`, chạy lại sau cũng được.

Nhịp nghỉ giữa các bài **tự điều chỉnh**: bình thường nghỉ ít, gặp dấu hiệu bất
thường hoặc bị chặn thì nghỉ dài hơn. Luôn có nghỉ, không bao giờ cuộn liên tục.

### Tool lấy được gì

Mỗi bình luận lưu: tác giả, nội dung, thời gian, số thích, `comment_id` của
Facebook, và bình luận cha (nếu là trả lời).

Bình luận bị Facebook ẩn sẽ bị bỏ qua.

**Hai kiểu trang đều chạy được:**

| Kiểu trang | Ví dụ | Cách hiển thị |
|---|---|---|
| Bài trong nhóm, bài thường | `.../posts/pfbid...` | bài mở trong hộp thoại, bình luận bên trong |
| **Bài ảnh** | `.../photo/?fbid=...` | không có hộp thoại, ảnh bên trái, bình luận ở **cột phải** |

**Lưu ý về con số của Facebook:** số bình luận Facebook hiển thị có thể **không phải**
số bình luận thật. Ví dụ một bài ảnh ghi `123` nhưng trang xem ảnh chỉ mở được 37
bình luận (6 bình luận gốc + 31 trả lời, tính từ các nút "Xem N câu trả lời").
Tool lấy đủ những gì trang đó cho hiện — đã đối chiếu 37/37, không sót.

Bình luận bị Facebook **ẩn** thì bỏ qua. Bình luận **trống** (chỉ có avatar, không
có chữ) vẫn được giữ lại với nội dung rỗng.

---

## 7. Dữ liệu nằm ở đâu

Tất cả nằm trong thư mục `data/` (thư mục này **không** được đưa lên GitHub):

| Đường dẫn | Chứa gì |
|---|---|
| `data/crawler.db` | database SQLite, chứa bài + collection |
| `data/posts.jsonl` | bản sao bài viết dạng text, mở bằng Notepad cũng được |
| `data/profile/` | profile trình duyệt, giữ phiên đăng nhập |

Muốn sao lưu thì copy nguyên thư mục `data/`.

Xem bài trong SQLite:

```bash
sqlite3 data/crawler.db "SELECT author, created_at, permalink FROM posts LIMIT 20;"
```

Xem bình luận đã lưu:

```bash
sqlite3 data/crawler.db "SELECT author, substr(text,1,60) FROM comments LIMIT 20;"
```

---

## 8. Chạy test

```bash
pip install -e ".[dev]"
pytest
```

Test không cần mạng và không đụng vào Facebook.

---

## 9. Cấu trúc dự án

```
src/pagecrawl/
  cli.py         các lệnh: ui, run, report, comments, mock
  session.py     nối lệnh + web monitor với trình duyệt
  browser.py     mở Chromium, cuộn trang, bắt dữ liệu từ trang
  extract.py     đọc bài viết từ dữ liệu trang, dựng link bài
  comments.py    lấy bình luận của một bài (mở modal, đổi chế độ, cuộn)
  crawl.py       vòng lặp crawl
  store.py       đọc/ghi SQLite
  monitor.py     web API
  dashboard.html giao diện web
  models.py      cấu trúc dữ liệu bài viết
  state.py       trạng thái phiên chạy
  mocksite.py    trang web giả để thử
tests/           test tự động
```

---

## 10. Khắc phục sự cố

**"Executable file not found" hoặc không gõ được `pagecrawl`**
Chưa bật môi trường ảo. Chạy lại `.venv\Scripts\activate` rồi cài lại `pip install -e .`

**Lỗi khi chạy `playwright install chromium`**
Thiếu mạng hoặc bị chặn. Thử lại, hoặc đặt biến môi trường `PLAYWRIGHT_DOWNLOAD_HOST` sang nguồn mirror.

**Cổng 8787 đã dùng**
Chạy `pagecrawl ui --port 9000`.

**Tool không lấy được bài nào**
Số lượt trên trang đã hết (cần bấm "Tải thêm"), hoặc bạn chưa đăng nhập, hoặc Facebook chặn.
Thử: tick ô cập nhật bài cũ, tăng "Chờ mỗi vòng", giảm tốc độ cuộn.

**Bị yêu cầu xác minh / CAPTCHA**
Facebook đang nghi ngờ. Dừng lại, chờ một lúc, đổi sang chạy `Hiện cửa sổ` và tự xử lý.

**Bài lưu bị trùng**
Cùng một bài thuộc nhiều collection là chuyện bình thường.

---

## Bảo mật

`data/profile/` chứa cookie đăng nhập. Thư mục `data/` đã nằm trong `.gitignore`.

Nếu **đã lỡ push** `data/` lên GitHub:

1. Đổi mật khẩu Facebook ngay.
2. Vào **Cài đặt → Bảo mật → Đăng xuất khỏi mọi thiết bị**.
3. Xoá repository trên GitHub và tạo repo mới (lịch sử git vẫn còn bản cũ).
4. Kéo theo thì xoá `.git` rồi `git init` lại.

Cũng nên thêm `.env` vào `.gitignore` nếu sau này bạn lưu mật khẩu trong file cấu hình.

---

## Giấy phép và trách nhiệm

Chỉ dùng tool này cho nhóm/page mà bạn có quyền truy cập và đồng ý thu thập dữ liệu.

Đây là công cụ học tập/cá nhân. Người dùng tự chịu trách nhiệm trước pháp luật và trước điều khoản sử dụng của Facebook.