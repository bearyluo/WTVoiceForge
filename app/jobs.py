"""单个重任务的后台管理器。

目录构建、bank 解包这类磁盘/CPU 较重的工作一次只跑一个；通过线程执行，
前端轮询 snapshot()。取消只是设置事件，由任务自行清理临时文件。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

ACTIVE_STATES = ("queued", "running")


class JobError(RuntimeError):
    pass


class JobBusy(JobError):
    pass


class JobCancelled(Exception):
    """任务被用户取消。"""


def check_cancel(job: "Job") -> None:
    if job.cancel_event.is_set():
        raise JobCancelled


_UNSET = object()


class Job:
    def __init__(self, kind: str, subject: str = "") -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.subject = subject
        self.state = "queued"
        self.stage = ""
        self.message = ""
        self.current = 0
        self.total: int | None = None
        self.percent: float | None = None
        self.error = ""
        self.result: dict[str, Any] | None = None
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.cancel_event = threading.Event()
        self._lock = threading.RLock()

    # ---------- 由任务线程调用 ----------

    def update(
        self,
        *,
        stage: str | None = None,
        message: str | None = None,
        current: int | None = None,
        total=_UNSET,
        percent: float | None = None,
    ) -> None:
        with self._lock:
            if stage is not None:
                self.stage = stage
            if message is not None:
                self.message = message
            if current is not None:
                self.current = max(0, int(current))
            if total is not _UNSET:
                self.total = max(0, int(total)) if total else None
                if self.total is None:
                    self.percent = None
            if percent is not None:
                self.percent = max(0.0, min(100.0, float(percent)))
            elif self.total:
                self.percent = min(100.0, self.current * 100.0 / self.total)

    def step(self, message: str | None = None) -> None:
        with self._lock:
            self.current += 1
            if message:
                self.message = message
            if self.total:
                self.percent = min(100.0, self.current * 100.0 / self.total)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            percent = self.percent
            if self.state == "done":
                percent = 100.0
            elapsed = (self.finished_at or time.time()) - self.started_at
            return {
                "id": self.id,
                "kind": self.kind,
                "subject": self.subject,
                "state": self.state,
                "active": self.state in ACTIVE_STATES,
                "stage": self.stage,
                "message": self.message,
                "current": self.current,
                "total": self.total,
                "percent": round(percent, 2) if percent is not None else None,
                "error": self.error,
                "result": self.result,
                "elapsed": int(elapsed),
                "finished": self.finished_at is not None,
            }


Runner = Callable[[Job], dict[str, Any] | None]


class Manager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._job: Job | None = None
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            return self._job.snapshot() if self._job else None

    def busy(self) -> bool:
        with self._lock:
            return bool(self._job and self._job.state in ACTIVE_STATES)

    def start(self, kind: str, runner: Runner, subject: str = "") -> dict[str, Any]:
        with self._lock:
            if self._job and self._job.state in ACTIVE_STATES:
                raise JobBusy(f"已有任务在运行：{self._job.kind}")
            job = Job(kind, subject)
            job.state = "running"
            job.started_at = time.time()
            self._job = job
            self._thread = threading.Thread(
                target=self._run,
                args=(job, runner),
                name=f"job-{kind}",
                daemon=True,
            )
            self._thread.start()
            return job.snapshot()

    def cancel(self) -> dict[str, Any] | None:
        with self._lock:
            job = self._job
            if job and job.state in ACTIVE_STATES:
                job.cancel_event.set()
                job.update(message="正在取消…")
            return job.snapshot() if job else None

    def _run(self, job: Job, runner: Runner) -> None:
        try:
            result = runner(job)
            if job.cancel_event.is_set():
                raise JobCancelled
            job.result = result or {}
            job.state = "done"
            job.percent = 100.0
            job.message = job.message or "完成"
        except JobCancelled:
            job.state = "cancelled"
            job.error = ""
            job.message = "已取消"
        except Exception as exc:  # noqa: BLE001 - 任务错误需要回传给前端
            job.state = "error"
            job.error = str(exc)
            job.message = str(exc)
        finally:
            job.finished_at = time.time()


manager = Manager()
