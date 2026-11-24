import base64
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from ag_scaife2antconc.web_api import create_app
from ag_scaife2antconc.web_service import Workbench, entry_key
from ag_scaife2antconc import oga, web_worker, pipeline
from .test_workspace import corpus, Engine, reexport


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    monkeypatch.setenv('APPDATA', str(tmp_path / 'empty-settings'))
    return Workbench(tmp_path / 'browser-state', spawn=lambda *a, **k: SimpleNamespace(poll=lambda: None))


@pytest.fixture
def client(workbench):
    app = create_app(workbench, origin='http://testserver', secret='a' * 48)
    with TestClient(app) as client:
        assert client.post('/api/session', headers={'Authorization': 'Bearer ' + 'a' * 48}).status_code == 200
        client.headers['X-AG-CSRF'] = app.state.csrf
        yield client


def open_corpus(client, corpus):
    response = client.post('/api/projects/open', json={'folder': str(corpus[1])})
    assert response.status_code == 200, response.text
    return response.json()


def choice(client, project, index=0, **updates):
    rows = client.get(f"/api/projects/{project['id']}/entries?view=all").json()
    detail = client.get(f"/api/projects/{project['id']}/entry/{rows['entries'][index]['key']}").json()
    return dict(key=detail['key'], revision=detail['revision'], lemmas=detail['lemma'].split('+'),
                tags=detail['pos'].split('+'), view='uncertain', scope='one', query='', **updates)


def test_authentication_origin_host_and_csrf_are_checked(workbench):
    app = create_app(workbench, origin='http://testserver', secret='b' * 48)
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        assert client.get('/api/state').status_code == 401
        assert client.post('/api/session', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        reply = client.post('/api/session', headers={'Authorization': 'Bearer ' + 'b' * 48})
        assert 'httponly' in reply.headers['set-cookie'].lower()
        assert 'samesite=strict' in reply.headers['set-cookie'].lower()
        assert client.get('/api/state').status_code == 200
        assert client.post('/api/projects/open', json={'folder': 'anywhere'}).status_code == 403
        assert client.get('/api/state', headers={'Origin': 'https://other.example'}).status_code == 403
        assert client.get('/api/state', headers={'Host': 'other.example'}).status_code == 421


def test_streamed_body_over_the_limit_is_413_not_400(client, monkeypatch):
    from ag_scaife2antconc import web_api
    monkeypatch.setattr(web_api, 'MAX_BODY', 1000)
    chunks = lambda: iter([b'{"folder": "' + b'x' * 600, b'y' * 600 + b'"}'])   # no Content-Length
    assert client.post('/api/projects/open', content=chunks(),
                       headers={'Content-Type': 'application/json'}).status_code == 413
    assert client.post('/api/projects/open', content=b'{"folder": "' + b'x' * 2000 + b'"}',
                       headers={'Content-Type': 'application/json'}).status_code == 413


def test_oga_import_review_and_reexport_without_local_models(client, workbench, tmp_path, monkeypatch):
    source = ('# source = Aristophanes, Ach._84\n'
              '1\tκ\tκαί\tc\t_\t_\t0\t_\t_\tt_1|0|ref=Ach._84\n'
              '2\tᾆτʼ\tεἶτα\td\t_\t_\t1\t_\t_\tt_2|t_1|ref=Ach._84\n'
              '3\tδὲ\tμηδέ\tg\t_\t_\t1\t_\t_\tt_3|t_1|ref=Ach._84\n'
              '4\tλέγει\t_\tv\t_\t_\t1\t_\t_\tt_4|t_1|ref=Ach._84\n'
              '5\tλέγει\tλέγω\tv\t_\t_\t1\t_\t_\te_1|t_1|ref=Ach._84\n'
              '6\t.\t.\tu\t_\t_\t1\t_\t_\tt_5|t_1|ref=Ach._84\n').encode()
    filename = 'tlg0019.tlg001.perseus-grc2_oga020_src.conllu'

    def open_data(url, timeout):
        assert url == oga.DATA + filename
        reply = BytesIO(source)
        reply.headers = {'Content-Length': str(len(source))}
        return reply

    monkeypatch.setattr(oga, 'build_opener', lambda handler: SimpleNamespace(open=open_data))
    monkeypatch.setattr(pipeline, 'Lemmatizer', lambda *args: pytest.fail('local lemmatizer called'))
    invalid = client.post('/api/inputs/oga', json={'url': 'https://example.test/' + filename})
    assert invalid.status_code == 400
    local = client.post('/api/inputs/upload', json={'filename': filename,
                                                   'content': base64.b64encode(source).decode()})
    assert local.status_code == 200, local.text
    remote = client.post('/api/inputs/oga', json={'url': 'https://github.com/mgbilby/oga_src/blob/main/data/' + filename})
    assert remote.status_code == 200, remote.text
    assert remote.json()['kind'] == local.json()['kind'] == 'oga'

    folder = tmp_path / 'results'
    request = {'inputs': [remote.json()['id']], 'folder': str(folder), 'mode': 'review'}
    job = client.post('/api/jobs', json=request)
    assert job.status_code == 200, job.text
    web_worker.execute(workbench.store.root / 'jobs' / job.json()['id'])
    status = client.get('/api/jobs/' + job.json()['id']).json()
    assert status['state'] == 'done', status
    project = open_corpus(client, (None, folder))
    assert project['provider'] == 'oga' and project['counts']['uncertain'] == 1
    endpoint = f"/api/projects/{project['id']}"
    rows = client.get(endpoint + '/entries?view=all').json()['entries']
    assert [(row['citation'], row['form'], row['lemma']) for row in rows] == [
        ('84', 'κ', 'καί'), ('84', "ᾆτ'", 'εἶτα'), ('84', 'δὲ', 'μηδέ'), ('84', 'λέγει', '')]
    entry = choice(client, project, index=2)
    entry.update(lemmas=['δέ'], view='all')
    assert client.post(endpoint + '/choice', json=entry).status_code == 200
    assert client.post('/api/jobs', json=request).status_code == 400
    job = client.post(endpoint + '/export', json={})
    assert job.status_code == 200, job.text
    web_worker.execute(workbench.store.root / 'jobs' / job.json()['id'])
    status = client.get('/api/jobs/' + job.json()['id']).json()
    assert status['state'] == 'done', status
    evidence = [json.loads(line) for line in (folder / 'analysis-review.jsonl').read_text(encoding='utf-8').splitlines()]
    assert evidence[2]['lemma'] == 'δέ' and evidence[2]['reviewed']
    assert evidence[2]['readings'][0]['oga']['lemma'] == 'μηδέ'
    tagged = next((folder / 'antconc-tagged').glob('*.txt')).read_text(encoding='utf-8-sig')
    assert 'δὲ_PART_δέ' in tagged


def test_saved_correction_advances_and_survives_service_restart(client, workbench, corpus):
    project = open_corpus(client, corpus)
    request = choice(client, project)
    request['lemmas'] = ['ἕ']
    response = client.post(f"/api/projects/{project['id']}/choice", json=request)
    assert response.status_code == 200, response.text
    assert response.json()['next']['citation'] == '1'
    assert response.json()['next']['key'] != request['key']
    fresh = Workbench(workbench.store.root)
    reopened = fresh.project(project['id'])
    assert reopened.summary['counts']['checked'] == 1
    assert reopened.workspace.values(reopened.workspace.records[0])[0] == 'ἕ'
    assert reopened.summary['pending']


def test_stale_tab_cannot_overwrite_a_saved_choice(client, corpus):
    project = open_corpus(client, corpus)
    old = choice(client, project)
    first = dict(old, lemmas=['ἕ'])
    assert client.post(f"/api/projects/{project['id']}/choice", json=first).status_code == 200
    before = (corpus[1] / 'lemma-review.tsv').read_bytes()
    second = client.post(f"/api/projects/{project['id']}/choice", json=old)
    assert second.status_code == 409 and second.json()['code'] == 'revision_conflict'
    assert (corpus[1] / 'lemma-review.tsv').read_bytes() == before


def test_manual_crasis_is_saved_and_exported_for_matching_occurrences(client, tmp_path):
    import xml.etree.ElementTree as ET
    from .test_app import tei

    source = tei(tmp_path, '<div n="82"><p>κἄχεζεν κἄχεζεν</p></div>')
    folder = tmp_path / 'manual-crasis'
    pipeline.run([source], str(folder), pipeline.Options(session=True, xml=True, provenance=True),
                 log=lambda message: None, lemmatizer=Engine())
    project = open_corpus(client, (source, folder))
    endpoint = f"/api/projects/{project['id']}"
    request = choice(client, project)
    original_lemmas, original_tags = request['lemmas'], request['tags']
    manual = {'cut': 1, 'forms': ['καί', 'ἔχεζεν']}
    request.update(lemmas=['καί', 'χέζω'], tags=['CCONJ', 'VERB'], crasis=manual, scope='all')
    before = (folder / 'lemma-review.tsv').read_bytes()
    rejected = dict(request, crasis=dict(manual, cut=99))
    assert client.post(endpoint + '/choice', json=rejected).status_code == 400
    assert (folder / 'lemma-review.tsv').read_bytes() == before

    reply = client.post(endpoint + '/choice', json=request)
    assert reply.status_code == 200, reply.text
    detail_url = endpoint + '/entry/' + request['key']
    detail = client.get(detail_url).json()
    assert detail['expansion'] == manual['forms'] and detail['manual_crasis'] == manual
    assert detail['machine']['expansion'] == ['κἄχεζεν']
    assert reply.json()['project']['pending']

    published = reexport(folder)
    assert [r['lemma'] for r in published.records] == ['καί+χέζω', 'καί+χέζω']
    assert all(r['manual_crasis'] == manual and r['resp'] == 'corrector' for r in published.records)
    tagged = next((folder / 'antconc-tagged').glob('*.txt')).read_text(encoding='utf-8-sig')
    assert tagged.count('κ_CCONJ_καί\u200aἄχεζεν_VERB_χέζω') == 2
    xml = ET.parse(next((folder / 'xml').glob('*.xml')))
    parts = xml.findall('.//{*}seg[@type="crasis"]/{*}w')
    assert [(w.text, w.get('lemma'), w.get('resp')) for w in parts[:2]] == [
        ('κ', 'καί', '#corrector'), ('ἄχεζεν', 'χέζω', '#corrector')]

    detail = client.get(detail_url).json()
    reset = dict(request, revision=detail['revision'], crasis=None, scope='one',
                 lemmas=original_lemmas, tags=original_tags)
    assert client.post(endpoint + '/choice', json=reset).status_code == 200
    assert client.get(detail_url).json()['expansion'] == ['κἄχεζεν']
    restored = reexport(folder)
    assert restored.records[0]['lemma'] == original_lemmas[0]
    assert restored.records[1]['manual_crasis'] == manual


@pytest.mark.parametrize('lemma', ['\u1f25', '\u03b7\u0314\u0301', '\u1fa7', '\u03b1\u0313\u0342\u0345'])
def test_polytonic_correction_survives_http_save_reopen_and_export(client, workbench, corpus, lemma):
    from .test_workspace import reexport
    project = open_corpus(client, corpus)
    request = choice(client, project)
    request['lemmas'], request['tags'] = [lemma], ['PRON']
    response = client.post(f"/api/projects/{project['id']}/choice", json=request)
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/projects/{project['id']}/entry/{request['key']}").json()
    assert detail['lemma'] == lemma
    assert lemma.encode('utf-8') in (corpus[1] / 'lemma-review.tsv').read_bytes()
    fresh = Workbench(workbench.store.root).project(project['id'])
    assert fresh.workspace.values(fresh.workspace.records[0])[0] == lemma
    exported = reexport(corpus[1])
    assert exported.records[0]['lemma'] == lemma
    assert any(lemma in p.read_text(encoding='utf-8-sig') for p in (corpus[1] / 'antconc-tagged').glob('*.txt'))


def test_bundled_polytonic_font_is_served_offline_with_its_licence(client):
    response = client.get('/assets/GentiumPlus-Regular.woff2')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'font/woff2'
    assert response.content[:4] == b'wOF2'
    licence = client.get('/assets/GentiumPlus-OFL.txt')
    assert licence.status_code == 200 and 'SIL OPEN FONT LICENSE' in licence.text
    assert client.get('/assets/private.woff2').status_code == 404


def test_failed_choice_does_not_change_the_sheet(client, corpus):
    project = open_corpus(client, corpus)
    request = choice(client, project)
    request['lemmas'] = ['δύο λέξεις']
    before = (corpus[1] / 'lemma-review.tsv').read_bytes()
    assert client.post(f"/api/projects/{project['id']}/choice", json=request).status_code == 400
    assert (corpus[1] / 'lemma-review.tsv').read_bytes() == before


def test_confirmation_stops_at_the_end_of_filtered_results(client, corpus):
    project = open_corpus(client, corpus)
    request = choice(client, project, 2)
    request['query'] = '2'
    response = client.post(f"/api/projects/{project['id']}/choice", json=request)
    assert response.status_code == 200
    assert response.json()['at_end'] and response.json()['next'] is None
    assert response.json()['project']['counts']['checked'] == 1


def test_upload_is_persistent_and_invalid_xml_is_cleaned(client, workbench, corpus):
    content = base64.b64encode(Path(corpus[0]).read_bytes()).decode('ascii')
    response = client.post('/api/inputs/upload', json={'filename': '../../work.xml', 'content': content})
    assert response.status_code == 200, response.text
    path = Path(response.json()['path'])
    assert path.is_relative_to(workbench.store.root / 'inputs') and path.exists()
    fresh = Workbench(workbench.store.root)
    assert fresh.paths([response.json()['id']]) == [str(path)]
    before = set((workbench.store.root / 'inputs').rglob('*.xml'))
    invalid = client.post('/api/inputs/upload', json={'filename': 'bad.xml', 'content': base64.b64encode(b'<bad>').decode()})
    assert invalid.status_code == 400
    assert set((workbench.store.root / 'inputs').rglob('*.xml')) == before


def test_local_machine_bridge_is_optional(client):
    assert client.post('/api/local/choose/folder', json={}).status_code == 404
    assert client.post('/api/inputs/local', json={'paths': ['anything']}).status_code == 404


def test_download_contains_only_project_results(client, corpus):
    project = open_corpus(client, corpus)
    (corpus[1] / 'private.txt').write_text('not a result', encoding='utf-8')
    response = client.get(f"/api/projects/{project['id']}/download/all")
    assert response.status_code == 200
    import io
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert 'lemma-review.tsv' in archive.namelist()
        assert 'analysis-session.json' in archive.namelist()
        assert 'private.txt' not in archive.namelist()
        assert any(n.startswith('antconc-tagged/') for n in archive.namelist())
        assert any(n.startswith('antconc-lemmata/') for n in archive.namelist())
    lemmata = client.get(f"/api/projects/{project['id']}/download/lemmata")
    assert lemmata.status_code == 200 and 'AntConc-lemmata-' in lemmata.headers['content-disposition']
    with ZipFile(io.BytesIO(lemmata.content)) as archive:
        assert archive.namelist() and all(n.startswith('antconc-lemmata/') for n in archive.namelist())


def test_job_blocks_competing_writes_and_can_be_cancelled(client, workbench, corpus):
    project = open_corpus(client, corpus)
    request = choice(client, project)
    response = client.post(f"/api/projects/{project['id']}/export", json={})
    assert response.status_code == 200, response.text
    job = response.json()
    assert client.post(f"/api/projects/{project['id']}/choice", json=request).status_code == 409
    assert client.post('/api/shutdown', json={}).status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/cancel", json={}).status_code == 200
    assert (workbench.store.root / 'jobs' / job['id'] / 'cancel').exists()


def test_worker_reexports_without_loading_models_and_retains_saved_choice(client, workbench, corpus, monkeypatch):
    project = open_corpus(client, corpus)
    request = choice(client, project)
    request['lemmas'] = ['ἕ']
    assert client.post(f"/api/projects/{project['id']}/choice", json=request).status_code == 200
    job = client.post(f"/api/projects/{project['id']}/export", json={}).json()
    def forbidden(*a, **k):
        pytest.fail('saved analysis must not load models')
    monkeypatch.setattr(pipeline, 'Lemmatizer', forbidden)
    web_worker.execute(workbench.store.root / 'jobs' / job['id'])
    status = workbench.job(job['id'])
    assert status['state'] == 'done', status
    reopened = workbench.project(project['id'])
    assert reopened.workspace.records[0]['lemma'] == 'ἕ'
    assert reopened.workspace.records[0]['reviewed']
    assert not reopened.summary['pending']


def test_worker_refuses_to_publish_over_external_review_changes(client, workbench, corpus):
    project = open_corpus(client, corpus)
    job = client.post(f"/api/projects/{project['id']}/export", json={}).json()
    evidence_before = (corpus[1] / 'analysis-review.jsonl').read_bytes()
    sheet = corpus[1] / 'lemma-review.tsv'
    sheet.write_bytes(sheet.read_bytes() + b'\n')
    external = sheet.read_bytes()
    web_worker.execute(workbench.store.root / 'jobs' / job['id'])
    status = workbench.job(job['id'])
    assert status['state'] == 'failed' and 'changed' in status['error']
    assert sheet.read_bytes() == external
    assert (corpus[1] / 'analysis-review.jsonl').read_bytes() == evidence_before


def test_worker_cancels_before_publish_without_touching_results(client, workbench, corpus):
    project = open_corpus(client, corpus)
    job = client.post(f"/api/projects/{project['id']}/export", json={}).json()
    before = {p.relative_to(corpus[1]): p.read_bytes() for p in corpus[1].rglob('*') if p.is_file()}
    workbench.cancel(job['id'])
    web_worker.execute(workbench.store.root / 'jobs' / job['id'])
    assert workbench.job(job['id'])['state'] == 'cancelled'
    assert before == {p.relative_to(corpus[1]): p.read_bytes() for p in corpus[1].rglob('*') if p.is_file()}


def test_browser_preview_matches_native_preview():
    from ag_scaife2antconc.gui import scan as native_scan
    from ag_scaife2antconc.preparation import scan as browser_scan
    from .test_app import ISAEUS, ACHARNIANS
    assert browser_scan([ISAEUS, ACHARNIANS]) == native_scan([ISAEUS, ACHARNIANS])


def test_pagination_can_keep_the_next_selected_entry_visible(client, corpus):
    project = open_corpus(client, corpus)
    all_rows = client.get(f"/api/projects/{project['id']}/entries?view=all").json()['entries']
    response = client.get(f"/api/projects/{project['id']}/entries", params={'view': 'all', 'limit': 1, 'around': all_rows[2]['key']})
    assert response.status_code == 200
    assert response.json()['offset'] == 2
    assert response.json()['entries'][0]['key'] == all_rows[2]['key']


def test_progress_json_survives_a_transient_windows_reader(tmp_path):
    import threading
    from ag_scaife2antconc.web_storage import atomic_json, read_json
    target = tmp_path / 'status.json'
    atomic_json(target, {'state': 'running'})
    reader = target.open('r', encoding='utf-8')
    release = threading.Timer(0.08, reader.close)
    release.start()
    try:
        atomic_json(target, {'state': 'done'})
        assert read_json(target) == {'state': 'done'}
    finally:
        reader.close()
        release.join()


def test_invalid_json_does_not_replace_a_previous_save(tmp_path):
    from ag_scaife2antconc.web_storage import atomic_json,read_json
    target=tmp_path/'working-text.json'
    atomic_json(target,{'lemma':'ἥ','text':"μἀλλ' ἀκούσατε."})
    before=target.read_bytes()
    with pytest.raises(ValueError): atomic_json(target,{'invalid':float('nan')})
    assert target.read_bytes()==before
    assert read_json(target)['lemma']=='ἥ'
    assert not list(tmp_path.glob('.writing-*'))


def test_project_is_not_read_mid_publish(client, workbench, corpus):
    from ag_scaife2antconc.web_storage import atomic_json
    project = open_corpus(client, corpus)
    job = client.post(f"/api/projects/{project['id']}/export", json={}).json()
    path = workbench.store.root / 'jobs' / job['id'] / 'status.json'
    job['state'] = 'publishing'
    atomic_json(path, job)
    assert client.get(f"/api/projects/{project['id']}").status_code == 409
    assert client.post('/api/projects/open', json={'folder': str(corpus[1])}).status_code == 409
    job['state'] = 'done'
    atomic_json(path, job)
    assert client.get(f"/api/projects/{project['id']}").status_code == 200


def test_completed_job_appears_in_recents_after_browser_or_service_restart(workbench, corpus):
    from ag_scaife2antconc.web_storage import atomic_json
    import time
    ident = 'completed-while-browser-closed'
    atomic_json(workbench.store.root / 'jobs' / ident / 'status.json',
                {'id': ident, 'folder': str(corpus[1]), 'state': 'done', 'created': time.time(), 'finished': time.time()})
    fresh = Workbench(workbench.store.root)
    state = fresh.state()
    assert len(state['projects']) == 1
    assert state['last_project'] == state['projects'][0]['id']
    assert fresh.project(state['last_project']).summary['has_saved_analysis']
    assert len(fresh.state()['projects']) == 1
    fresh.forget_project(state['last_project'])
    assert Workbench(workbench.store.root).state()['projects'] == []


def test_download_cleans_up_temporary_zip_file(client, workbench, corpus):
    project = open_corpus(client, corpus)
    response = client.get(f"/api/projects/{project['id']}/download/all")
    assert response.status_code == 200
    downloads = workbench.store.root / 'downloads'
    assert not list(downloads.glob('*.zip'))


def test_es_modules_are_served_with_js_mime_and_pass_unit_checks(client):
    import re
    import shutil
    import subprocess
    import ag_scaife2antconc

    assets = Path(ag_scaife2antconc.__file__).with_name('web_assets')
    modules = ['app.js', 'state.js', 'api.js', 'crasis.js', 'jobs.js',
               'results.js', 'review.js', 'text_editor.js', 'projects.js', 'windows.js']
    html = client.get('/').text
    assert '<html lang="en">' in html
    assert '<script type="module" src="/assets/app.js"></script>' in html
    for marker in ('id="view-home"', 'id="view-review"', 'id="view-results"', 'id="job-panel"'):
        assert marker in html
    assert 'id="recent-projects"' not in html
    assert client.get('/assets/unknown.js').status_code == 404
    css = client.get('/assets/app.css')
    assert css.status_code == 200 and css.headers['content-type'].startswith('text/css')

    imported = set()
    sources = {}
    for name in modules:
        reply = client.get('/assets/' + name)
        assert reply.status_code == 200, name
        assert reply.headers['content-type'].startswith('text/javascript'), (name, reply.headers['content-type'])
        sources[name] = reply.text
        for match in re.findall(r"from\s+['\"]\./([^'\"]+\.js)['\"]", reply.text):
            imported.add(match)
    assert imported <= set(modules)
    exports = {n: set(re.findall(r'export\s+(?:async\s+)?(?:function|const|let|class)\s+([\w$]+)', t))
               for n, t in sources.items()}
    for name, text in sources.items():
        for syms, target in re.findall(r"import\s*\{([^}]+)\}\s*from\s*['\"]\./([^'\"]+\.js)['\"]", text):
            for sym in (s.strip().split(' as ')[0] for s in syms.split(',') if s.strip()):
                assert sym in exports[target], f'{name} imports missing symbol {sym!r} from {target}'

    node = shutil.which('node')
    if node:
        for name in modules:
            check = subprocess.run([node, '--check', str(assets / name)], capture_output=True, text=True)
            assert check.returncode == 0, f'{name}: {check.stderr}'
        crasis_url = (assets / 'crasis.js').as_uri()
        script = f"""
import assert from 'node:assert/strict';
import {{
  crasisBoundaries, isCrasisChoiceComplete, isDownloadDisabled,
  parseDownloadFilename, selectLatestTextDraft, reason
}} from {json.dumps(crasis_url)};

assert.deepEqual(crasisBoundaries('κἀγώ'), [[1, 'κ | ἀγώ'], [2, 'κἀ | γώ'], [3, 'κἀγ | ώ']]);
assert.equal(isCrasisChoiceComplete({{ crasis: {{ cut: 1, forms: ['καί', 'ἐγώ'] }}, lemmas: ['καί', 'ἐγώ'] }}), true);
assert.equal(isCrasisChoiceComplete({{ crasis: {{ cut: 0, forms: ['καί', 'ἐγώ'] }}, lemmas: ['καί', 'ἐγώ'] }}), false);
for (const kind of ['tagged', 'xml', 'all', 'review', 'report', 'evidence']) {{
  assert.equal(isDownloadDisabled(kind, true), true);
  assert.equal(isDownloadDisabled(kind, false), false);
}}
assert.equal(parseDownloadFilename('attachment; filename="custom.jsonl"', 'evidence', 'Work'), 'custom.jsonl');
assert.equal(parseDownloadFilename('attachment; filename="100%.zip"', 'all', 'Work'), '100%.zip');
assert.equal(parseDownloadFilename('', 'evidence', 'Work'), 'analysis-review.jsonl');
assert.equal(parseDownloadFilename('', 'tagged', 'Work'), 'AntConc-Work.zip');
const latest = selectLatestTextDraft([
  {{ key: 'old', value: {{ text: 'α', updated_at: 100 }} }},
  {{ key: 'new', value: {{ text: 'β', updated_at: 200 }} }},
]);
assert.equal(latest.key, 'new');
assert.ok(reason('interrogative/indefinite accent conflicts with selected lemma').includes('τίς/τις'));
assert.ok(reason('contextual top-1 outside local candidates; top-k contextual candidate used').includes('top-k'));
"""
        res = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr


def test_inputs_can_be_removed_and_recent_projects_forgotten_without_deleting_files(client, corpus):
    content = base64.b64encode(Path(corpus[0]).read_bytes()).decode('ascii')
    added = client.post('/api/inputs/upload', json={'filename': 'work.xml', 'content': content}).json()
    assert any(i['id'] == added['id'] for i in client.get('/api/state').json()['inputs'])
    assert client.post('/api/inputs/remove', json={'id': added['id']}).status_code == 200
    assert not any(i['id'] == added['id'] for i in client.get('/api/state').json()['inputs'])

    project = open_corpus(client, corpus)
    assert any(p['id'] == project['id'] for p in client.get('/api/state').json()['projects'])
    assert client.post(f"/api/projects/{project['id']}/forget", json={}).status_code == 200
    assert not any(p['id'] == project['id'] for p in client.get('/api/state').json()['projects'])
    assert (corpus[1] / 'lemma-review.tsv').is_file()
