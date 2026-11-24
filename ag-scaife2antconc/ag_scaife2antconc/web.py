"""Single-user local browser launcher. ASGI/service/worker layers remain separate."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from . import APP_NAME
from .pipeline import SetupError
from .web_api import create_app, csrf_for
from .web_service import LocalBridge, Workbench
from .web_storage import atomic_json, read_json, state_directory
from .web_launch import find_app_browser, local_url, owns_record, process_image, startup_lock, terminate_owned_server
from .web_worker import process_identity

DEFAULT_PORT = 8766
DEFAULT_IDLE_TIMEOUT = 60
RECOVERY_WAIT = 8
STARTUP_GRACE = 15


def should_exit_workbench(service, idle_limit):
    with service.lock:
        if service.windows:
            return False
        idle = idle_limit > 0 and time.monotonic() - service.last_activity >= idle_limit
        if (service.quit_when_finished or idle) and not service.active_job():
            service.closed = True
            return True
        return False


def request_api(url, secret, endpoint, data=None):
    body = json.dumps(data or {}).encode('utf-8') if data is not None else None
    request = urllib.request.Request(url + endpoint, data=body,
        headers={'Authorization': 'Bearer ' + secret, 'X-AG-CSRF': csrf_for(secret), 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:
        return json.load(response)


def existing(url, secret, timeout=3):
    try:
        # Establish identity before sending a credential to a listening service.
        request = urllib.request.Request(url + '/api/health')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            health = json.load(response)
        return (isinstance(health, dict) and health.get('application') == APP_NAME and health.get('ready', True) and
                health.get('instance') == hashlib.sha256(secret.encode()).hexdigest())
    except (OSError, ValueError):
        return False


def server_record(root):
    try:
        value = read_json(Path(root) / 'server.json', {})
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def listening_socket(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
        sock.listen(128)
        return sock
    except OSError:
        sock.close()
        raise


def connect_or_listen(url, secret, root):
    """Reuse a healthy server, wait for shutdown/startup, recover a verified hang."""
    from urllib.parse import urlsplit
    port = urlsplit(url).port
    record = server_record(root)
    # A concurrently starting instance gets time to become ready before recovery.
    age = max(0, time.time() - record.get('started', 0)) if isinstance(record.get('started', 0), (float, int)) else STARTUP_GRACE
    deadline = time.monotonic() + max(RECOVERY_WAIT, STARTUP_GRACE - age if owns_record(record, root) else 0)
    last_error = None
    while True:
        if existing(url, secret, timeout=.4):
            return None
        try:
            return listening_socket(port)
        except OSError as error:
            last_error = error
        if time.monotonic() >= deadline:
            break
        time.sleep(.15)
    record = server_record(root)
    if local_url(record.get('url')) == url and terminate_owned_server(record, root):
        until = time.monotonic() + 5
        while True:
            try:
                return listening_socket(port)
            except OSError as error:
                last_error = error
                if time.monotonic() >= until:
                    break
                time.sleep(.1)
    raise SetupError(f'Could not bind the local application port ({port}). '
                     'No other application was stopped. Close any previous AG-Scaife2AntConc window and try again.') from last_error


def open_browser(url, secret):
    # A URL fragment is not sent to the HTTP server or its access log. The page
    # exchanges it for an HttpOnly session cookie and removes it from history.
    target = url + '/#launch=' + secret
    browser = None if os.environ.get('AG_BROWSER_TAB') == '1' else find_app_browser()
    if browser:
        try:
            flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
            subprocess.Popen([browser, f'--app={target}'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            return
        except OSError:
            pass
    webbrowser.open_new_tab(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description='AG-Scaife2AntConc: local Ancient Greek workbench')
    parser.add_argument('files', nargs='*')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--browser', action='store_true', help='open in a regular browser tab instead of app window mode')
    parser.add_argument('--idle-timeout', type=int, default=DEFAULT_IDLE_TIMEOUT,
                        help='seconds of inactivity (with no active job) before auto-exit; 0 disables')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    if args.browser:
        os.environ['AG_BROWSER_TAB'] = '1'
    root = Path(args.state or state_directory()).resolve()
    with startup_lock(root):
        service = Workbench(root, local_bridge=LocalBridge())
        record = server_record(root)
        url = (local_url(record.get('url')) if args.port == DEFAULT_PORT else None) or f'http://127.0.0.1:{args.port}'
        app = create_app(service, origin=url)
        secret = app.state.secret
        if args.stop:
            if existing(url, secret, timeout=.5):
                try:
                    request_api(url, secret, '/api/shutdown', {})
                except urllib.error.HTTPError as error:
                    try:
                        message = json.loads(error.read()).get('error', str(error))
                    except (ValueError, TypeError):
                        message = str(error)
                    raise SetupError(message) from error
            elif local_url(record.get('url')) == url and owns_record(record, root):
                terminate_owned_server(record, root)
            return
        sock = connect_or_listen(url, secret, root)
        if sock is None:
            if args.files:
                request_api(url, secret, '/api/inputs/local', {'paths': [str(Path(p).resolve()) for p in args.files]})
            if not args.no_browser:
                # /api/health does not count as activity: renew the idle timer, or a server about to
                # exit with no windows could stop before the browser's first request arrives.
                try:
                    request_api(url, secret, '/api/session', {})
                except OSError:
                    pass
                open_browser(url, secret)
            return
        try:
            if args.files:
                service.add_paths(args.files)
            logging.basicConfig(filename=service.store.root / 'server.log', encoding='utf-8', level=logging.INFO)
            import uvicorn
            config = uvicorn.Config(app, log_config=None, access_log=False, use_colors=False, lifespan='on')
            server = uvicorn.Server(config)
            app = create_app(service, origin=url, secret=secret, shutdown=lambda: setattr(server, 'should_exit', True))
            server.config.app = app
            atomic_json(service.store.root / 'server.json', {
                'application': APP_NAME, 'url': url, 'pid': os.getpid(), 'started': time.time(),
                'process_identity': process_identity(), 'state_directory': str(root),
                'process_image': process_image(),
                'executable': str(Path(sys.executable).resolve()), 'module': str(Path(__file__).resolve()),
                'instance': hashlib.sha256(secret.encode()).hexdigest()})
        except BaseException:
            sock.close()
            raise

    def ready():
        for _ in range(100):
            if existing(url, secret):
                if not args.no_browser:
                    open_browser(url, secret)
                return
            time.sleep(0.1)

    def watch_idle():
        idle_limit = 0 if args.no_browser else max(0, args.idle_timeout)
        while not server.should_exit:
            time.sleep(1)
            if should_exit_workbench(service, idle_limit):
                server.should_exit = True
                break
        service.closed = True  # end presence streams during a console or API shutdown

    threading.Thread(target=ready, daemon=True).start()
    threading.Thread(target=watch_idle, daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
        connection = service.store.root / 'server.json'
        if server_record(root).get('pid') == os.getpid():
            connection.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
