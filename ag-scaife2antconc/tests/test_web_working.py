"""Working-text integration: real HTTP contracts and durable, model-free worker exports."""
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from ag_scaife2antconc import pipeline, web_worker
from ag_scaife2antconc.working_text import WorkingText
from .test_app import tei
from .test_web import client, workbench, open_corpus, choice
from .test_workspace import corpus, Engine


def test_migration_edits_preserve_review_and_conflicting_tabs(client, corpus):
    source, folder, _ = corpus
    project = open_corpus(client, corpus)
    endpoint = f"/api/projects/{project['id']}"
    selected = choice(client, project)
    selected.update(lemmas=['ἕ'], tags=['PRON'])
    assert client.post(endpoint + '/choice', json=selected).status_code == 200
    original = {name: (folder / name).read_bytes() for name in ('lemma-review.tsv', 'analysis-review.jsonl', 'analysis-session.json')}
    source_bytes = Path(source).read_bytes()
    rows = client.get(endpoint + '/entries?view=all').json()['entries']
    first_key = rows[0]['key']
    reply = client.get(endpoint + '/passage/' + first_key)
    assert reply.status_code == 200, reply.text
    current = reply.json()
    assert current['project']['working_text'] and current['passage']['text'] == 'οἱ οἱ'
    assert client.get(endpoint + '/entry/' + first_key).json()['lemma'] == 'ἕ'
    body = {'passage_id': current['passage']['id'], 'revision': current['passage']['revision'],
            'text': 'οἱ σύ οἱ', 'reason': 'test insertion', 'text_reviewed': True}
    edited = client.post(endpoint + '/text', json=body)
    assert edited.status_code == 200, edited.text
    after = edited.json()
    assert after['passage']['text'] == 'οἱ σύ οἱ' and after['passage']['text_reviewed']
    assert client.get(endpoint + '/entry/' + first_key).json()['lemma'] == 'ἕ'
    assert not client.get(endpoint + '/entry/' + first_key).json()['checked']
    conflict = client.post(endpoint + '/text', json=body)
    assert conflict.status_code == 409 and conflict.json()['code'] == 'revision_conflict'
    assert client.get(endpoint + '/passages').json()['passages'][0]['id'] == body['passage_id']
    history = after['passage']['history']
    undone = client.post(endpoint + '/text/undo', json={'passage_id': body['passage_id'],
        'revision': after['passage']['revision'], 'revision_id': next(e['id'] for e in history if e['kind'] == 'text')})
    assert undone.status_code == 200, undone.text
    assert undone.json()['passage']['text'] == 'οἱ οἱ'
    assert Path(source).read_bytes() == source_bytes
    assert all((folder / name).read_bytes() == content for name, content in original.items())


def test_create_before_models_correct_export_and_portable_project(client, workbench, tmp_path, monkeypatch):
    source = tei(tmp_path, '<l n="293">ἀντὶ δʼ ὧν ἐσπεισάμην οὐκ ἴστε. μἀλλαʼ ἀκούσατε.</l>')
    initial = Path(source).read_bytes()
    item = workbench.add_paths([source])[0]
    folder = tmp_path / 'working'
    monkeypatch.setattr(pipeline, 'Lemmatizer', lambda *a, **k: pytest.fail('creation or export loaded a model'))
    response = client.post('/api/projects/create', json={'inputs': [item['id']], 'folder': str(folder)})
    assert response.status_code == 200, response.text
    project = response.json(); endpoint = f"/api/projects/{project['id']}"
    assert project['working_text'] and project['can_reexport']
    passages = client.get(endpoint + '/passages').json()
    passage = client.get(endpoint + '/passages/' + passages['passages'][0]['id']).json()['passage']
    corrected = passage['text'].replace('μἀλλαʼ', "μἀλλ'")
    edited = client.post(endpoint + '/text', json={'passage_id': passage['id'], 'revision': passage['revision'],
                                                 'text': corrected, 'reason': 'correct source reading'})
    assert edited.status_code == 200, edited.text
    assert client.get(endpoint + '/download/tagged').status_code == 409
    job = client.post(endpoint + '/export', json={}).json()
    web_worker.execute(workbench.store.root / 'jobs' / job['id'])
    status = client.get('/api/jobs/' + job['id']).json()
    assert status['state'] == 'done', status
    refreshed = client.get(endpoint).json()
    assert refreshed['exports_current'] and not refreshed['pending']
    xml = client.get(endpoint + '/download/xml')
    assert xml.status_code == 200, xml.text
    from io import BytesIO
    with ZipFile(BytesIO(xml.content)) as archive:
        text = '\n'.join(archive.read(n).decode('utf-8') for n in archive.namelist() if n.endswith('.xml'))
        assert "μἀλλ'" in text
    tagged = client.get(endpoint + '/download/tagged')
    with ZipFile(BytesIO(tagged.content)) as archive:
        text = '\n'.join(archive.read(n).decode('utf-8') for n in archive.namelist())
        assert "μἀλλ'_X_" in text and "μἀλλα'" not in text
        assert all('/antconc-tagged/' in '/' + n for n in archive.namelist())
    lemmata = client.get(endpoint + '/download/lemmata')
    assert lemmata.status_code == 200
    with ZipFile(BytesIO(lemmata.content)) as archive:
        assert archive.namelist() and all('/antconc-lemmata/' in '/' + n for n in archive.namelist())
        assert '_' not in ''.join(archive.read(n).decode('utf-8') for n in archive.namelist())
    portable = client.get(endpoint + '/download/working')
    with ZipFile(BytesIO(portable.content)) as archive:
        names = archive.namelist()
        assert 'working-text.json' in names
        archived = next(n for n in names if n.endswith('/in.xml'))
        assert archive.read(archived) == initial
        payload = json.loads(archive.read('working-text.json'))
        assert payload['passages'][0]['text'] == corrected
        portable_folder = tmp_path / 'restored-project'
        archive.extractall(portable_folder)
    restored = WorkingText.load(portable_folder)
    restored.edit(restored.data['passages'][0]['id'], corrected + ' καί', expected_revision=restored.revision)
    assert restored.data['passages'][0]['text'].endswith(' καί')
    assert Path(source).read_bytes() == initial


def test_working_analysis_uses_edited_text_and_preserves_manual_choice(client, workbench, corpus, monkeypatch):
    project = open_corpus(client, corpus); endpoint = f"/api/projects/{project['id']}"
    rows = client.get(endpoint + '/entries?view=all').json()['entries']
    selected = choice(client, project)
    selected.update(lemmas=['ἕ'], tags=['PRON'])
    assert client.post(endpoint + '/choice', json=selected).status_code == 200
    p = client.get(endpoint + '/passage/' + rows[0]['key']).json()['passage']
    edited = client.post(endpoint + '/text', json={'passage_id': p['id'], 'revision': p['revision'], 'text': 'οἱ σύ οἱ'})
    assert edited.status_code == 200, edited.text
    class CurrentTextEngine(Engine):
        def __call__(self, tokens, words):
            super().__call__(tokens, words)
            for w in words:
                if w.form == 'σύ': w.lemma, w.pos = 'σύ', 'PRON'
            return {}
    monkeypatch.setattr(pipeline, 'Lemmatizer', lambda *a, **k: CurrentTextEngine())
    job = client.post(endpoint + '/analyse', json={}).json()
    web_worker.execute(workbench.store.root / 'jobs' / job['id'])
    status = client.get('/api/jobs/' + job['id']).json()
    assert status['state'] == 'done', status
    new_rows = client.get(endpoint + '/entries?view=all').json()['entries']
    assert [(r['form'], r['lemma']) for r in new_rows[:3]] == [('οἱ','ἕ'), ('σύ','σύ'), ('οἱ','ὁ')]
    assert new_rows[0]['key'] == rows[0]['key']


def test_text_edit_blocked_by_job_and_worker_protects_revision(client, workbench, corpus):
    project = open_corpus(client, corpus); endpoint = f"/api/projects/{project['id']}"
    key = client.get(endpoint + '/entries?view=all').json()['entries'][0]['key']
    p = client.get(endpoint + '/passage/' + key).json()['passage']
    job = client.post(endpoint + '/export', json={}).json()
    edit = client.post(endpoint + '/text', json={'passage_id': p['id'], 'revision': p['revision'], 'text': 'σύ'})
    assert edit.status_code == 409 and edit.json()['code'] == 'busy'
    model = WorkingText.load(corpus[1])
    model.edit(p['id'], 'σύ', expected_revision=model.revision)
    web_worker.execute(workbench.store.root / 'jobs' / job['id'])
    assert client.get('/api/jobs/' + job['id']).json()['state'] == 'failed'
    assert WorkingText.load(corpus[1]).passage(p['id'])['text'] == 'σύ'


def test_review_after_cached_save_reloads_external_choices_and_rejects_stale_tab(client,corpus):
    project=open_corpus(client,corpus); endpoint=f"/api/projects/{project['id']}"
    selected=choice(client,project)
    client.get(endpoint+'/passage/'+selected['key']).raise_for_status()
    selected=choice(client,project)
    saved=client.post(endpoint+'/choice',json=dict(selected,lemmas=['ἥ'],tags=['PRON']))
    assert saved.status_code==200,saved.text
    stale=choice(client,project)
    external=WorkingText.load(corpus[1])
    external.choose(external.records[0],['σύ'],['PRON'])
    updated=client.get(endpoint+'/entry/'+selected['key']).json()
    assert updated['lemma']=='σύ' and updated['revision']==external.revision
    protected=(corpus[1]/'working-text.json').read_bytes()
    conflict=client.post(endpoint+'/choice',json=stale)
    assert conflict.status_code==409 and conflict.json()['code']=='revision_conflict'
    assert (corpus[1]/'working-text.json').read_bytes()==protected


def test_full_reanalysis_uses_current_model_whole_work_context_and_keeps_review(client,workbench,corpus,monkeypatch):
    project=open_corpus(client,corpus); endpoint=f"/api/projects/{project['id']}"
    key=client.get(endpoint+'/entries?view=all').json()['entries'][0]['key']
    client.get(endpoint+'/passage/'+key).raise_for_status()
    selected=choice(client,project)
    client.post(endpoint+'/choice',json=dict(selected,lemmas=['ἥ'],tags=['PRON'])).raise_for_status()
    model=WorkingText.load(corpus[1]); ids=[r['uid'] for r in model.records]
    model.data['options']['model']='grc_odycy_joint_sm'
    model._commit()
    calls=[]
    class NewEngine(Engine):
        def __call__(self,tokens,words):
            calls.append([w.form for w in words]); super().__call__(tokens,words)
            for word in words: word.lemma,word.pos='ἐγώ','PRON'
            return {}
    monkeypatch.setattr(pipeline,'Lemmatizer',lambda *a,**k:NewEngine())
    response=client.post(endpoint+'/analyse',json={'scope':'all'})
    assert response.status_code==200,response.text
    job=response.json()
    request=json.loads((workbench.store.root/'jobs'/job['id']/'request.json').read_text(encoding='utf-8'))
    assert request['options']['model']==pipeline.MODEL and request['analysis_scope']=='all'
    web_worker.execute(workbench.store.root/'jobs'/job['id'])
    assert client.get('/api/jobs/'+job['id']).json()['state']=='done'
    fresh=WorkingText.load(corpus[1])
    assert calls==[['οἱ','οἱ','οἱ']]
    assert [r['uid'] for r in fresh.records]==ids
    assert [r['lemma'] for r in fresh.records]==['ἥ','ἐγώ','ἐγώ']
    assert fresh.checked(fresh.records[0])
    assert fresh.data['analysis_runs'][-1]['metadata']['analysis_scope']=='all'


def test_morpho_lookup_returns_raw_unverified_evidence_without_editing(client,corpus,monkeypatch):
    from ag_diogenes_spacy_txt2xml import morpho
    project=open_corpus(client,corpus); endpoint=f"/api/projects/{project['id']}"
    key=client.get(endpoint+'/entries?view=all').json()['entries'][0]['key']
    originals={n:(corpus[1]/n).read_bytes() for n in ('lemma-review.tsv','analysis-review.jsonl','analysis-session.json')}
    class Lookup:
        failed=None
        records={'οἱ':{'response':{'shortdef':['ὁ, article']}}}
        def ask(self,forms): assert forms==['οἱ']
        def lemmas(self,form): return ('ὁ',)
        def evidence(self,form): return [{'query':form,'verification':'not established'}]
    monkeypatch.setattr(morpho,'Morpho',Lookup)
    response=client.post(endpoint+'/lookup/morpho',json={'key':key,'component':0})
    assert response.status_code==200,response.text
    reply=response.json()
    assert reply['lemmas']==['ὁ'] and reply['verification']=='not established'
    assert reply['evidence'][0]['raw_response']==Lookup.records['οἱ']['response']
    assert reply['selection_changed'] is False
    assert all((corpus[1]/n).read_bytes()==b for n,b in originals.items())
    assert client.post(endpoint+'/lookup/morpho',json={'key':key,'component':1}).status_code==404
