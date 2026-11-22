"""Local Desktop App launcher for ClipForge (opens native Edge/Chrome --app window)."""

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import webbrowser

from clipforge.config import ROOT_DIR, load_channel_profiles
from clipforge.desktop.icon import generate_ico
from clipforge.desktop.server import create_server
from clipforge.logging_utils import close_logging, configure_logging, ensure_safe_stdio, log_info, record_error
from clipforge.storage import resolve_workspace


def summary() -> str:
    profiles = load_channel_profiles().get('channels', {})
    names = ', '.join(sorted(profiles)) or 'sin perfiles'
    return f'ClipForge local workspace: {ROOT_DIR} | Canales configurados: {names}'


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


def _open_desktop_window(url: str):
    browser = _find_chromium_app_browser()
    if browser:
        profile_dir = ROOT_DIR / '.venv' / 'clipforge-ui-profile'
        profile_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [
                browser,
                f'--app={url}',
                '--window-size=1440,920',
                f'--user-data-dir={profile_dir}',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    webbrowser.open(url)


def run_desktop_app(port: int = 8765, *, open_window: bool = True):
    ensure_safe_stdio()
    workspace = resolve_workspace()
    configure_logging(workspace.logs_dir)

    ico_path = ROOT_DIR / 'assets' / 'icons' / 'clipforge.ico'
    if not ico_path.is_file():
        try:
            generate_ico(ico_path)
        except Exception as exc:
            record_error('launcher.icon', exc, logs_dir=workspace.logs_dir)

    bound_port = _find_open_port(port)
    server, state = create_server('127.0.0.1', bound_port, workspace_dir=workspace.root)
    actual_port = server.server_address[1]
    url = f'http://127.0.0.1:{actual_port}/'
    log_info('launcher', f'ClipForge Studio iniciado en {url} | workspace={workspace.root}', workspace.logs_dir)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    if open_window:
        _open_desktop_window(url)
    else:
        print(f'ClipForge Studio escuchando en {url}', flush=True)

    try:
        while not state.shutdown_event.wait(timeout=0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        state.recorder.stop()
        server.shutdown()
        server.server_close()
        log_info('launcher', 'ClipForge Studio detenido correctamente.', workspace.logs_dir)
        close_logging()


def main():
    parser = argparse.ArgumentParser(description='Inicia ClipForge Studio en ventana local de escritorio.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT_DIR)
    run_desktop_app(port=args.port, open_window=not args.no_browser)


if __name__ == '__main__':
    main()
