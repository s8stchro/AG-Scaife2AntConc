"""Durable local analysis job. Browser/server disconnection does not terminate it."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

from .pipeline import Options, SetupError, run
from .session import Saved, file_digest
from .web_storage import atomic_json, read_json


class Cancelled(Exception):
    pass


def digest_or_none(path):
    return file_digest(path) if Path(path).is_file() else None


def process_identity(pid=None):
    """Windows creation time distinguishes a worker from a later reused PID."""
    pid = pid or os.getpid()
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return None
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def execute(job_dir):
    job_dir = Path(job_dir)
    request = read_json(job_dir / 'request.json')
    status = dict(request['status'], state='running', pid=os.getpid(), process_identity=process_identity(),
                  started=time.time(), heartbeat=time.time(), phase='Loading resources…', sequence=0)
    lock, finished = threading.RLock(), threading.Event()
    committed = False

    def update(**values):
        with lock:
            status.update(values, heartbeat=time.time())
            atomic_json(job_dir / 'status.json', status)

    def heartbeat():
        while not finished.wait(2):
            update()

    def check_cancel():
        if not committed and (job_dir / 'cancel').exists():
            raise Cancelled()

    def log(message):
        check_cancel()
        with lock:
            status['sequence'] += 1
            event = {'sequence': status['sequence'], 'time': datetime.now(timezone.utc).isoformat(), 'message': str(message)}
            with (job_dir / 'events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            update(last_message=str(message))

    def commit():
        nonlocal committed
        check_cancel()
        for relative, expected in request['protected'].items():
            if digest_or_none(Path(request['folder']) / relative) != expected:
                raise SetupError('Results or corrections changed during analysis. '
                                 'No previous files were overwritten. Reopen the folder.')
        committed = True
        update(state='publishing', phase='Saving outputs')

    update()
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        operation = request.get('operation')
        if operation in ('working-export', 'working-analysis', 'working-headwords'):
            from .working_text import WorkingText
            model = WorkingText.load(request['folder'])
            if digest_or_none(Path(request['folder']) / 'working-text.json') != request['protected'].get('working-text.json'):
                raise SetupError('The working text changed before the job started.')
            if operation == 'working-export':
                from .working_export import export_project
                log('Exporting the current text revision and annotations…')
                exported = export_project(model, before_publish=commit)
                update(state='done', phase='Completed', finished=time.time(),
                       export_revision=exported.get('revision'), export_warnings=exported.get('warnings', []))
            elif operation == 'working-headwords':
                from .working_headwords import standardize
                values = request['options']
                values['keep'] = frozenset(values['keep'])
                stats = standardize(model, Options(**values), log=log, before_publish=commit)
                update(state='done', phase='Completed', finished=time.time(), headword_stats=stats)
            else:
                from .working_analysis import analyse
                values = request['options']
                values['keep'] = frozenset(values['keep'])
                analysed = analyse(model, Options(**values), log=log, before_publish=commit,
                                   scope=request.get('analysis_scope', 'changed'))
                update(state='done', phase='Completed', finished=time.time(), analysed=analysed)
            return
        if request['replay']:
            engine = Saved.load(request['folder'])
            options = engine.export_options(request['folder'])
            paths = engine.paths
        else:
            engine = None
            values = request['options']
            values['keep'] = frozenset(values['keep'])
            options = Options(**values)
            paths = request['paths']
        report = run(paths, request['folder'], options, log=log, lemmatizer=engine, before_publish=commit)
        update(state='done', phase='Completed', report=report, finished=time.time())
    except Cancelled:
        update(state='cancelled', phase='Cancelled safely', finished=time.time())
    except Exception as error:
        (job_dir / 'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        update(state='failed', error=str(error), phase='Run failed', finished=time.time())
    finally:
        finished.set()
        thread.join(timeout=4)


if __name__ == '__main__':
    execute(sys.argv[1])
