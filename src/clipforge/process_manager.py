"""Process lifecycle registry, low-priority Windows execution, and single-instance lock for Gevik Clip."""

import atexit
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from urllib.request import urlopen

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0
BELOW_NORMAL_PRIORITY = getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0) if sys.platform == 'win32' else 0
BACKGROUND_FLAGS = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY


class ProcessRegistry:
    """Tracks every active child process so Cancel / Shutdown never leaves orphan FFmpeg/CLI processes."""

    def __init__(self):
        self._lock = threading.RLock()
        self._procs: set[subprocess.Popen] = set()
        self._ui_proc: subprocess.Popen | None = None

    def register(self, proc: subprocess.Popen) -> subprocess.Popen:
        with self._lock:
            self._procs.add(proc)
        return proc

    def unregister(self, proc: subprocess.Popen):
        with self._lock:
            self._procs.discard(proc)

    def register_ui_window(self, proc: subprocess.Popen | None):
        with self._lock:
            self._ui_proc = proc

    def terminate_proc(self, proc: subprocess.Popen | None):
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except OSError:
            pass
        finally:
            self.unregister(proc)

    def terminate_all(self, *, close_ui: bool = True):
        with self._lock:
            procs = list(self._procs)
            ui = self._ui_proc if close_ui else None
            if close_ui:
                self._ui_proc = None
        for proc in procs:
            self.terminate_proc(proc)
        if ui is not None:
            self.terminate_proc(ui)

    def run_managed(
        self,
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        timeout: float | None = None,
        text: bool = True,
        low_priority: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> subprocess.CompletedProcess:
        flags = BACKGROUND_FLAGS if low_priority else CREATE_NO_WINDOW
        kwargs: dict = {
            'cwd': cwd,
            'env': env,
            'stdin': subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'creationflags': flags,
        }
        if text:
            kwargs['text'] = True
            kwargs['encoding'] = 'utf-8'
            kwargs['errors'] = 'replace'

        proc = self.register(subprocess.Popen(cmd, **kwargs))
        try:
            if cancel_event is None:
                stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
                return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

            # Pollable wait so a user clicking "Cancel" stops FFmpeg / Whisper / AI immediately
            result_box: dict = {}

            def reader():
                try:
                    out, err = proc.communicate(input=input_text)
                    result_box['out'] = out
                    result_box['err'] = err
                except Exception as exc:
                    result_box['exc'] = exc

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            elapsed = 0.0
            while t.is_alive():
                if cancel_event.is_set():
                    self.terminate_proc(proc)
                    t.join(timeout=2)
                    raise InterruptedError('Tarea cancelada por el usuario.')
                if timeout is not None and elapsed >= timeout:
                    self.terminate_proc(proc)
                    t.join(timeout=2)
                    raise subprocess.TimeoutExpired(cmd, timeout)
                t.join(timeout=0.15)
                elapsed += 0.15
            if 'exc' in result_box:
                raise result_box['exc']
            return subprocess.CompletedProcess(cmd, proc.returncode, result_box.get('out'), result_box.get('err'))
        except subprocess.TimeoutExpired:
            self.terminate_proc(proc)
            raise
        finally:
            self.unregister(proc)


REGISTRY = ProcessRegistry()
atexit.register(REGISTRY.terminate_all)


def instance_lock_path(workspace_root: Path) -> Path:
    return workspace_root.resolve() / '.gevik_instance.json'


def check_existing_instance(workspace_root: Path) -> str | None:
    """Return active local URL if Gevik Clip is already running, else None."""
    lock_file = instance_lock_path(workspace_root)
    if not lock_file.is_file():
        return None
    try:
        data = json.loads(lock_file.read_text(encoding='utf-8'))
        url = str(data.get('url') or '').rstrip('/')
        if not url.startswith('http://127.0.0.1:'):
            return None
        with urlopen(f'{url}/api/ping', timeout=1.0) as resp:
            if resp.status == 200:
                return f'{url}/'
    except Exception:
        pass
    try:
        lock_file.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def write_instance_lock(workspace_root: Path, url: str, port: int):
    lock_file = instance_lock_path(workspace_root)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'pid': os.getpid(),
        'url': url.rstrip('/'),
        'port': port,
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    lock_file.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def clear_instance_lock(workspace_root: Path):
    lock_file = instance_lock_path(workspace_root)
    try:
        if lock_file.is_file():
            data = json.loads(lock_file.read_text(encoding='utf-8'))
            if data.get('pid') == os.getpid():
                lock_file.unlink(missing_ok=True)
    except Exception:
        pass
