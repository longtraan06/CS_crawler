import asyncio
import sys
import threading
from pathlib import Path

import typer

from pagecrawl.mocksite import serve_mock
from pagecrawl.session import run_session, serve_ui
from pagecrawl.store import Store

app = typer.Typer(add_completion=False, help="Crawl bài viết đang xem được trên một page.")


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


_utf8_stdio()


def _launch_ui(port: int, db: Path, profile: Path, headless: bool) -> None:
    typer.echo(f"Mở http://127.0.0.1:{port}")
    typer.echo("Mọi thao tác crawl nằm trên trang này. Thoát bằng Ctrl+C.")
    asyncio.run(serve_ui(db, profile, port=port, headless=headless))


@app.callback(invoke_without_command=True)
def _setup(ctx: typer.Context) -> None:
    _utf8_stdio()
    if ctx.invoked_subcommand is None:
        _launch_ui(8787, _db_option(), _profile_option(), False)


def _db_option() -> Path:
    return Path("data") / "crawler.db"


def _profile_option() -> Path:
    return Path("data") / "profile"


@app.command()
def ui(
    port: int = typer.Option(8787, "--port", help="Cổng giao diện"),
    db: Path = typer.Option(_db_option(), "--db"),
    profile: Path = typer.Option(_profile_option(), "--profile"),
    headless: bool = typer.Option(False, "--headless", help="Ẩn cửa sổ Chromium. Mặc định là hiện."),
) -> None:
    """Mở một trang để nhập link, chỉnh tuỳ chọn và crawl."""
    _launch_ui(port, db, profile, headless)


@app.command()
def run(
    url: str = typer.Option(..., "--url", help="URL page cần crawl"),
    refresh: bool = typer.Option(False, "--refresh", help="Cập nhật bài đã có nếu like, comment, text hoặc ảnh đổi"),
    db: Path = typer.Option(_db_option(), "--db", help="File SQLite"),
    profile: Path = typer.Option(_profile_option(), "--profile", help="Thư mục profile trình duyệt"),
    port: int = typer.Option(8787, "--port", help="Cổng web monitor"),
    headless: bool = typer.Option(False, "--headless", help="Chạy không giao diện. Mặc định là có cửa sổ."),
    idle_timeout: float = typer.Option(8.0, "--idle-timeout", help="Giây chờ mỗi vòng scroll"),
    collection: str = typer.Option("mặc định", "--collection", help="Collection lưu bài viết (tạo mới nếu chưa có)"),
) -> None:
    if not url.startswith(("http://", "https://")):
        raise typer.BadParameter("URL phải bắt đầu bằng http:// hoặc https://")
    typer.echo(f"Monitor: http://127.0.0.1:{port}")
    typer.echo("Đăng nhập trên cửa sổ trình duyệt nếu site yêu cầu, rồi bấm Bắt đầu crawl.")
    code = asyncio.run(
        run_session(
            url,
            db,
            profile,
            refresh=refresh,
            port=port,
            headless=headless,
            idle_timeout=idle_timeout,
            collection=collection,
        )
    )
    if code:
        raise typer.Exit(code)


@app.command()
def report(
    url: str = typer.Option(..., "--url", help="URL page đã crawl"),
    db: Path = typer.Option(_db_option(), "--db"),
    collection: str = typer.Option("", "--collection", help="Chỉ xem bài trong collection này"),
) -> None:
    store = Store(db)
    try:
        collection_id = None
        if collection:
            match = next((c for c in store.list_collections() if c["name"] == collection), None)
            if match is None:
                raise typer.BadParameter(f"không có collection: {collection}")
            collection_id = match["id"]
        result = store.list_posts(page_url=url, limit=10000, collection_id=collection_id)
    finally:
        store.close()
    typer.echo(f"{result['total']} bài cho {url}")
    for post in reversed(result["posts"]):
        preview = " ".join(post["text"].split())
        if len(preview) > 80:
            preview = preview[:77] + "..."
        when = post["created_at"] or post["time_label"] or ""
        link = post["permalink"] or "(không có link)"
        typer.echo(
            f"- {post['author']} | {when} | like={post['like_count']} comment={post['comment_count']}"
            f" | ảnh={len(post['image_urls'])} | {link}"
        )
        typer.echo(f"    {preview}")


@app.command()
def mock(port: int = typer.Option(8790, "--port", help="Cổng site giả để thử tool")) -> None:
    server = serve_mock(port)
    bound = server.server_address[1]
    typer.echo(f"Mock page: http://127.0.0.1:{bound}/page/demo")
    typer.echo("Tài khoản bất kỳ. Dừng bằng Ctrl+C.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
