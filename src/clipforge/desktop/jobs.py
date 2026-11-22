"""Sequential FIFO processing queue with live progress, cancellation, and idle RAM cleanup."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable
import uuid

from clipforge.logging_utils import log_event, log_info, record_error
from clipforge.pipeline.media import unload_idle_whisper

ProgressCallback = Callable[[str, int, str], None]


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: str = 'queued'
    stage: str = 'En cola'
    percent: int = 0
    detail: str = 'Esperando turno en la cola...'
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_monotonic: float | None = None
    finished_at: str | None = None
    elapsed_seconds: float = 0.0
    result: Any = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self, queue_position: int = 0) -> dict:
        elapsed = self.elapsed_seconds
        if self.status == 'running' and self.started_monotonic is not None:
            elapsed = round(time.monotonic() - self.started_monotonic, 1)
        return {
            'id': self.id,
            'kind': self.kind,
            'label': self.label,
            'status': self.status,
            'stage': self.stage,
            'percent': self.percent,
            'detail': self.detail,
            'queue_position': queue_position,
            'created_at': self.created_at,
            'finished_at': self.finished_at,
            'elapsed_seconds': elapsed,
            'result': self.result,
            'error': self.error,
        }


class JobManager:
    """Single-worker FIFO queue so heavy Whisper/FFmpeg tasks run cleanly in order with cancel support."""

    def __init__(self, logs_dir: Path | None = None):
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._pending_order: list[str] = []
        self._active_job_id: str | None = None
        self._queue: queue.Queue[tuple[str, Callable]] = queue.Queue()
        self._logs_dir = logs_dir
        self._worker = threading.Thread(target=self._worker_loop, name='gevik-queue', daemon=True)
        self._worker.start()

    def submit(self, kind: str, label: str, fn: Callable) -> Job:
        job_id = uuid.uuid4().hex[:10]
        job = Job(id=job_id, kind=kind, label=label)
        with self._lock:
            self._jobs[job_id] = job
            self._pending_order.append(job_id)
            pos = len(self._pending_order)
        log_event('queue', 'job_enqueued', {'job_id': job_id, 'kind': kind, 'label': label, 'position': pos}, logs_dir=self._logs_dir)
        self._queue.put((job_id, fn))
        return job

    def cancel(self, job_id: str | None = None) -> dict:
        """Cancel a specific job (or the currently active job if job_id is omitted)."""
        with self._lock:
            target_id = job_id or self._active_job_id
            if not target_id or target_id not in self._jobs:
                return {'cancelled': False}
            job = self._jobs[target_id]
            job.cancel_event.set()
            if job.status == 'queued':
                if target_id in self._pending_order:
                    self._pending_order.remove(target_id)
                job.status = 'cancelled'
                job.stage = 'Cancelado'
                job.detail = 'Cancelado antes de iniciar'
                job.finished_at = datetime.now(timezone.utc).isoformat()
            elif job.status == 'running':
                job.stage = 'Cancelando...'
                job.detail = 'Deteniendo proceso en curso...'
        log_event('queue', 'job_cancel_requested', {'job_id': target_id}, logs_dir=self._logs_dir)
        return {'cancelled': True, 'job_id': target_id}

    def cancel_all(self):
        with self._lock:
            for job in self._jobs.values():
                if job.status in {'queued', 'running'}:
                    job.cancel_event.set()
                    if job.status == 'queued':
                        job.status = 'cancelled'
                        job.stage = 'Cancelado'
                        job.finished_at = datetime.now(timezone.utc).isoformat()
            self._pending_order.clear()

    def _worker_loop(self):
        while True:
            try:
                job_id, fn = self._queue.get(timeout=15.0)
            except queue.Empty:
                unload_idle_whisper()
                continue

            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.cancel_event.is_set() or job.status == 'cancelled':
                    if job_id in self._pending_order:
                        self._pending_order.remove(job_id)
                    self._queue.task_done()
                    continue
                if job_id in self._pending_order:
                    self._pending_order.remove(job_id)
                self._active_job_id = job_id
                job.status = 'running'
                job.stage = 'Iniciando'
                job.percent = 2
                job.detail = 'Preparando proceso...'
                job.started_monotonic = time.monotonic()

            log_event('queue', 'job_started', {'job_id': job_id, 'kind': job.kind, 'label': job.label}, logs_dir=self._logs_dir)

            def update_progress(stage: str, percent: int, detail: str):
                with self._lock:
                    if not job.cancel_event.is_set():
                        job.stage = stage
                        job.percent = max(0, min(100, int(percent)))
                        job.detail = detail
                log_info(f'queue.{job.kind}.{job_id}', f'{stage} ({percent}%): {detail}', self._logs_dir)

            try:
                # Support both fn(progress) and fn(progress, cancel_event)
                import inspect
                params = len(inspect.signature(fn).parameters)
                output = fn(update_progress, job.cancel_event) if params >= 2 else fn(update_progress)
                with self._lock:
                    job.result = output
                    job.status = 'done'
                    job.percent = 100
                    if job.stage in {'Iniciando', 'En cola'}:
                        job.stage = 'Completado'
                    job.elapsed_seconds = round(time.monotonic() - (job.started_monotonic or time.monotonic()), 1)
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                log_event('queue', 'job_finished', {
                    'job_id': job_id,
                    'kind': job.kind,
                    'label': job.label,
                    'elapsed_seconds': job.elapsed_seconds,
                    'result': output if isinstance(output, (dict, str, int, float, bool)) else str(output),
                }, logs_dir=self._logs_dir)
            except InterruptedError:
                with self._lock:
                    job.status = 'cancelled'
                    job.stage = 'Cancelado'
                    job.detail = 'Cancelado por el usuario'
                    job.elapsed_seconds = round(time.monotonic() - (job.started_monotonic or time.monotonic()), 1)
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                log_event('queue', 'job_cancelled', {'job_id': job_id, 'label': job.label}, logs_dir=self._logs_dir)
            except Exception as exc:
                record_error(
                    f'job.{job.kind}',
                    exc,
                    context={'job_id': job_id, 'label': job.label, 'stage': job.stage},
                    logs_dir=self._logs_dir,
                )
                with self._lock:
                    job.error = str(exc) or type(exc).__name__
                    job.status = 'failed'
                    job.stage = 'Error'
                    job.detail = job.error
                    job.elapsed_seconds = round(time.monotonic() - (job.started_monotonic or time.monotonic()), 1)
                    job.finished_at = datetime.now(timezone.utc).isoformat()
            finally:
                with self._lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                self._queue.task_done()

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            pos = self._pending_order.index(job_id) + 1 if job_id in self._pending_order else 0
            return job.to_dict(queue_position=pos)

    def snapshot(self) -> dict:
        with self._lock:
            items = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[:25]
            serialized = []
            active = None
            queued_count = 0
            for job in items:
                pos = self._pending_order.index(job.id) + 1 if job.id in self._pending_order else 0
                d = job.to_dict(queue_position=pos)
                serialized.append(d)
                if job.status == 'running':
                    active = d
                elif job.status == 'queued':
                    queued_count += 1
            return {
                'active': active,
                'queued_count': queued_count,
                'jobs': serialized,
            }

    def list_all(self) -> list[dict]:
        return self.snapshot()['jobs']
