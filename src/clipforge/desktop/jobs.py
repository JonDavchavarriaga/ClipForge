"""Thread-safe local background job manager with persistent error logging."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import threading
import traceback
from typing import Any, Callable
import uuid

from clipforge.logging_utils import log_info, record_error


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: str = 'queued'
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    logs: list[str] = field(default_factory=list)
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'kind': self.kind,
            'label': self.label,
            'status': self.status,
            'created_at': self.created_at,
            'finished_at': self.finished_at,
            'logs': self.logs[-80:],
            'result': self.result,
            'error': self.error,
        }


class JobManager:
    def __init__(self, logs_dir: Path | None = None):
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._logs_dir = logs_dir

    def submit(self, kind: str, label: str, fn: Callable[[Callable[[str], None]], Any]) -> Job:
        job_id = uuid.uuid4().hex[:10]
        job = Job(id=job_id, kind=kind, label=label)
        with self._lock:
            self._jobs[job_id] = job

        def append_log(message: str):
            text = str(message).strip()
            if text:
                with self._lock:
                    job.logs.append(text)
                log_info(f'job.{kind}.{job_id}', text, self._logs_dir)

        def runner():
            with self._lock:
                job.status = 'running'
            log_info(f'job.{kind}.{job_id}', f'Iniciado: {label}', self._logs_dir)
            try:
                output = fn(append_log)
                with self._lock:
                    job.result = output
                    job.status = 'done'
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                log_info(f'job.{kind}.{job_id}', f'Completado: {label}', self._logs_dir)
            except Exception as exc:
                record_error(
                    f'job.{kind}',
                    exc,
                    context={'job_id': job_id, 'label': label},
                    logs_dir=self._logs_dir,
                )
                with self._lock:
                    job.error = str(exc) or type(exc).__name__
                    job.logs.append(traceback.format_exc().splitlines()[-1])
                    job.status = 'failed'
                    job.finished_at = datetime.now(timezone.utc).isoformat()

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def list_all(self) -> list[dict]:
        with self._lock:
            items = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [job.to_dict() for job in items[:30]]
