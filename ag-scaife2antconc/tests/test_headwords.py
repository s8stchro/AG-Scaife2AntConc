"""Model-free headword updates preserve original sources and manual revisions."""
from copy import deepcopy

import pytest

from ag_greek_rules import assess_headword
from ag_scaife2antconc import pipeline, working_headwords, web_worker
from ag_scaife2antconc.working_text import WorkingText
from .test_web import client, workbench, open_corpus, choice
from .test_workspace import corpus


class Reference:
    receipt = {'dictionary_sha256': 'fixture', 'standard':'Diogenes/Morpheus + LSJ'}
    def assess(self, form, lemma, **kwargs):
        entries = [{'id':'lsj:fixture:1','headword':'τρεῖς','verified':True},
                   {'id':'lsj:fixture:2','headword':'ὁ','verified':True}]
        return assess_headword(form, lemma, entries=entries, pos=kwargs.get('pos'))


def test_normalizes_oga_without_models_and_preserves_manual_and_source(tmp_path, monkeypatch):
    source = tmp_path/'tlg0019.tlg001.perseus-grc2_oga020_src.conllu'
    source.write_text('1\tτρία\tτρία\tm\t_\t_\t0\t_\t_\tt_1|0|ref=Ach._187\n'
                      '2\tτρία\tτρία\tm\t_\t_\t0\t_\t_\tt_2|0|ref=Ach._187\n', encoding='utf-8')
    raw = source.read_bytes()
    model = WorkingText.create(tmp_path/'out', [str(source)])
    model.choose(model.records[1], ['τρία'], ['NUM'])
    manual = deepcopy(model.data['passages'][0]['tokens'][1]['annotation'])
    original_oga = deepcopy(model.data['passages'][0]['tokens'][0]['record']['readings'][0]['oga'])
    ids = [t['uid'] for t in model.data['passages'][0]['tokens']]
    monkeypatch.setattr(pipeline, 'Lemmatizer', lambda *a, **k: pytest.fail('headword naming loaded neural models'))
    stats = working_headwords.standardize(model, pipeline.Options(), reference=Reference(), log=lambda m: None)
    assert stats['headwords renamed'] == 1 and stats['manual choices preserved'] == 1
    assert [r['lemma'] for r in model.records] == ['τρεῖς', 'τρία']
    tokens = model.data['passages'][0]['tokens']
    assert tokens[0]['record']['readings'][0]['oga'] == original_oga
    assert tokens[1]['annotation'] == manual and [t['uid'] for t in tokens] == ids
    assert source.read_bytes() == raw and (model.folder/model.data['sources'][0]['path']).read_bytes() == raw
    assert model.data['history'][-1]['kind'] == 'headwords'
    revision = model.revision
    working_headwords.standardize(model, pipeline.Options(), reference=Reference(), log=lambda m: None)
    assert model.revision == revision


def test_stale_context_is_not_silently_certified(corpus):
    _, folder, _ = corpus
    model = WorkingText.ensure(folder)
    passage = model.passage_for(model.records[0])
    model.edit(passage['id'], 'οἱ σύ οἱ', expected_revision=model.revision)
    before = deepcopy(next(p for p in model.data['passages'] if p['id'] == passage['id']))
    stats = working_headwords.standardize(model, pipeline.Options(), reference=Reference(), log=lambda m: None)
    assert stats['stale analyses preserved'] == 3
    assert next(p for p in model.data['passages'] if p['id'] == passage['id']) == before


def test_headword_update_is_atomic_when_publication_is_cancelled(corpus):
    _, folder, _ = corpus
    model = WorkingText.ensure(folder)
    before = (folder/'working-text.json').read_bytes()
    def cancelled(): raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        working_headwords.standardize(model, pipeline.Options(), reference=Reference(), before_publish=cancelled, log=lambda m: None)
    assert (folder/'working-text.json').read_bytes() == before


def test_browser_action_is_a_durable_model_free_job(client, workbench, corpus, monkeypatch):
    project = open_corpus(client, corpus); endpoint = f"/api/projects/{project['id']}"
    selected = choice(client, project)
    selected.update(lemmas=['ἕ'], tags=['PRON'])
    assert client.post(endpoint+'/choice', json=selected).status_code == 200
    original = working_headwords.standardize
    monkeypatch.setattr(working_headwords, 'standardize',
        lambda model, options, **kwargs: original(model, options, reference=Reference(), **kwargs))
    monkeypatch.setattr(pipeline, 'Lemmatizer', lambda *a, **k: pytest.fail('neural model loaded'))
    reply = client.post(endpoint+'/headwords', json={})
    assert reply.status_code == 200, reply.text
    job = reply.json()
    assert job['operation'] == 'working-headwords'
    web_worker.execute(workbench.store.root/'jobs'/job['id'])
    status = client.get('/api/jobs/'+job['id']).json()
    assert status['state'] == 'done', status
    rows = client.get(endpoint+'/entries?view=all').json()['entries']
    assert rows[0]['lemma'] == 'ἕ'
    assert status['headword_stats']['manual choices preserved'] == 1
