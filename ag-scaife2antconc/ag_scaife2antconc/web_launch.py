"""Local startup coordination and narrowly verified recovery of our own server."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import sys
import time
from urllib.parse import urlsplit

from . import APP_NAME
from .pipeline import SetupError


def find_app_browser():
    """Find Microsoft Edge or Google Chrome for standalone --app window mode."""
    if os.name == 'nt':
        roots = [os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'), os.environ.get('LOCALAPPDATA')]
        relatives = (
            r'Microsoft\Edge\Application\msedge.exe',
            r'Google\Chrome\Application\chrome.exe',
        )
        for root in roots:
            if not root:
                continue
            for rel in relatives:
                candidate = Path(root) / rel
                if candidate.is_file():
                    return str(candidate)
    for name in ('msedge', 'microsoft-edge', 'google-chrome', 'chrome', 'chromium', 'chromium-browser'):
        found = shutil.which(name)
        if found:
            return found
    return None


def local_url(value):
    """An on-disk server record must never redirect the launcher to another host."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme == 'http' and parts.hostname == '127.0.0.1' and
                parts.username is None and parts.password is None and
                parts.port is not None and 1024 <= parts.port <= 65535 and
                parts.path in ('', '/') and not parts.query and not parts.fragment):
            return f'http://127.0.0.1:{parts.port}'
    except ValueError:
        pass
    return None


@contextmanager
def startup_lock(root, timeout=20):
    """Serialize authentication creation and server binding across double-clicks."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'startup.lock').open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            until = time.monotonic() + timeout
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if time.monotonic() >= until:
                        raise SetupError('The application is already starting. Please wait a moment and try again.') from error
                    time.sleep(.05)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            until = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as error:
                    if time.monotonic() >= until:
                        raise SetupError('The application is already starting. Please try again shortly.') from error
                    time.sleep(.05)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def owns_record(record, root):
    """File ownership hints alone cannot authorize termination of a reused PID."""
    try:
        return (record['application'] == APP_NAME and type(record['pid']) is int and record['pid'] > 0 and
                type(record['process_identity']) is int and record['process_identity'] > 0 and
                isinstance(record.get('process_image'), str) and bool(record['process_image']) and
                Path(record['state_directory']).resolve() == Path(root).resolve() and
                Path(record['executable']).resolve() == Path(sys.executable).resolve() and
                Path(record['module']).resolve() == Path(__file__).with_name('web.py').resolve())
    except (KeyError, TypeError, ValueError, OSError):
        return False


def process_image():
    """Windows virtual-environment launchers can differ from the running image."""
    if os.name != 'nt':
        return str(Path(sys.executable).resolve())
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    length = wintypes.DWORD(32768)
    image = ctypes.create_unicode_buffer(length.value)
    if not kernel.QueryFullProcessImageNameW(kernel.GetCurrentProcess(), 0, image, ctypes.byref(length)):
        return None
    return str(Path(image.value).resolve())


def terminate_owned_server(record, root):
    """Stop only a verified Windows server, never its detached analysis workers.

    The creation time and executable are checked on the same process handle
    used for termination, so PID reuse cannot target an unrelated process.
    """
    if os.name != 'nt' or not owns_record(record, root):
        return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000 | 0x100000 | 0x0001, False, record['pid'])
    if not handle:
        return False
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return False
        identity = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        if identity != record['process_identity']:
            return False
        length = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(length.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
            return False
        if Path(image.value).resolve() != Path(record['process_image']).resolve():
            return False
        return bool(kernel.TerminateProcess(handle, 1) and kernel.WaitForSingleObject(handle, 5000) == 0)
    finally:
        kernel.CloseHandle(handle)
