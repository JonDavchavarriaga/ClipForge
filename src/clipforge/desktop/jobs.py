"""Sequential FIFO processing queue with live stage/progress tracking and disk logging."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable
import uuid

from clipforge.logging_utils import log_event, log_info, record_error

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
    """Single-worker FIFO queue so heavy Whisper/FFmpeg tasks run cleanly in order."""

    def __init__(self, logs_dir: Path | None = None):
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._pending_order: list[str] = []
        self._queue: queue.Queue[tuple[str, Callable[[ProgressCallback], Any]]] = queue.Queue()
        self._logs_dir = logs_dir
        self._worker = threading.Thread(target=self._worker_loop, name='clipforge-queue', daemon=True)
        self._worker.start()

    def submit(self, kind: str, label: str, fn: Callable[[ProgressCallback], Any]) -> Job:
        job_id = uuid.uuid4().hex[:10]
        job = Job(id=job_id, kind=kind, label=label)
        with self._lock:
            self._jobs[job_id] = job
            self._pending_order.append(job_id)
            pos = len(self._pending_order)
        log_event('queue', 'job_enqueued', {'job_id': job_id, 'kind': kind, 'label': label, 'position': pos}, logs_dir=self._logs_dir)
        self._queue.put((job_id, fn))
        return job

    def _worker_loop(self):
        while True:
            job_id, fn = self._queue.get()
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    self._queue.task_done()
                    continue
                if job_id in self._pending_order:
                    self._pending_order.remove(job_id)
                job.status = 'running'
                job.stage = 'Iniciando'
                job.percent = 2
                job.detail = 'Preparando proceso...'
                job.started_monotonic = time.monotonic()

            log_event('queue', 'job_started', {'job_id': job_id, 'kind': job.kind, 'label': job.label}, logs_dir=self._logs_dir)

            def update_progress(stage: str, percent: int, detail: str):
                with self._lock:
                    job.stage = stage
                    job.percent = max(0, min(100, int(percent)))
                    job.detail = detail
                log_info(f'queue.{job.kind}.{job_id}', f'{stage} ({percent}%): {detail}', self._logs_dir)

            try:
                output = fn(update_progress)
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
