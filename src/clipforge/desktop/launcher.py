"""Local Desktop App launcher for Gevik Clip (Single-Instance native window + clean full shutdown)."""

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import webbrowser

from clipforge.config import ROOT_DIR, load_channel_profiles
from clipforge.desktop.icon import generate_ico
from clipforge.desktop.server import create_server
from clipforge.logging_utils import close_logging, configure_logging, ensure_safe_stdio, log_info, record_error
from clipforge.process_manager import (
    REGISTRY,
    check_existing_instance,
    clear_instance_lock,
    write_instance_lock,
)
from clipforge.storage import resolve_workspace


def summary() -> str:
    profiles = load_channel_profiles().get('channels', {})
    names = ', '.join(sorted(profiles)) or 'sin perfiles'
    return f'Gevik Clip workspace: {ROOT_DIR} | Canales configurados: {names}'


def _find_open_port(preferred: int = 8765) -> int:
    for port in (preferred, *range(preferred + 1, preferred + 20), 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return sock.getsockname()[1]
            except OSError:
                continue
    return 0


def _find_chromium_app_browser() -> str | None:
    candidates = [
        shutil.which('msedge'),
        shutil.which('chrome'),
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return str(path)
    return None


def _open_desktop_window(url: str) -> subprocess.Popen | None:
    browser = _find_chromium_app_browser()
    if browser:
        profile_dir = ROOT_DIR / '.venv' / 'gevik-ui-profile'
        profile_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [
                browser,
                f'--app={url}',
                '--window-size=1480,940',
                f'--user-data-dir={profile_dir}',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        REGISTRY.register_ui_window(proc)
        return proc
    webbrowser.open(url)
    return None


def run_desktop_app(port: int = 8765, *, open_window: bool = True):
    ensure_safe_stdio()
    workspace = resolve_workspace()
    configure_logging(workspace.logs_dir)

    # Single-instance check: reuse existing running Gevik Clip instead of opening a duplicate server
    if open_window:
        existing_url = check_existing_instance(workspace.root)
        if existing_url:
            log_info('launcher', f'Instancia activa detectada en {existing_url}; abriendo ventana.', workspace.logs_dir)
            _open_desktop_window(existing_url)
            close_logging()
            return

    ico_path = ROOT_DIR / 'assets' / 'icons' / 'gevik-clip.ico'
    if not ico_path.is_file():
        try:
            generate_ico(ico_path)
        except Exception as exc:
            record_error('launcher.icon', exc, logs_dir=workspace.logs_dir)

    bound_port = _find_open_port(port)
    server, state = create_server('127.0.0.1', bound_port, workspace_dir=workspace.root)
    actual_port = server.server_address[1]
    url = f'http://127.0.0.1:{actual_port}/'
    write_instance_lock(workspace.root, url, actual_port)
    log_info('launcher', f'Gevik Clip iniciado en {url} | workspace={workspace.root}', workspace.logs_dir)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    if open_window:
        _open_desktop_window(url)
    else:
        print(f'Gevik Clip escuchando en {url}', flush=True)

    try:
        while not state.shutdown_event.wait(timeout=0.4):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        # Give HTTP response to /api/shutdown a brief moment to flush before closing UI & sockets
        time.sleep(0.25)
        state.jobs.cancel_all()
        state.recorder.stop()
        REGISTRY.terminate_all(close_ui=True)
        server.shutdown()
        server.server_close()
        clear_instance_lock(workspace.root)
        log_info('launcher', 'Gevik Clip cerrado completamente (servidor, ventana y subprocesos).', workspace.logs_dir)
        close_logging()


def main():
    parser = argparse.ArgumentParser(description='Inicia Gevik Clip en ventana local de escritorio.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT_DIR)
    run_desktop_app(port=args.port, open_window=not args.no_browser)


if __name__ == '__main__':
    main()
