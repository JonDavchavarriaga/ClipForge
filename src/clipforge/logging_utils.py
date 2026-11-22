"""Persistent rotating text logs + structured JSONL error log for ClipForge.

Also protects headless Windows launches (`pythonw.exe`) where `sys.stdout` and `sys.stderr`
are `None`, preventing crashes inside libraries like `tqdm` / `huggingface_hub` / `faster-whisper`.
"""

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import traceback
from typing import Any

_LOCK = threading.RLock()
_ACTIVE_LOGS_DIR: Path | None = None
_LOGGER_NAME = 'clipforge'


class _NullStream:
    def write(self, data):
        return len(data) if data else 0

    def flush(self):
        return None

    def isatty(self):
        return False


def ensure_safe_stdio():
    """Ensure stdout/stderr are never None under pythonw.exe and disable tty progress bars."""
    if sys.stdout is None:
        sys.stdout = _NullStream()
    if sys.stderr is None:
        sys.stderr = _NullStream()
    if not getattr(sys.stderr, 'isatty', lambda: False)():
        os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')
        os.environ.setdefault('TQDM_DISABLE', '1')


def configure_logging(logs_dir: Path) -> logging.Logger:
    global _ACTIVE_LOGS_DIR
    ensure_safe_stdio()
    logs_dir = logs_dir.resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / 'clipforge.log'

    with _LOCK:
        _ACTIVE_LOGS_DIR = logs_dir
        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        # Avoid duplicating file handlers pointing to the same path
        target_str = str(log_file)
        for handler in list(logger.handlers):
            if isinstance(handler, RotatingFileHandler) and getattr(handler, 'baseFilename', '') == target_str:
                return logger
            handler.close()
            logger.removeHandler(handler)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8',
            delay=True,
        )
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        )
        logger.addHandler(file_handler)
        return logger


def close_logging():
    """Close and detach open file handlers (needed on Windows before removing temp log dirs)."""
    global _ACTIVE_LOGS_DIR
    with _LOCK:
        logger = logging.getLogger(_LOGGER_NAME)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        _ACTIVE_LOGS_DIR = None


def log_info(source: str, message: str, logs_dir: Path | None = None):
    target_dir = logs_dir or _ACTIVE_LOGS_DIR
    if target_dir is None:
        return
    logger = configure_logging(target_dir)
    logger.info('[%s] %s', source, message)


def record_error(
    source: str,
    error: BaseException | str,
    *,
    context: dict[str, Any] | None = None,
    logs_dir: Path | None = None,
) -> dict:
    """Persist an error to both clipforge.log and structured errors.jsonl."""
    target_dir = logs_dir or _ACTIVE_LOGS_DIR
    if target_dir is not None:
        target_dir = target_dir.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

    is_exc = isinstance(error, BaseException)
    error_type = type(error).__name__ if is_exc else 'Error'
    message = str(error) if is_exc else str(error)
    tb = ''.join(traceback.format_exception(type(error), error, error.__traceback__)) if is_exc else ''

    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source': source,
        'error_type': error_type,
        'message': message,
        'context': context or {},
        'traceback': tb.strip(),
    }

    if target_dir is not None:
        logger = configure_logging(target_dir)
        ctx_text = f' | ctx={json.dumps(entry["context"], ensure_ascii=False)}' if entry['context'] else ''
        logger.error('[%s] %s: %s%s', source, error_type, message, ctx_text)
        if tb.strip():
            logger.error('%s', tb.strip())

        errors_file = target_dir / 'errors.jsonl'
        with _LOCK:
            with errors_file.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return entry


def read_recent_logs(logs_dir: Path, limit: int = 120) -> list[str]:
    log_file = logs_dir / 'clipforge.log'
    if not log_file.is_file():
        return []
    lines = log_file.read_text(encoding='utf-8', errors='replace').splitlines()
    return lines[-limit:]


def read_recent_errors(logs_dir: Path, limit: int = 40) -> list[dict]:
    errors_file = logs_dir / 'errors.jsonl'
    if not errors_file.is_file():
        return []
    entries: list[dict] = []
    for raw in errors_file.read_text(encoding='utf-8', errors='replace').splitlines()[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            entries.append({'timestamp': '', 'source': 'raw', 'error_type': 'Log', 'message': raw})
    return list(reversed(entries))
