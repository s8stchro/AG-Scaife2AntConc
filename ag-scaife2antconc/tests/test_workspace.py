import json

import pytest

from ag_scaife2antconc import pipeline, review
from ag_scaife2antconc.pipeline import Options, SetupError, run
from ag_scaife2antconc.session import Saved
from ag_scaife2antconc.workspace import Workspace
from .test_app import FakeLemmatizer, tei


class Engine(FakeLemmatizer):
    class greta:
        name = 'GreTa test'

    def __call__(self, tokens, words):
        stats = super().__call__(tokens, words)
        for w in words:
            w.lemma, w.pos, w.msd = 'ὁ', 'DET', 'Case=Nom|Number=Plur'
            w.lemma_id = 'test:1'
            w.review_reasons = ['ambiguous grammatical/lexical analysis']
        return stats


@pytest.fixture
def corpus(tmp_path):
    source = tei(tmp_path, '<div n="1"><p>οἱ οἱ</p></div><div n="2"><p>οἱ</p></div>')
    folder = tmp_path / 'out'
    run([source], str(folder), Options(session=True, xml=True), log=lambda m: None, lemmatizer=Engine())
    return source, folder, Workspace(folder)


def reexport(folder):
    saved = Saved.load(folder)
    run(saved.paths, str(folder), saved.export_options(folder), log=lambda m: None, lemmatizer=saved)
    return Workspace(folder)


def test_legacy_run_cannot_replace_a_working_edition(corpus):
    from ag_scaife2antconc.working_text import WorkingText
    source, folder, _ = corpus
    WorkingText.ensure(folder)
    preserved = {name: (folder / name).read_bytes() for name in ('working-text.json', 'lemma-review.tsv')}
    with pytest.raises(SetupError, match='editable working text'):
        run([source], str(folder), Options(replace=True), lemmatizer=Engine())
    assert all((folder / name).read_bytes() == content for name, content in preserved.items())


def test_scoped_choice_replays_without_loading_models(corpus, monkeypatch):
    _, folder, workspace = corpus
    chosen = workspace.records[1]
    workspace.choose(chosen, ['ἕ'], ['PRON'])
    assert [workspace.checked(r) for r in workspace.records] == [False, True, False]
    def forbidden(*args):
        pytest.fail('re-export must not load a model')
    monkeypatch.setattr(pipeline, 'Lemmatizer', forbidden)
    result = reexport(folder)
    assert [(r['lemma'], r['reviewed']) for r in result.records] == [('ὁ', False), ('ἕ', True), ('ὁ', False)]
    assert result.records[1]['machine_before_correction'][:2] == ['ὁ', 'DET']
    assert 'neural lemmatization was not re-run' in (folder / 'report.txt').read_text(encoding='utf-8')
    result.choose(result.records[1], ['ὁ'], ['DET'])
    result = reexport(folder)
    assert all(r['lemma'] == 'ὁ' for r in result.records)
    assert result.records[1]['msd'] == 'Case=Nom|Number=Plur'
    assert result.records[1]['lemma_id'] == 'test:1'


def test_global_choice_does_not_claim_all_occurrences_were_reviewed(corpus):
    _, folder, workspace = corpus
    workspace.choose(workspace.records[0], ['ἕ'], ['PRON'], all_matches=True)
    result = reexport(folder)
    assert [r['lemma'] for r in result.records] == ['ἕ'] * 3
    assert [r['reviewed'] for r in result.records] == [True, False, False]
    assert len(result.visible(filter_name='checked')) == 1


def test_confirmation_preserves_original_morphology_and_sources(corpus):
    _, folder, workspace = corpus
    workspace.choose(workspace.records[0], ['ὁ'], ['DET'])
    result = reexport(folder)
    record = result.records[0]
    assert record['reviewed'] and record['resp'] == 'diogenes'
    assert record['msd'] == 'Case=Nom|Number=Plur' and record['lemma_id'] == 'test:1'


def test_workspace_preserves_external_sheet_edits(corpus):
    _, folder, workspace = corpus
    other = Workspace(folder)
    other.choose(other.records[2], ['σύ'], ['PRON'])
    workspace.choose(workspace.records[0], ['ἕ'], ['PRON'])
    result = reexport(folder)
    assert [r['lemma'] for r in result.records] == ['ἕ', 'ὁ', 'σύ']


@pytest.mark.parametrize('operation', ['choice', 'export'])
def test_save_survives_a_transient_windows_reader(corpus, operation):
    import threading
    _, folder, workspace = corpus
    record = workspace.records[0]
    if operation == 'export':
        workspace.choose(record, ['\u1f25'], ['PRON'])
        path = next((folder / 'antconc-tagged').glob('*.txt'))
    else:
        path = workspace.sheet
    reader = path.open('r', encoding='utf-8')
    release = threading.Timer(0.08, reader.close)
    release.start()
    try:
        if operation == 'choice':
            workspace.choose(record, ['\u1f25'], ['PRON'])
            assert Workspace(folder).values(record)[0] == '\u1f25'
        else:
            assert reexport(folder).records[0]['lemma'] == '\u1f25'
    finally:
        reader.close()
        release.join()


@pytest.mark.parametrize('value', ['', 'δύο λέξεις', 'α+β', '?'])
def test_invalid_choices_leave_the_sheet_unchanged(corpus, value):
    _, _, workspace = corpus
    before = workspace.sheet.read_bytes()
    with pytest.raises(ValueError):
        workspace.choose(workspace.records[0], [value], ['PRON'])
    assert workspace.sheet.read_bytes() == before


def test_replay_rejects_changed_source_before_publish(corpus):
    source, folder, workspace = corpus
    from pathlib import Path
    original = Path(source).read_text(encoding='utf-8')
    Path(source).write_text(original.replace('οἱ οἱ', 'οἱ σύ'), encoding='utf-8')
    before = (folder / 'analysis-review.jsonl').read_bytes()
    with pytest.raises(SetupError, match='changed'):
        reexport(folder)
    assert (folder / 'analysis-review.jsonl').read_bytes() == before


def test_snapshot_rejects_mismatched_evidence(corpus):
    _, folder, _ = corpus
    path = folder / 'analysis-review.jsonl'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(SetupError, match='compatible'):
        Saved.load(folder)


@pytest.mark.parametrize('app_version', ['0.3.0', '0.3.1', '0.3.2', '0.4.0', '0.4.1', '0.4.2', '0.4.3'])
def test_snapshot_accepts_previous_compatible_interface_version(corpus, app_version):
    _, folder, _ = corpus
    path = folder / 'analysis-session.json'
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['app_version'] = app_version
    if app_version == '0.4.2':
        payload['core_version'] = '0.3.1'
    elif app_version != '0.4.3':
        payload['core_version'] = '0.3.0'
    path.write_text(json.dumps(payload), encoding='utf-8')
    assert Saved.load(folder)
    payload['app_version'] = '0.2.2'
    path.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(SetupError, match='compatible'):
        Saved.load(folder)


def test_cancellation_before_publish_keeps_previous_results(corpus):
    source, folder, workspace = corpus
    before = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    class Stop(Exception):
        pass
    def cancel():
        raise Stop()
    with pytest.raises(Stop):
        run([source], str(folder), Options(session=True, xml=True), log=lambda m: None,
            lemmatizer=Engine(), before_publish=cancel)
    assert before == {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def test_crasis_confirmation_keeps_component_morphology(tmp_path):
    from ag_diogenes_spacy_txt2xml.tokenize import Word
    word = Word([('t', 'κἀγώ')], crasis=[('κ', 'καί', 'CCONJ', 'lexicon'), ('ἀγώ', 'ἐγώ', 'PRON', 'diogenes')],
                crasis_msd=['', 'Case=Nom|Number=Sing'], expansion=['καί', 'ἐγώ'])
    c = review.Correction('καί+ἐγώ', 'CCONJ+PRON', [], scope=('Work', '1', 1))
    review.apply([('κἀγώ', word, '1', 1)], {('κἀγώ', 'καί+ἐγώ', 'Work', '1', 1): c}, work='Work')
    assert word.reviewed and word.crasis[1][3] == 'diogenes'
    assert word.crasis_msd == ['', 'Case=Nom|Number=Sing']


def test_workspace_reads_and_updates_legacy_greek_review_sheet(corpus):
    _, folder, _ = corpus
    sheet = folder / 'lemma-review.tsv'
    greek_header = ['μορφή', 'λήμμα', 'μέρος λόγου', 'εμφανίσεις', 'σημείωση', 'άλλα λήμματα',
                    'θέσεις', 'συμφραζόμενα', 'διορθωμένο λήμμα', 'διορθωμένο μέρος λόγου',
                    'έργο', 'θέση', 'λέξη', 'χειροκίνητη κράση']
    legacy_row = ['οἱ', 'ὁ', 'DET', '1', '', '', '1', '', 'σύ', 'PRON', 'in', '2', '1', '']
    sheet.write_text(review.BOM + '\t'.join(greek_header) + '\n' + '\t'.join(legacy_row) + '\n', encoding='utf-8')
    ws = Workspace(folder)
    assert ws.values(ws.records[2]) == ('σύ', 'PRON')
    assert ws.checked(ws.records[2])
    ws.choose(ws.records[0], ['ἕ'], ['PRON'])
    reopened = Workspace(folder)
    assert reopened.values(reopened.records[0]) == ('ἕ', 'PRON')
    assert reopened.values(reopened.records[2]) == ('σύ', 'PRON')
