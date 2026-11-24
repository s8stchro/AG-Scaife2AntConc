"""ASGI transport for the workbench service. The local launcher binds loopback only."""
import asyncio
import hashlib
import hmac
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from . import APP_NAME, __version__
from .pipeline import SetupError, STEP_NAMES
from .web_service import Workbench, ServiceError
from .web_storage import atomic_json, read_json
from .working_text import Conflict

COOKIE = 'ag_workbench'
MAX_BODY = 48_000_000   # bytes per request, with or without Content-Length


def csrf_for(secret):
    return hmac.new(secret.encode('ascii'), b'workbench-csrf-v1', hashlib.sha256).hexdigest()


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Upload(Body):
    filename: str = Field(min_length=1, max_length=256)
    content: str = Field(max_length=45_000_000)


class OgaLink(Body):
    url: str = Field(min_length=1, max_length=1024)


class Paths(Body):
    paths: list[str] = Field(min_length=1, max_length=100)


class Folder(Body):
    folder: str = Field(min_length=1, max_length=4096)


class MorphoLookup(Body):
    key: str = Field(min_length=1, max_length=4096)
    component: int = Field(default=0, ge=0, le=7)


class AnalysisScope(Body):
    scope: Literal['changed', 'all'] = 'changed'


class Inputs(Body):
    inputs: list[str] = Field(min_length=1, max_length=100)


class RemoveInput(Body):
    id: str | None = Field(default=None, max_length=128)
    all: bool = False


class RunRequest(Inputs):
    folder: str = Field(min_length=1, max_length=4096)
    mode: Literal['quick', 'review'] = 'quick'
    keep: list[str] = Field(default_factory=list, max_length=20)
    accents: bool = False
    morpho: bool = False
    xml: bool = False
    corrections: str | None = Field(default=None, max_length=4096)
    replace: bool = False


class CreateProject(Inputs):
    folder: str = Field(min_length=1, max_length=4096)


class TextEdit(Body):
    passage_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(min_length=64, max_length=64)
    text: str = Field(max_length=50_000)
    reason: str = Field(default='', max_length=2000)
    text_reviewed: bool = False


class TextUndo(Body):
    passage_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(min_length=64, max_length=64)
    revision_id: str = Field(min_length=1, max_length=256)


class CrasisChoice(Body):
    cut: int = Field(strict=True, ge=1)
    forms: list[str] = Field(min_length=2, max_length=2)


class Choice(Body):
    key: str = Field(min_length=1, max_length=4096)
    revision: str = Field(min_length=64, max_length=64)
    lemmas: list[str] = Field(min_length=1, max_length=8)
    tags: list[str] = Field(min_length=1, max_length=8)
    crasis: CrasisChoice | None = None
    scope: Literal['one', 'all'] = 'one'
    view: Literal['uncertain', 'warnings', 'all', 'checked'] = 'uncertain'
    query: str = Field(default='', max_length=500)


class Preferences(Body):
    diogenes: str | None = Field(default=None, max_length=4096)
    greta_model: str | None = Field(default=None, max_length=4096)
    context_model: str | None = Field(default=None, max_length=4096)
    outdir: str | None = Field(default=None, max_length=4096)
    mode: Literal['quick', 'review'] | None = None


def create_app(service=None, *, origin='http://127.0.0.1:8766', secret=None, shutdown=None):
    service = service or Workbench()
    if secret is None:
        path = service.store.root / 'authentication.json'
        values = read_json(path, {})
        secret = values.get('secret')
        if not isinstance(secret, str) or len(secret) < 40:
            secret = secrets.token_urlsafe(40)
            atomic_json(path, {'secret': secret})
    expected_host = urlsplit(origin).netloc.lower()
    csrf = csrf_for(secret)
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.workbench, app.state.secret, app.state.csrf = service, secret, csrf
    assets = Path(__file__).with_name('web_assets')

    class _BodyTooLarge(Exception):
        pass

    @app.middleware('http')
    async def local_session(request: Request, call_next):
        if request.headers.get('host', '').lower() != expected_host:
            return JSONResponse({'error': 'Invalid application host.'}, status_code=421)
        supplied_origin = request.headers.get('origin')
        if supplied_origin and supplied_origin != origin:
            return JSONResponse({'error': 'Request originated from another site.'}, status_code=403)
        try:
            length = int(request.headers.get('content-length', '0') or 0)
        except ValueError:
            return JSONResponse({'error': 'Invalid request.'}, status_code=400)
        if length < 0 or length > MAX_BODY:
            return JSONResponse({'error': 'Request payload is too large.'}, status_code=413)
        if request.url.path.startswith('/api/') and request.url.path not in ('/api/health', '/api/session'):
            bearer = request.headers.get('authorization', '')
            authenticated = (hmac.compare_digest(request.cookies.get(COOKIE, ''), secret) or
                             hmac.compare_digest(bearer, 'Bearer ' + secret))
            if not authenticated:
                return JSONResponse({'error': 'Open AG-Scaife2AntConc from Start-Workbench.bat or the Windows Start Menu to connect.', 'code': 'session_required'}, status_code=401)
            if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('x-ag-csrf', ''), csrf):
                return JSONResponse({'error': 'Please refresh your connection to the application.'}, status_code=403)
        too_large = False
        if request.method not in ('GET', 'HEAD'):
            seen_bytes = 0
            orig_receive = request._receive
            async def bounded_receive():
                nonlocal seen_bytes, too_large
                message = await orig_receive()
                seen_bytes += len(message.get('body', b''))
                if seen_bytes > MAX_BODY:
                    too_large = True
                    raise _BodyTooLarge()
                return message
            request._receive = bounded_receive
        if request.url.path != '/api/health':
            service.last_activity = time.monotonic()
        try:
            response = await call_next(request)
        except _BodyTooLarge:
            too_large = True
        # FastAPI turns an exception raised while it parses a body into its own 400 response,
        # so a streamed body over the limit is recognised here rather than by the exception.
        if too_large:
            return JSONResponse({'error': 'Request payload is too large.'}, status_code=413)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_request, error):
        return JSONResponse({'error': str(error), 'code': error.code}, status_code=error.status)

    @app.exception_handler(Conflict)
    async def working_conflict(_request, error):
        return JSONResponse({'error': str(error), 'code': 'revision_conflict'}, status_code=409)

    @app.exception_handler(SetupError)
    @app.exception_handler(ValueError)
    async def input_error(_request, error):
        return JSONResponse({'error': str(error), 'code': 'input_error'}, status_code=400)

    @app.exception_handler(OSError)
    async def os_error(_request, _error):
        return JSONResponse({'error': 'Could not access local files. Check the folder path and permissions.', 'code': 'io_error'}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        return JSONResponse({'error': 'Please check the request fields and try again.', 'code': 'validation_error'}, status_code=422)

    @app.get('/')
    def index():
        return FileResponse(assets / 'index.html', media_type='text/html')

    _ALLOWED_ASSETS = frozenset((
        'app.js', 'state.js', 'api.js', 'crasis.js', 'jobs.js',
        'results.js', 'review.js', 'text_editor.js', 'projects.js', 'windows.js',
        'app.css', 'favicon.svg', 'GentiumPlus-Regular.woff2', 'GentiumPlus-OFL.txt',
    ))

    @app.get('/assets/{name}')
    def asset(name: str):
        if name not in _ALLOWED_ASSETS:
            raise ServiceError('File not found.', 404)
        # Explicit JS/CSS media types prevent Windows registry text/plain MIME rejections for ES modules.
        media = ('text/javascript; charset=utf-8' if name.endswith('.js') else
                 'text/css; charset=utf-8' if name.endswith('.css') else
                 'font/woff2' if name.endswith('.woff2') else None)
        return FileResponse(assets / name, media_type=media)

    @app.get('/api/health')
    def health():
        return {'application': APP_NAME, 'version': __version__, 'instance': hashlib.sha256(secret.encode()).hexdigest(),
                'ready': not service.closed}

    @app.post('/api/session')
    def authenticate(request: Request):
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + secret):
            raise ServiceError('Session was not recognized. Please relaunch the application.', 401)
        response = JSONResponse({'connected': True})
        response.set_cookie(COOKIE, secret, httponly=True, samesite='strict', max_age=60 * 60 * 24 * 30)
        return response

    @app.get('/api/state')
    def state():
        return dict(service.state(), csrf=csrf,
                    steps={step: names[1][:1].upper() + names[1][1:] for step, names in STEP_NAMES.items()})

    @app.get('/api/windows/{window_id}')
    async def window_connection(window_id: UUID):
        # A live connection survives background-tab timer throttling. Disconnect
        # releases it even if the window is closed without running page scripts.
        async def presence():
            connection = object()
            with service.lock:
                if service.closed:
                    return
                service.windows[window_id] = connection
            try:
                yield ': connected\n\n'
                while not service.closed:
                    await asyncio.sleep(1)
                    yield ': open\n\n'
            finally:
                with service.lock:
                    if service.windows.get(window_id) is connection:
                        del service.windows[window_id]
                        service.last_activity = time.monotonic()
        return StreamingResponse(presence(), media_type='text/event-stream')

    @app.post('/api/windows/{window_id}/quit')
    def quit_window(window_id: UUID):
        with service.lock:
            service.windows.pop(window_id, None)
            if service.windows:
                return {'status': 'other_windows'}
            if service.active_job():
                service.quit_when_finished = True
                return {'status': 'finishing_job'}
            service.closed = True
            if shutdown:
                shutdown()
            return {'status': 'closed'}

    @app.post('/api/inputs/upload')
    def upload(body: Upload):
        return service.upload(body.filename, body.content)

    @app.post('/api/inputs/oga')
    def import_oga(body: OgaLink):
        return service.import_oga(body.url)

    @app.post('/api/inputs/local')
    def local_paths(body: Paths):
        if not service.bridge or not service.bridge.enabled:
            raise ServiceError('Local file picker is not available.', 404)
        return service.add_paths(body.paths)

    @app.post('/api/inputs/remove')
    def remove_inputs(body: RemoveInput):
        return service.remove_inputs(body.id, clear_all=body.all)

    @app.post('/api/preview')
    def preview(body: Inputs):
        return service.preview(body.inputs)

    @app.post('/api/preflight')
    def preflight(body: RunRequest):
        with service.lock:
            return service.preflight(body.model_dump())[0]

    @app.post('/api/jobs')
    def start(body: RunRequest):
        return service.start(body.model_dump())

    @app.get('/api/jobs/{ident}')
    def job(ident: str, after: int = 0):
        return service.job(ident, max(0, after))

    @app.post('/api/jobs/{ident}/cancel')
    def cancel(ident: str):
        return service.cancel(ident)

    @app.post('/api/projects/open')
    def open_project(body: Folder):
        return service.open_project(body.folder)

    @app.post('/api/projects/create')
    def create_project(body: CreateProject):
        return service.create_project(body.model_dump())

    @app.get('/api/projects/{ident}')
    def project(ident: str):
        with service.lock:
            project = service.project(ident)
            service.remember(project)
            return project.public()

    @app.post('/api/projects/{ident}/forget')
    def forget_project(ident: str):
        return service.forget_project(ident)

    @app.get('/api/projects/{ident}/entries')
    def entries(ident: str, view: str = 'uncertain', q: str = '', offset: int = 0, limit: int = 100, around: str | None = None):
        if offset < 0 or not 1 <= limit <= 200 or len(q) > 500:
            raise ServiceError('Invalid pagination or search query.')
        return service.entries(ident, view, q, offset, limit, around)

    @app.get('/api/projects/{ident}/entry/{key}')
    def detail(ident: str, key: str):
        return service.detail(ident, key)

    @app.post('/api/projects/{ident}/lookup/morpho')
    def lookup_morpho(ident: str, body: MorphoLookup):
        return service.lookup_morpho(ident, body.key, body.component)

    @app.post('/api/projects/{ident}/choice')
    def choice(ident: str, body: Choice):
        return service.save_choice(ident, body.model_dump())

    @app.get('/api/projects/{ident}/passage/{key}')
    def passage(ident: str, key: str):
        return service.passage(ident, key)

    @app.get('/api/projects/{ident}/passages/{passage_id}')
    def passage_by_id(ident: str, passage_id: str):
        return service.passage_by_id(ident, passage_id)

    @app.get('/api/projects/{ident}/passages')
    def passages(ident: str):
        return service.passages(ident)

    @app.post('/api/projects/{ident}/text')
    def edit_text(ident: str, body: TextEdit):
        return service.revise_text(ident, body.model_dump())

    @app.post('/api/projects/{ident}/text/undo')
    def undo_text(ident: str, body: TextUndo):
        return service.revise_text(ident, body.model_dump(), undo=True)

    @app.post('/api/projects/{ident}/analyse')
    def analyse_text(ident: str, body: AnalysisScope):
        return service.analyse_text(ident, body.scope)

    @app.post('/api/projects/{ident}/export')
    def reexport(ident: str):
        return service.reexport(ident)

    @app.post('/api/projects/{ident}/headwords')
    def standardize_headwords(ident: str):
        return service.standardize_headwords(ident)

    @app.get('/api/projects/{ident}/download/{kind}')
    def download(ident: str, kind: str):
        path, filename = service.download(ident, kind)
        downloads_root = (service.store.root / 'downloads').resolve()
        is_temp_zip = Path(path).resolve().is_relative_to(downloads_root)
        cleanup = BackgroundTask(lambda p: p.unlink(missing_ok=True), Path(path)) if is_temp_zip else None
        return FileResponse(path, filename=filename, background=cleanup)

    @app.post('/api/projects/{ident}/open-folder')
    def open_folder(ident: str):
        if not service.bridge or not service.bridge.enabled:
            raise ServiceError('Opening local folders is not available.', 404)
        with service.lock:
            service.bridge.open_folder(service.project(ident).folder)
        return {'opened': True}

    @app.post('/api/local/choose/{kind}')
    def choose(kind: str):
        return {'value': service.choose(kind)}

    @app.post('/api/preferences')
    def preferences(body: Preferences):
        return service.preferences(body.model_dump(exclude_unset=True))

    @app.post('/api/shutdown')
    def close():
        with service.lock:
            if service.active_job():
                raise ServiceError('An analysis job is running. You can close this window and return later, '
                                   'or request cancellation first.', 409, 'busy')
            service.closed = True
            if shutdown:
                shutdown()
        return {'closed': True}

    return app
