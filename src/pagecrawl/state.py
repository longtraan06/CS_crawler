import asyncio
from typing import Any

from pagecrawl.store import Store


class AppState:
    def __init__(self) -> None:
        self.page_url = ""
        self.refresh = False
        self.idle_timeout = 8.0
        self.headless = False
        self.controls = False
        self.collection_id = 1
        self.collection_name = "mặc định"
        self.run_id: int | None = None
        self.status = "starting"
        self.stop_reason = ""
        self.new_count = 0
        self.skip_count = 0
        self.updated_count = 0
        self.rounds = 0
        self.session_ready = False
        self.logs: list[str] = []
        self.page: Any = None
        self.job: asyncio.Task | None = None
        self.start_event: asyncio.Event | None = None
        self.stop_event: asyncio.Event | None = None
        self.shutdown_event: asyncio.Event | None = None
        self.subscribers: list[asyncio.Queue] = []

    def attach(self) -> None:
        if self.start_event is None:
            self.start_event = asyncio.Event()
            self.stop_event = asyncio.Event()
            self.shutdown_event = asyncio.Event()

    def log(self, line: str) -> None:
        self.logs.append(line)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]
        self.emit({"type": "log", "line": line})

    def set_status(self, status: str) -> None:
        self.status = status
        self.emit_status()

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "status",
            "status": self.status,
            "page_url": self.page_url,
            "refresh": self.refresh,
            "idle_timeout": self.idle_timeout,
            "headless": self.headless,
            "controls": self.controls,
            "collection_id": self.collection_id,
            "collection_name": self.collection_name,
            "stop_reason": self.stop_reason,
            "new_count": self.new_count,
            "skip_count": self.skip_count,
            "updated_count": self.updated_count,
            "rounds": self.rounds,
            "session_ready": self.session_ready,
            "logs": self.logs[-50:],
        }

    def emit_status(self) -> None:
        self.emit(self.snapshot())

    def emit(self, event: dict[str, Any]) -> None:
        dead: list[asyncio.Queue] = []
        for queue in self.subscribers:
            try:
                queue.put_nowait(event)
            except Exception:
                dead.append(queue)
        for queue in dead:
            if queue in self.subscribers:
                self.subscribers.remove(queue)

    def reserve_job(self, url: str, refresh: bool, idle_timeout: float, headless: bool = False) -> bool:
        if self.status in {"starting", "opening", "waiting_login", "running", "stalled"}:
            return False
        self.attach()
        self.page_url = url
        self.refresh = refresh
        self.idle_timeout = idle_timeout
        self.headless = headless
        self.run_id = None
        self.stop_reason = ""
        self.new_count = 0
        self.skip_count = 0
        self.updated_count = 0
        self.rounds = 0
        self.session_ready = False
        self.page = None
        self.logs = []
        if self.start_event is not None:
            self.start_event.clear()
        if self.stop_event is not None:
            self.stop_event.clear()
        self.set_status("opening")
        return True

    def touch_run(self, store: Store) -> None:
        if self.run_id is None:
            return
        store.update_run(
            self.run_id,
            status=self.status,
            stop_reason=self.stop_reason,
            new_count=self.new_count,
            skip_count=self.skip_count,
            updated_count=self.updated_count,
            rounds=self.rounds,
        )
