"""Workbench application service: no HTTP/UI imports and no new linguistic decisions."""
import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from . import catalog, oga, __version__
from .file_io import keep_name
from .pipeline import (LEMMA_FOLDER, Options, SetupError, expected_results, input_work, oga_options, old_results, plan,
                       unsaved_corrections)
from .preparation import scan
from .reader import STEPS
from .session import Saved, file_digest
from .web_storage import Store, atomic_json, read_json
from .web_worker import digest_or_none, process_identity
from .workspace import Workspace, POS_NAMES, identity

ACTIVE = {'queued', 'running', 'publishing'}
VIEWS = {'uncertain', 'warnings', 'all', 'checked'}


class ServiceError(Exception):
    def __init__(self, message, status=400, code='invalid_request'):
        super().__init__(message)
        self.status, self.code = status, code


def entry_key(record):
    value = json.dumps(identity(record), ensure_ascii=False).encode('utf-8')
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def entry_identity(key):
    try:
        value = json.loads(base64.urlsafe_b64decode(key + '=' * (-len(key) % 4)))
        if (not isinstance(value, list) or len(value) != 3 or
                not all(isinstance(s, str) for s in value[:2]) or type(value[2]) is not int):
            raise ValueError()
        return tuple(value)
    except (ValueError, TypeError, UnicodeError) as error:
        raise ServiceError('Could not recognize the word position.', 404) from error


class LocalBridge:
    """Optional local-machine adapter. A hosted deployment must replace/disable it."""
    enabled = True

    def choose(self, kind):
        if kind not in ('folder', 'files', 'corrections'):
            raise ServiceError('Invalid selection.')
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-m',
                                 'ag_scaife2antconc.local_dialog', kind],
                                capture_output=True, encoding='utf-8',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ServiceError('Could not open the file chooser. You can upload files or paste the path instead.')
        return json.loads(result.stdout)

    def open_folder(self, folder):
        if hasattr(os, 'startfile'):
            os.startfile(str(folder))
        else:
            import webbrowser
            webbrowser.open(Path(folder).as_uri())


class Project:
    def __init__(self, info):
        self.info = info
        self.folder = Path(info['folder'])
        self.workspace, self.saved = None, None
        self.evidence_stamp, self.revision = None, None
        self.summary = {}

    def refresh(self):
        from .working_text import WorkingText
        if WorkingText.exists(self.folder):
            path = self.folder / 'working-text.json'
            stat = path.stat()
            stamp = ('working', stat.st_mtime_ns, stat.st_size)
            if stamp != self.evidence_stamp:
                # A successful choice already updated this model and its hash.
                # Reuse it only when the durable file matches; external edits
                # still reload the edition and participate in conflict checks.
                if not isinstance(self.workspace, WorkingText) or file_digest(path) != self.workspace.revision:
                    self.workspace = WorkingText.load(self.folder)
                else:
                    self.workspace._validate_sources()
                self.evidence_stamp = stamp
                self.revision = self.workspace.revision
                self.saved = None
            self.workspace._check()
            self.recount()
            return self
        evidence = self.folder / 'analysis-review.jsonl'
        try:
            stat = evidence.stat()
        except OSError as error:
            raise ServiceError('The output folder is not available.', 404) from error
        stamp = stat.st_mtime_ns, stat.st_size
        changed = stamp != self.evidence_stamp
        if changed:
            self.workspace = Workspace(self.folder)
            try:
                self.saved = Saved.load(self.folder)
            except SetupError:
                self.saved = None
            self.evidence_stamp = stamp
        revision = file_digest(self.workspace.sheet)
        if changed or revision != self.revision:
            from . import review
            self.workspace.corrections = review.read(self.workspace.sheet)
            self.revision = revision
            self.recount()
        return self

    def recount(self):
        from .working_text import WorkingText
        w = self.workspace
        working = isinstance(w, WorkingText)
        counts = {'uncertain': 0, 'warnings': 0, 'all': len(w.records), 'checked': 0}
        pending = False
        for r in w.records:
            checked = w.checked(r)
            counts['checked'] += int(checked)
            if not checked:
                if w.priorities[identity(r)]:
                    counts['uncertain'] += 1
                elif r['review_reasons']:
                    counts['warnings'] += 1
            if (w.values(r) != (r['lemma'], r['pos']) or checked != bool(r.get('reviewed')) or
                    w.manual_crasis(r) != r.get('manual_crasis')):
                pending = True
        manifest = read_json(self.folder / 'working-exports' / 'current.json', {}) if working else {}
        if working:
            pending = manifest.get('revision') != w.revision
        self.summary = {'counts': counts, 'pending': pending,
                        'provider': self.saved.provider if self.saved else None,
                        'expanded': sum(len(w.expansion(r)) for r in w.records),
                        'published_checked': sum(bool(r.get('reviewed')) for r in w.records),
                        'has_saved_analysis': self.saved is not None,
                        'can_reexport': working or bool(self.saved and all(Path(p).is_file() for p in self.saved.paths)),
                        'working_text': working,
                        'can_edit_text': working or bool(self.saved and all(Path(p).is_file() for p in self.saved.paths))}
        if working:
            sources = w.data.get('sources', [])
            self.summary.update(
                provider='oga' if sources and all(s['kind'] == 'oga' for s in sources) else None,
                text_revision=w.revision,
                text_changes_count=sum(e.get('kind') in ('text', 'undo') and e.get('old_text') != e.get('new_text')
                                       for p in w.data['passages'] for e in p.get('history', [])),
                stale_annotations=sum(bool(r.get('stale') or r.get('text_stale') or r.get('context_stale')) for r in w.records),
                exports_current=not pending,
                export_fidelity=manifest.get('fidelity'), export_warnings=manifest.get('warnings', []),
                published_checked=manifest.get('counts', {}).get('reviewed', 0))

    def public(self):
        return dict(self.info, **self.summary, revision=self.revision)

    def record(self, key):
        record = self.workspace.by_id.get(entry_identity(key))
        if record is None:
            raise ServiceError('Occurrence not found.', 404)
        return record

    def brief(self, record):
        lemma, pos = self.workspace.values(record)
        return {'key': entry_key(record), 'form': record['form'], 'work': record['work'],
                'citation': record['citation'], 'lemma': lemma, 'pos': pos,
                'checked': self.workspace.checked(record),
                'priority': bool(self.workspace.priorities[identity(record)])}

    def detail(self, record):
        return dict(self.brief(record), revision=self.revision, expansion=self.workspace.expansion(record),
                    manual_crasis=self.workspace.manual_crasis(record),
                    context=self.workspace.contexts[identity(record)],
                    priorities=self.workspace.priorities[identity(record)],
                    observations=record['review_reasons'], readings=record['readings'],
                    rule_contributions=record.get('rule_contributions', []),
                    uid=record.get('uid'), text_stale=bool(record.get('stale') or record.get('text_stale')),
                    context_stale=bool(record.get('context_stale')),
                    prior_annotations=record.get('prior_annotations', []),
                    machine={'lemma': record.get('working_machine', record).get('lemma', ''),
                             'pos': record.get('working_machine', record).get('pos', 'X'),
                             'expansion': record.get('machine_expansion', record['expansion']),
                             'before_correction': record.get('machine_before_correction'), 'msd': record.get('msd')})


class Workbench:
    def __init__(self, root=None, *, local_bridge=None, spawn=None):
        self.store, self.lock = Store(root), threading.RLock()
        self.bridge = local_bridge
        self.spawn = spawn or subprocess.Popen
        self.projects, self.processes = {}, {}
        self.reconciled_jobs = set(self.store.data.get('reconciled_jobs', []))
        self.picker_lock = threading.Lock()
        self.morpho_lock = threading.Lock()
        self.closed = False
        self.windows = {}
        self.quit_when_finished = False
        self.last_activity = time.monotonic()
        downloads = self.store.root / 'downloads'
        if downloads.is_dir():
            cutoff = time.time() - 3600
            for item in downloads.glob('*.zip'):
                try:
                    if item.stat().st_mtime < cutoff:
                        item.unlink(missing_ok=True)
                except OSError:
                    pass
        if not self.store.path.exists() and not self.store.data['projects']:
            folder = self.store.data['preferences'].get('outdir')
            if folder and (Path(folder) / 'analysis-review.jsonl').is_file():
                try:
                    self.open_project(folder)
                except (SetupError, ServiceError, OSError, ValueError):
                    pass

    def project(self, ident):
        info = next((p for p in self.store.data['projects'] if p['id'] == ident), None)
        if not info:
            raise ServiceError('Project not found.', 404)
        publishing = self.active_job(info['folder'])
        if publishing and publishing['state'] == 'publishing':
            raise ServiceError('Files are still being saved. Please try again in a moment.', 409, 'busy')
        if ident not in self.projects:
            self.projects[ident] = Project(info)
        return self.projects[ident].refresh()

    def remember(self, project):
        project.info.update(last_opened=time.time(), counts=project.summary['counts'])
        self.store.data['last_project'] = project.info['id']
        self.store.save()

    def open_project(self, folder):
        with self.lock:
            folder = str(Path(folder).expanduser().resolve())
            publishing = self.active_job(folder)
            if publishing and publishing['state'] == 'publishing':
                raise ServiceError('Files are still being saved. Please try again in a moment.', 409, 'busy')
            info = next((p for p in self.store.data['projects'] if os.path.normcase(p['folder']) == os.path.normcase(folder)), None)
            if info is None:
                # Validate before registering a new location.
                from .working_text import WorkingText
                WorkingText.load(folder) if WorkingText.exists(folder) else Workspace(folder)
                info = {'id': uuid.uuid4().hex, 'folder': folder, 'title': Path(folder).name, 'last_opened': time.time()}
                self.store.data['projects'].append(info)
            project = self.project(info['id'])
            self.remember(project)
            return project.public()

    def job_states(self):
        result = []
        for path in (self.store.root / 'jobs').glob('*/status.json'):
            try:
                status = read_json(path)
                if status['state'] in ACTIVE:
                    process = self.processes.get(status['id'])
                    dead = process is not None and process.poll() is not None
                    if status.get('process_identity') is not None and status.get('pid'):
                        dead = dead or process_identity(status['pid']) != status['process_identity']
                    if not status.get('process_identity') and process is None:
                        dead = time.time() - status.get('heartbeat', status['created']) > 180
                    if dead:
                        status = read_json(path)  # completion can race the process-exit check
                        if status['state'] in ACTIVE:
                            status.update(state='interrupted', phase='Job interrupted', finished=time.time(),
                                          error='The job stopped before finishing. Any choices already saved are preserved.')
                            atomic_json(path, status)
                result.append(status)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(result, key=lambda j: j['created'], reverse=True)

    def active_job(self, folder=None):
        return next((j for j in self.job_states() if j['state'] in ACTIVE and
                     (folder is None or os.path.normcase(j['folder']) == os.path.normcase(str(folder)))), None)

    def state(self):
        with self.lock:
            jobs = self.job_states()
            changed = False
            for job in reversed(jobs):
                if job['state'] != 'done' or job['id'] in self.reconciled_jobs:
                    continue
                self.reconciled_jobs.add(job['id'])
                changed = True
                folder = str(Path(job['folder']).resolve())
                if (not any(os.path.normcase(p['folder']) == os.path.normcase(folder) for p in self.store.data['projects']) and
                        ((Path(folder) / 'analysis-review.jsonl').is_file() or (Path(folder) / 'working-text.json').is_file())):
                    info = {'id': uuid.uuid4().hex, 'folder': folder, 'title': Path(folder).name,
                            'last_opened': job.get('finished', job['created'])}
                    self.store.data['projects'].append(info)
                    self.store.data['last_project'] = info['id']
            if changed:
                self.store.data['reconciled_jobs'] = sorted(self.reconciled_jobs)
                self.store.save()
            return {'version': __version__, 'local': bool(self.bridge and self.bridge.enabled),
                    'preferences': self.store.data['preferences'], 'inputs': self.store.data['inputs'],
                    'projects': sorted(self.store.data['projects'], key=lambda p: p['last_opened'], reverse=True),
                    'last_project': self.store.data['last_project'], 'jobs': jobs[:15],
                    'last_inputs': self.store.data.get('last_inputs', []),
                    'active_job': next((j for j in jobs if j['state'] in ACTIVE), None),
                    'pos_names': POS_NAMES}

    def add_paths(self, paths):
        with self.lock:
            added = []
            inputs = list(self.store.data['inputs'])
            for raw in paths:
                path = str(Path(raw).expanduser().resolve())
                existing = next((i for i in inputs if os.path.normcase(i['path']) == os.path.normcase(path)), None)
                if existing:
                    added.append(existing)
                    continue
                try:
                    work = input_work(path)
                except (ET.ParseError, OSError, ValueError) as error:
                    raise ServiceError(f'Could not read "{Path(path).name}": {error}') from error
                entry = catalog.lookup(work.ident)
                item = {'id': uuid.uuid4().hex, 'path': path, 'name': Path(path).name,
                        'title': entry.display if entry else work.title or work.ident or Path(path).stem,
                        'units': len(work.lines), 'kind': 'oga' if oga.is_oga(path) else 'xml'}
                inputs.append(item)
                added.append(item)
            self.store.data['inputs'] = inputs
            self.store.data['last_inputs'] = [i['id'] for i in added]
            self.store.save()
            return added

    def remove_inputs(self, ident=None, *, clear_all=False):
        with self.lock:
            if clear_all:
                self.store.data['inputs'] = []
                self.store.data['last_inputs'] = []
            elif ident:
                self.store.data['inputs'] = [i for i in self.store.data['inputs'] if i['id'] != ident]
                self.store.data['last_inputs'] = [i for i in self.store.data.get('last_inputs', []) if i != ident]
            self.store.save()
            return {'inputs': self.store.data['inputs']}

    def forget_project(self, ident):
        with self.lock:
            self.state()  # Record completed jobs before removing their recent-project entry.
            self.store.data['projects'] = [p for p in self.store.data['projects'] if p['id'] != ident]
            if self.store.data.get('last_project') == ident:
                self.store.data['last_project'] = self.store.data['projects'][0]['id'] if self.store.data['projects'] else None
            self.projects.pop(ident, None)
            self.store.save()
            return {'removed': ident}

    def upload(self, filename, encoded):
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise ServiceError('Could not decode the uploaded file.') from error
        return self.store_input(filename, content)

    def import_oga(self, link):
        return self.store_input(*oga.fetch(link))

    def store_input(self, filename, content):
        source = Path(filename)
        suffix = source.suffix.lower()
        if suffix not in ('.xml', '.conllu'):
            raise ServiceError('Select an XML file or an OGA (.conllu) file.')
        if not content or len(content) > oga.MAX_BYTES:
            raise ServiceError('Files must be non-empty and at most 32 MB.')
        name = keep_name(source.stem, 140) + suffix   # results are named after the uploaded file
        directory = self.store.root / 'inputs' / uuid.uuid4().hex
        directory.mkdir(parents=True)
        path = directory / name
        try:
            path.write_bytes(content)
            return self.add_paths([path])[0]
        except Exception:
            path.unlink(missing_ok=True)
            directory.rmdir()
            raise

    def paths(self, identifiers):
        if not identifiers:
            raise ServiceError('Add at least one text.')
        items = {i['id']: i for i in self.store.data['inputs']}
        try:
            return [items[i]['path'] for i in dict.fromkeys(identifiers)]
        except KeyError as error:
            raise ServiceError('A selected text was not found. Please add it again.') from error

    def options(self, request):
        quick = request.get('mode', 'quick') == 'quick'
        keep = request.get('keep', [])
        if any(k not in STEPS for k in keep):
            raise ServiceError('Invalid cleansing option.')
        prefs = self.store.data['preferences']
        return Options(session=True, morphology=True, provenance=True,
                       keep=frozenset() if quick else frozenset(keep),
                       accents=False if quick else request.get('accents', False),
                       xml=False if quick else request.get('xml', False),
                       corrections=None if quick else request.get('corrections') or None,
                       diogenes=prefs.get('diogenes') or None, greta_model=prefs.get('greta_model') or None,
                       context_model=prefs.get('context_model') or None,
                       replace=request.get('replace', False))

    def preflight(self, request):
        paths = self.paths(request['inputs'])
        folder = request.get('folder', '').strip()
        if not folder:
            raise ServiceError('Choose an output folder.')
        folder = str(Path(folder).expanduser().resolve())
        opts = self.options(request)
        if all(oga.is_oga(path) for path in paths):
            opts = oga_options(opts, folder)
        pending = unsaved_corrections(folder, opts)
        if pending:
            opts.corrections = pending  # always retain already saved choices
        works = plan(paths, opts.keep)
        obsolete = old_results(folder, expected_results(works, opts))
        return {'folder': folder, 'obsolete': obsolete, 'uses_corrections': bool(opts.corrections)}, paths, opts

    def preview(self, identifiers):
        paths = self.paths(identifiers)
        if any(oga.is_oga(path) for path in paths):
            raise ServiceError('Cleansing options apply to XML files only.')
        return {step: {'count': n, 'words': words, 'examples': examples}
                for step, (n, words, examples) in scan(paths).items()}

    def launch(self, paths, folder, opts, *, replay=False, mode='quick', operation=None, analysis_scope='changed'):
        if self.closed or (self.quit_when_finished and not self.windows):
            raise ServiceError('The workbench is closing. Reopen it to start another job.', 409, 'closing')
        if self.active_job():
            raise ServiceError('An analysis job is already running. Wait for it to finish.', 409, 'busy')
        self.quit_when_finished = False
        ident = uuid.uuid4().hex
        directory = self.store.root / 'jobs' / ident
        directory.mkdir(parents=True)
        status = {'id': ident, 'folder': str(folder), 'mode': mode, 'replay': replay,
                  'operation': operation,
                  'provider': 'oga' if paths and oga.is_oga(paths[0]) else None,
                  'state': 'queued', 'phase': 'Preparing…', 'created': time.time(), 'sequence': 0}
        protected = {name: digest_or_none(Path(folder) / name) for name in ('lemma-review.tsv', 'analysis-review.jsonl', 'working-text.json')}
        values = dict(asdict(opts), keep=sorted(opts.keep))
        atomic_json(directory / 'request.json', {'status': status, 'folder': str(folder), 'paths': paths,
                                               'options': values, 'protected': protected, 'replay': replay,
                                               'operation': operation, 'analysis_scope': analysis_scope})
        atomic_json(directory / 'status.json', status)
        try:
            with (directory / 'process.log').open('wb') as log:
                process = self.spawn([sys.executable, '-X', 'utf8', '-B', '-u', '-m',
                                      'ag_scaife2antconc.web_worker', str(directory)],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes[ident] = process
        except OSError as error:
            status.update(state='failed', error=str(error))
            atomic_json(directory / 'status.json', status)
            raise ServiceError('Could not start the analysis job.') from error
        return status

    def start(self, request):
        with self.lock:
            from .working_text import WorkingText
            if WorkingText.exists(request.get('folder', '')):
                raise ServiceError('This folder already has a working edition and history. Open the project and run text analysis from inside it.',
                                   409, 'working_project_exists')
            info, paths, opts = self.preflight(request)
            if info['obsolete'] and not opts.replace:
                raise ServiceError('Previous outputs exist in this folder and require confirmation before replacement.', 409, 'replace_required')
            job = self.launch(paths, info['folder'], opts, mode=request.get('mode', 'quick'))
            self.store.data['preferences'].update(outdir=info['folder'], mode=request.get('mode', 'quick'))
            self.store.data['last_inputs'] = []
            self.store.save()
            return job

    def entries(self, ident, view='uncertain', query='', offset=0, limit=100, around=None):
        if view not in VIEWS:
            raise ServiceError('Invalid view.')
        with self.lock:
            project = self.project(ident)
            records = project.workspace.visible(query, view)
            if around:
                selected = entry_identity(around)
                index = next((i for i, r in enumerate(records) if identity(r) == selected), None)
                if index is not None:
                    offset = index // limit * limit
            if offset >= len(records):
                offset = max(0, (len(records) - 1) // limit * limit)
            return {'project': project.public(), 'total': len(records), 'offset': offset,
                    'entries': [project.brief(r) for r in records[offset:offset + limit]]}

    def detail(self, ident, key):
        with self.lock:
            project = self.project(ident)
            return project.detail(project.record(key))

    def lookup_morpho(self, ident, key, component=0):
        from ag_diogenes_spacy_txt2xml.morpho import Morpho, SITE
        from urllib.parse import quote
        with self.lock:
            project = self.project(ident)
            forms = project.workspace.expansion(project.record(key))
            if not 0 <= component < len(forms):
                raise ServiceError('Expansion word not found.', 404)
            form = forms[component]
        if not self.morpho_lock.acquire(blocking=False):
            raise ServiceError('A Morpho lookup is already running. Please wait a moment.', 409, 'busy')
        try:
            provider = Morpho()
            provider.ask([form])
            evidence = provider.evidence(form)
            for entry in evidence:
                entry['raw_response'] = provider.records[entry['query']]['response']
            return {'provider': 'Morpho / Logeion', 'form': form, 'lemmas': list(provider.lemmas(form)),
                    'verification': 'not established', 'evidence': evidence, 'error': provider.failed,
                    'url': SITE+quote(form), 'selection_changed': False}
        finally:
            self.morpho_lock.release()

    def save_choice(self, ident, request):
        with self.lock:
            project = self.project(ident)
            if self.active_job(project.folder):
                raise ServiceError('Project files are currently being updated. Wait for the job to finish.', 409, 'busy')
            if request['revision'] != project.revision:
                raise ServiceError('Choices changed in another tab or in the review sheet. '
                                   'Your draft was kept. Reload the word before saving.', 409, 'revision_conflict')
            record = project.record(request['key'])
            view, query = request.get('view', 'uncertain'), request.get('query', '')
            if view not in VIEWS:
                raise ServiceError('Invalid view.')
            before = project.workspace.visible(query, view)
            positions = [identity(r) for r in before]
            following = set(positions[positions.index(identity(record)) + 1:]) if identity(record) in positions else set()
            project.workspace.choose(record, request['lemmas'], request['tags'], request.get('scope', 'one') == 'all',
                                     crasis=request.get('crasis'))
            project.refresh()
            after = project.workspace.visible(query, view)
            next_record = next((r for r in after if identity(r) in following), None)
            self.remember(project)
            return {'project': project.public(), 'saved': project.brief(record),
                    'next': project.detail(next_record) if next_record else None, 'at_end': next_record is None}

    def reexport(self, ident):
        with self.lock:
            project = self.project(ident)
            if project.summary.get('working_text'):
                return self.launch([], project.folder, Options(), operation='working-export', mode='review')
            if not project.summary['can_reexport']:
                raise ServiceError('Re-exporting requires the saved analyses and the original source texts in their original locations.')
            return self.launch(project.saved.paths, project.folder, project.saved.export_options(project.folder), replay=True, mode='review')

    def create_project(self, request):
        from .working_text import WorkingText
        with self.lock:
            if self.active_job():
                raise ServiceError('Wait for the active job to finish.', 409, 'busy')
            folder = request.get('folder', '').strip()
            if not folder:
                raise ServiceError('Choose a new project folder.')
            WorkingText.create(folder, self.paths(request['inputs']), self.options(dict(request, mode='quick')))
            self.store.data['last_inputs'] = []
            self.store.save()
            return self.open_project(folder)

    def ensure_working(self, project):
        from .working_text import WorkingText
        if self.active_job(project.folder):
            raise ServiceError('Wait for the project update to finish.', 409, 'busy')
        if not project.summary.get('working_text'):
            WorkingText.ensure(project.folder)
            project.evidence_stamp = None
            project.refresh()
        return project

    def passage(self, ident, key):
        with self.lock:
            project = self.ensure_working(self.project(ident))
            record = project.record(key)
            return {'project': project.public(), 'passage': project.workspace.passage_for(record)}

    def passage_by_id(self, ident, passage_id):
        with self.lock:
            project = self.ensure_working(self.project(ident))
            return {'project': project.public(), 'passage': project.workspace.passage(passage_id)}

    def passages(self, ident):
        with self.lock:
            project = self.ensure_working(self.project(ident))
            return {'project': project.public(), 'passages': [
                {key: p.get(key) for key in ('id', 'work', 'citation', 'text', 'original_text', 'text_reviewed')}
                for p in project.workspace.data['passages']]}

    def revise_text(self, ident, request, *, undo=False):
        with self.lock:
            project = self.ensure_working(self.project(ident))
            if request['revision'] != project.revision:
                raise ServiceError('The text or choices changed. Your draft is preserved; reload the passage.',
                                   409, 'revision_conflict')
            model = project.workspace
            if undo:
                model.undo(request['passage_id'], request['revision_id'], expected_revision=request['revision'])
            else:
                model.edit(request['passage_id'], request['text'], reason=request.get('reason', ''),
                           text_reviewed=request.get('text_reviewed', False), expected_revision=request['revision'])
            project.evidence_stamp = None
            project.refresh()
            passage = project.workspace.passage(request['passage_id'])
            row = next((r for r in project.workspace.records if r['work'] == passage['work'] and r['citation'] == passage['citation']), None)
            self.remember(project)
            return {'project': project.public(), 'passage': passage, 'entry': project.detail(row) if row else None}

    def analyse_text(self, ident, scope='changed'):
        with self.lock:
            project = self.ensure_working(self.project(ident))
            values = dict(project.workspace.data.get('options') or {})
            from .pipeline import MODEL
            values.update(keep=frozenset(values.get('keep') or []), corrections=None, replace=False, session=False, model=MODEL)
            prefs = self.store.data['preferences']
            for name in ('diogenes', 'greta_model', 'context_model'):
                if prefs.get(name): values[name] = prefs[name]
            return self.launch([], project.folder, Options(**values), operation='working-analysis', mode='review', analysis_scope=scope)

    def standardize_headwords(self, ident):
        with self.lock:
            project = self.ensure_working(self.project(ident))
            prefs = self.store.data['preferences']
            return self.launch([], project.folder, Options(diogenes=prefs.get('diogenes')),
                               operation='working-headwords', mode='review')

    def job(self, ident, after=0):
        with self.lock:
            status = next((j for j in self.job_states() if j['id'] == ident), None)
            if not status:
                raise ServiceError('Job not found.', 404)
            events = []
            path = self.store.root / 'jobs' / ident / 'events.jsonl'
            if path.exists():
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:
                        event = json.loads(line)
                        if event['sequence'] > after:
                            events.append(event)
                    except (ValueError, KeyError):
                        continue  # a writer can still be appending the final line
            return dict(status, events=events[:500])

    def cancel(self, ident):
        with self.lock:
            status = self.job(ident)
            if status['state'] == 'publishing':
                raise ServiceError('Saving has already started and is completing safely.', 409)
            if status['state'] in ACTIVE:
                (self.store.root / 'jobs' / ident / 'cancel').touch()
            return {'requested': status['state'] in ACTIVE}

    def download(self, ident, kind):
        with self.lock:
            project = self.project(ident)
            if self.active_job(project.folder):
                raise ServiceError('Wait for the file export to finish.', 409)
            if project.summary.get('working_text'):
                return self.download_working(project, kind)
            single = {'review': 'lemma-review.tsv', 'report': 'report.txt', 'evidence': 'analysis-review.jsonl'}
            if kind in single:
                path = project.folder / single[kind]
                if not path.is_file() or not path.resolve().is_relative_to(project.folder.resolve()):
                    raise ServiceError('File not found.', 404)
                return path, path.name
            if kind not in ('tagged', 'lemmata', 'all'):
                raise ServiceError('Invalid download.', 404)
            paths = [] if kind == 'lemmata' else list((project.folder / 'antconc-tagged').glob('*.txt'))
            if kind in ('lemmata', 'all'):
                paths += list((project.folder / LEMMA_FOLDER).glob('*.txt'))
                if kind == 'lemmata' and not paths:
                    raise ServiceError('No lemma files yet: run the analysis or export again to create them.', 404)
            if kind == 'all':
                paths += list((project.folder / 'xml').glob('*.xml'))
                paths += [project.folder / name for name in ('lemma-review.tsv', 'report.txt', 'analysis-review.jsonl', 'analysis-session.json')]
            directory = self.store.root / 'downloads'
            directory.mkdir(parents=True, exist_ok=True)
            output = directory / (uuid.uuid4().hex + '.zip')
            with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
                for path in paths:
                    if path.is_file() and path.resolve().is_relative_to(project.folder.resolve()):
                        archive.write(path, path.relative_to(project.folder).as_posix())
            prefix = {'tagged': 'AntConc-', 'lemmata': 'AntConc-lemmata-'}.get(kind, 'Scaife2AntConc-')
            return output, prefix + project.folder.name + '.zip'

    def download_working(self, project, kind):
        if kind not in ('tagged', 'lemmata', 'xml', 'all', 'working', 'review', 'report', 'evidence'):
            raise ServiceError('Invalid download.', 404)
        directory = self.store.root / 'downloads'
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / (uuid.uuid4().hex + '.zip')
        root = project.folder.resolve()
        # A portable copy must not depend on legacy sheets left in the old location.
        portable = dict(project.workspace.data, legacy_hashes={}, copied_from_revision=project.revision)
        project_bytes = json.dumps(portable, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if kind == 'working':
            paths = [p for p in (root / '.working-text').rglob('*') if p.is_file()]
            # The canonical JSON includes the imported annotations and complete revision history.
            with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
                archive.writestr('working-text.json', project_bytes)
                for path in paths:
                    if not path.resolve().is_relative_to(root):
                        raise ServiceError('Invalid project file path.')
                    archive.write(path, path.relative_to(root).as_posix())
            return output, 'Working-text-' + root.name + '.zip'
        manifest = read_json(root / 'working-exports' / 'current.json', {})
        if manifest.get('revision') != project.revision:
            raise ServiceError('The text or choices changed. Click "Update files" to export the current revision.',
                               409, 'export_outdated')
        files = manifest.get('files', [])
        paths = [root / f for f in files]
        if any(not p.resolve().is_relative_to(root / 'working-exports') or not p.is_file() for p in paths):
            raise ServiceError('Files for the current export were not found.')
        if kind in ('review', 'report', 'evidence'):
            suffix = {'review': 'lemma-review.tsv', 'report': 'report.txt', 'evidence': 'analysis-review.jsonl'}[kind]
            path = next((p for p in paths if p.name == suffix), None)
            if path is None:
                raise ServiceError('That file is not included in this export.', 404)
            return path, path.name
        selected = [p for p in paths if kind == 'all' or (kind == 'tagged' and 'antconc-tagged' in p.parts) or
                    (kind == 'lemmata' and LEMMA_FOLDER in p.parts) or (kind == 'xml' and p.suffix == '.xml')]
        if not selected:
            raise ServiceError('No files of that type exist.', 404)
        version_root = root / 'working-exports' / project.revision
        with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
            for path in selected:
                archive.write(path, path.relative_to(version_root).as_posix())
            if kind == 'all':
                archive.writestr('project/working-text.json', project_bytes)
                for path in (root / '.working-text').rglob('*'):
                    if path.is_file() and path.resolve().is_relative_to(root):
                        archive.write(path, 'project/' + path.relative_to(root).as_posix())
        prefix = {'tagged': 'AntConc-', 'lemmata': 'AntConc-lemmata-', 'xml': 'XML-'}.get(kind, 'Scaife2AntConc-')
        return output, prefix + root.name + '.zip'

    def choose(self, kind):
        if not self.bridge or not self.bridge.enabled:
            raise ServiceError('Local folder selection is not available in this mode.', 404)
        if not self.picker_lock.acquire(blocking=False):
            raise ServiceError('A file chooser is already open.', 409)
        try:
            value = self.bridge.choose(kind)
            return self.add_paths(value) if kind == 'files' and value else value
        finally:
            self.picker_lock.release()

    def preferences(self, values):
        with self.lock:
            if self.active_job():
                raise ServiceError('Resource paths can only be changed after the active analysis finishes.', 409)
            self.store.data['preferences'].update({k: v for k, v in values.items() if k in ('diogenes', 'greta_model', 'context_model', 'outdir', 'mode')})
            self.store.save()
            return self.store.data['preferences']
