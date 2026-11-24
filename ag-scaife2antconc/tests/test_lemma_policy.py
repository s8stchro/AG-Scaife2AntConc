import copy
from collections import Counter
from types import SimpleNamespace

from ag_diogenes_spacy_txt2xml.tokenize import Word
from ag_scaife2antconc import pipeline
from ag_scaife2antconc.lemma_policy import apply_deictic_policy
from ag_scaife2antconc.session import Saved
from ag_scaife2antconc.workspace import Workspace
from .test_app import FakeLemmatizer, tei


def selected_word(form, lemma):
    word = Word([('t', form)], lemma=lemma, pos='PRON', msd='Case=Acc|Number=Sing',
                resp='diogenes context', lemma_id='morpheus:base-id', expansion=[form])
    word.evidence = [dict(kind='word', expansion=[form], score=1.0, components=[dict(
        form=form, greta=lemma, selected_id=word.lemma_id,
        candidates=[dict(lemma=lemma,lemma_id=word.lemma_id,source='diogenes',score=1.0,raw='masc acc sg')])])]
    return word


def test_policy_keeps_provider_predictions_and_scores_separate():
    word = selected_word('τουτονὶ', 'οὗτος')
    raw = copy.deepcopy(word.evidence[0]['components'])
    assert apply_deictic_policy([word]) == 1
    assert word.lemma == 'οὑτοσί' and not word.crasis
    assert word.expansion == ['τουτονὶ'] and word.form == 'τουτονὶ'
    assert word.evidence[0]['components'] == raw
    assert word.lemma_id is None and word.resp.endswith('lemma_policy')
    assert word.msd == 'Case=Acc|Number=Sing'
    trace = word.evidence[0]['rule_contributions'][0]
    assert trace['candidate_lemma'] == 'οὗτος' and trace['headword'] == 'οὑτοσί'
    assert trace['rule_id'] == 'grc.lemma.deictic-iota-headword'
    before = copy.deepcopy(word)
    apply_deictic_policy([word])
    assert word == before


def test_deictic_policy_preserves_another_rules_trace():
    word = selected_word('τουτονὶ', 'οὗτος')
    trace = {'rule_id': 'grc.grammar.corroborated-pos', 'status': 'abstained', 'component': 0}
    word.evidence[0]['rule_contributions'] = [trace]
    assert apply_deictic_policy([word]) == 1
    assert word.lemma == 'οὑτοσί'
    assert word.evidence[0]['rule_contributions'][0] == trace
    assert word.evidence[0]['rule_contributions'][1]['rule_id'] == 'grc.lemma.deictic-iota-headword'
    before = copy.deepcopy(word)
    assert apply_deictic_policy([word]) == 0 and word == before


def test_real_application_engine_calls_the_portable_policy_after_selection():
    word = selected_word('νυνὶ', 'νυνί')
    engine = pipeline.Lemmatizer.__new__(pipeline.Lemmatizer)
    engine.morpho = None
    engine.headword_reference = None
    engine.hybrid = SimpleNamespace(run=lambda *a: Counter({'diogenes context':1}))
    stats = engine([word], [word])
    assert stats['deictic forms'] == 1 and word.lemma == 'νυνί'
    assert [t['rule_id'] for t in word.evidence[0]['rule_contributions']] == [
        'grc.lemma.deictic-iota-headword', 'grc.lemma.diogenes-headword']
    assert word.evidence[0]['rule_contributions'][-1]['reason'] == 'separate_deictic_headword_preserved'


def test_unknown_or_unrelated_analysis_is_not_silently_replaced():
    word = selected_word('αὑτηί', 'ἑαυτοῦ')
    assert apply_deictic_policy([word]) == 0
    assert word.lemma == 'ἑαυτοῦ'
    assert word.evidence[0]['rule_contributions'][0]['status'] == 'abstained'


class PolicyEngine(FakeLemmatizer):
    class greta:
        name = 'GreTa test'

    def __call__(self, tokens, words):
        super().__call__(tokens, words)
        for word in words:
            selected = selected_word(word.form, 'οὗτος')
            for field in ('lemma','pos','msd','resp','lemma_id','expansion','evidence'):
                setattr(word, field, getattr(selected, field))
        apply_deictic_policy(words)
        return Counter({'diogenes context':len(words)})


def test_export_replay_manual_override_and_search_keep_the_convention(tmp_path):
    source = tei(tmp_path, '<div n="1"><p>οὑτοσί τουτονὶ</p></div>')
    out = tmp_path/'out'
    pipeline.run([source], str(out), pipeline.Options(session=True, xml=True), log=lambda m:None, lemmatizer=PolicyEngine())
    workspace = Workspace(out)
    assert [r['lemma'] for r in workspace.records] == ['οὑτοσί','οὑτοσί']
    assert all(len(r['expansion']) == 1 for r in workspace.records)
    assert len(workspace.visible('deictic','all')) == 2
    assert len(workspace.visible('-ι','all')) == 2
    assert 'τουτονὶ_PRON_οὑτοσί' in next((out/'antconc-tagged').glob('*.txt')).read_text(encoding='utf-8-sig')
    assert 'lemma="οὑτοσί"' in next((out/'xml').glob('*.xml')).read_text(encoding='utf-8')
    workspace.choose(workspace.records[1], ['οὗτος'], ['PRON'])
    saved = Saved.load(out)
    pipeline.run(saved.paths, str(out), saved.export_options(out), log=lambda m:None, lemmatizer=saved)
    workspace = Workspace(out)
    assert [workspace.values(r)[0] for r in workspace.records] == ['οὑτοσί','οὗτος']
    assert workspace.records[1]['machine_before_correction'][0] == 'οὑτοσί'
    assert workspace.records[1]['rule_contributions'][0]['headword'] == 'οὑτοσί'
    assert all(len(r['expansion']) == 1 for r in workspace.records)
