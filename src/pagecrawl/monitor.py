import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from pagecrawl.state import AppState
from pagecrawl.store import Store

Opener = Callable[[str, bool, float, bool], Awaitable[None]]


class OpenBody(BaseModel):
    url: str
    refresh: bool = False
    idle_timeout: float = Field(default=8, ge=1, le=60)
    headless: bool = False


class CollectionBody(BaseModel):
    name: str


class SelectBody(BaseModel):
    collection_id: int

_HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


def create_app(state: AppState, store: Store, opener: Opener | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.attach()
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.app_state = state
    app.state.store = store

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _HTML

    @app.get("/api/state")
    async def api_state() -> dict:
        return state.snapshot()

    @app.get("/api/posts")
    async def api_posts(page_url: str = "", q: str = "", offset: int = 0, limit: int = 50, collection_id: int = 0) -> dict:
        effective = collection_id or state.collection_id
        return store.list_posts(page_url=page_url, query=q, offset=offset, limit=min(limit, 200), collection_id=effective)

    @app.get("/api/collections")
    async def api_collections() -> dict:
        return {"collections": store.list_collections(), "current": state.collection_id}

    @app.post("/api/collections")
    async def api_create_collection(body: CollectionBody) -> dict:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="tên collection trống")
        collection = store.create_collection(name)
        return {"ok": True, "collection": collection}

    @app.delete("/api/collections/{collection_id}")
    async def api_delete_collection(collection_id: int):
        if state.status in {"starting", "opening", "waiting_login", "running", "stalled"}:
            raise HTTPException(status_code=409, detail="đang crawl — dừng rồi hãy xóa collection")
        if collection_id == 1:
            raise HTTPException(status_code=400, detail="không thể xóa collection mặc định")
        cols_del, posts_del = store.delete_collection(collection_id)
        if state.collection_id == collection_id:
            fallback = store.get_collection(1)
            if fallback:
                state.collection_id = int(fallback["id"])
                state.collection_name = str(fallback["name"])
                state.emit_status()
        return {"ok": True, "deleted": {"collections": cols_del, "posts": posts_del}}

    @app.post("/api/select")
    async def api_select(body: SelectBody) -> dict:
        if state.status in {"starting", "opening", "waiting_login", "running", "stalled"}:
            raise HTTPException(status_code=409, detail="đang crawl — dừng rồi hãy đổi collection")
        collection = store.get_collection(body.collection_id)
        if collection is None:
            raise HTTPException(status_code=404, detail="không có collection này")
        state.collection_id = collection["id"]
        state.collection_name = collection["name"]
        state.log(f"chuyển sang collection: {collection['name']}")
        state.emit_status()
        return {"ok": True}

    @app.post("/api/open")
    async def api_open(body: OpenBody) -> dict:
        if opener is None:
            raise HTTPException(status_code=404, detail="hãy chạy pagecrawl ui")
        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="URL phải bắt đầu bằng http:// hoặc https://")
        if not state.reserve_job(url, body.refresh, body.idle_timeout, body.headless):
            raise HTTPException(status_code=409, detail="đang có phiên crawl")
        state.log(
            "mở Chromium " + ("headless" if body.headless else "có giao diện")
            + f" — {url}"
        )
        state.job = asyncio.create_task(
            opener(url, body.refresh, body.idle_timeout, body.headless)
        )
        return {"ok": True, "headless": body.headless}

    @app.post("/api/start")
    async def api_start() -> dict:
        state.attach()
        if state.status != "waiting_login":
            raise HTTPException(status_code=409, detail="chưa tới bước đăng nhập")
        state.log("bắt đầu crawl")
        state.start_event.set()
        return {"ok": True}

    @app.post("/api/stop")
    async def api_stop() -> dict:
        state.attach()
        if state.status in {"done", "error", "idle"}:
            if state.controls:
                return {"ok": True}
            state.log("đóng monitor")
            if state.shutdown_event is not None:
                state.shutdown_event.set()
            return {"ok": True}
        state.log("dừng")
        state.stop_reason = "user_stop"
        state.stop_event.set()
        state.start_event.set()
        return {"ok": True}

    @app.get("/api/events")
    async def api_events(request: Request) -> StreamingResponse:
        queue = asyncio_queue()
        state.subscribers.append(queue)

        async def stream():
            try:
                yield _sse(state.snapshot())
                while True:
                    if await request.is_disconnected():
                        break
                    event = await queue.get()
                    yield _sse(event)
            finally:
                if queue in state.subscribers:
                    state.subscribers.remove(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def asyncio_queue():
    return asyncio.Queue()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
