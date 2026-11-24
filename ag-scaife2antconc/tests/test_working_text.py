"""Editorial revisions preserve original evidence and occurrence-scoped choices."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from ag_scaife2antconc import pipeline
from ag_scaife2antconc.working_text import WorkingText, Conflict, token_spans
from ag_scaife2antconc.workspace import Workspace, identity
from .test_app import tei
from .test_workspace import corpus


def test_migration_preserves_sources_and_all_existing_review_values(corpus):
    source,folder,workspace=corpus
    workspace.choose(workspace.records[1],['ἥ'],['PRON'])
    before={name:(folder/name).read_bytes() for name in ('analysis-review.jsonl','lemma-review.tsv','analysis-session.json')}
    model=WorkingText.ensure(folder)
    legacy=Workspace(folder)
    assert [(identity(r),model.values(r),model.checked(r),model.expansion(r)) for r in model.records]==[
        (identity(r),legacy.values(r),legacy.checked(r),legacy.expansion(r)) for r in legacy.records]
    assert all((folder/name).read_bytes()==content for name,content in before.items())
    archived=folder/model.data['sources'][0]['path']
    assert archived.read_bytes()==Path(source).read_bytes()
    assert model.data['sources'][0]['sha256']==hashlib.sha256(archived.read_bytes()).hexdigest()
    assert len(model.revision)==64


def test_migration_finds_analyses_saved_under_the_old_catalogue_name(corpus):
    source,folder,_=corpus
    # Up to 0.7.1 the work was named after the catalogue (here its TLG number), not the input file.
    path=folder/'analysis-review.jsonl'
    rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert {r['work'] for r in rows}=={'in'}
    path.write_text(''.join(json.dumps(dict(r,work='tlg9999.tlg001'),ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    snapshot=folder/'analysis-session.json'
    payload=json.loads(snapshot.read_text(encoding='utf-8'))
    payload['evidence_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    legacy=Workspace(folder)
    legacy.choose(legacy.records[1],['ἥ'],['PRON'])
    legacy=Workspace(folder)
    model=WorkingText.ensure(folder)
    assert [p['work'] for p in model.data['passages']]==['in','in']
    assert [(r['citation'],r['token'],model.values(r),model.checked(r)) for r in model.records]==[
        (r['citation'],r['token'],legacy.values(r),legacy.checked(r)) for r in legacy.records]


@pytest.mark.parametrize('text',[
    'ἀντὶ δʼ ὧν ἐσπεισάμην οὐκ ἴστε. μἀλλαʼ ἀκούσατε.',
    'ἥ ἄνθρωπος «ἄνδρα» ἐνταῦθα· δ’αὐτόν',
    "οἱ οἱ, 'γὼ δὲ π̣άλιν ἔρχομαι.",
])
def test_literal_character_spans_use_existing_normalization(text):
    spans=token_spans(text)
    assert spans and all(0<=s['start']<s['end']<=len(text) for s in spans)
    assert all(a['end']<=b['start'] for a,b in zip(spans,spans[1:]))
    assert ''.join(text[s['start']:s['end']] for s in spans)
    if 'μἀλλα' in text:
        item=next(s for s in spans if s['form'].startswith('μἀλλα'))
        assert text[item['start']:item['end']]=='μἀλλαʼ'


def test_edit_keeps_unambiguous_ids_and_choices_but_invalidates_context(corpus):
    _,folder,workspace=corpus
    workspace.choose(workspace.records[0],['ἥ'],['PRON'])
    model=WorkingText.ensure(folder); passage=model.passage_for(model.records[0])
    old=passage['tokens']; revision=model.revision
    result=model.edit(passage['id'],'οἱ καὶ οἱ',reason='Insert omitted conjunction',expected_revision=revision)
    assert [t['token'] for t in result['tokens']]==[old[0]['token'],3,old[1]['token']]
    assert [t['uid'] for t in result['tokens']][::2]==[t['uid'] for t in old]
    assert result['tokens'][0]['lemma']=='ἥ'
    assert result['tokens'][0]['context_stale']
    inserted=result['tokens'][1]
    assert inserted['lemma']=='' and inserted['pos']=='X' and inserted['stale']
    assert model.checked(model.records[0]) is False
    assert result['text_reviewed'] and result['annotation_stale']
    assert result['history'][-1]['reason']=='Insert omitted conjunction'
    assert model.revision!=revision
    assert WorkingText.load(folder).passage(result['id'])['text']=='οἱ καὶ οἱ'


def test_ambiguous_repeated_insertion_cannot_inherit_reviewed_neighbours(corpus):
    _,folder,workspace=corpus
    workspace.choose(workspace.records[0],['ἥ'],['PRON'])
    model=WorkingText.ensure(folder); passage=model.passage_for(model.records[0])
    original_ids={t['uid'] for t in passage['tokens']}
    result=model.edit(passage['id'],'οἱ οἱ οἱ',expected_revision=model.revision)
    assert all(t['uid'] not in original_ids for t in result['tokens'])
    assert all(t['lemma']=='' and t['stale'] for t in result['tokens'])
    assert all(t['prior_annotations'] for t in result['tokens'])


def test_undo_restores_old_text_and_ids_without_reusing_allocated_ids(corpus):
    _,folder,_=corpus
    model=WorkingText.ensure(folder); passage=model.passage_for(model.records[0])
    original=deepcopy(passage['tokens'])
    edited=model.edit(passage['id'],'οἱ καὶ οἱ',expected_revision=model.revision)
    allocated=edited['tokens'][1]['token']; event=edited['history'][-1]['id']
    restored=model.undo(passage['id'],event,expected_revision=model.revision)
    assert restored['text']==passage['text']
    assert [t['uid'] for t in restored['tokens']]==[t['uid'] for t in original]
    assert restored['history'][-1]['kind']=='undo'
    assert restored['history'][-1]['undo_of']==event
    later=model.edit(passage['id'],'οἱ δὲ οἱ',expected_revision=model.revision)
    assert later['tokens'][1]['token']>allocated


def test_undo_keeps_later_manual_choices_on_unchanged_tokens(corpus):
    _,folder,_=corpus
    model=WorkingText.ensure(folder); passage=model.passage_for(model.records[0])
    edited=model.edit(passage['id'],'οἱ καὶ οἱ',expected_revision=model.revision)
    event=edited['history'][-1]['id']
    model.choose(model.records[0],['ἥ'],['PRON'])
    restored=model.undo(passage['id'],event,expected_revision=model.revision)
    assert restored['tokens'][0]['lemma']=='ἥ'
    assert restored['tokens'][0]['context_stale']


def test_full_passage_deletion_and_undo_remain_accessible(corpus):
    _,folder,_=corpus
    model=WorkingText.ensure(folder); passage=model.passage_for(model.records[0])
    deleted=model.edit(passage['id'],'',expected_revision=model.revision)
    assert deleted['tokens']==[] and deleted['text']==''
    restored=model.undo(passage['id'],deleted['history'][-1]['id'],expected_revision=model.revision)
    assert restored['text']==passage['text'] and len(restored['tokens'])==2


def test_stale_revision_and_external_legacy_changes_are_preserved(corpus):
    _,folder,_=corpus
    first=WorkingText.ensure(folder); other=WorkingText.load(folder)
    passage=first.passage_for(first.records[0])
    first.edit(passage['id'],'οἱ καὶ οἱ',expected_revision=first.revision)
    before=(folder/'working-text.json').read_bytes()
    with pytest.raises(Conflict): other.edit(passage['id'],'οἱ δὲ οἱ',expected_revision=other.revision)
    assert (folder/'working-text.json').read_bytes()==before
    external=folder/'lemma-review.tsv'; external.write_bytes(external.read_bytes()+b'\n')
    with pytest.raises(Conflict,match='review sheet'): first.choose(first.records[0],['ὁ'],['DET'])
    assert external.read_bytes().endswith(b'\n\n')


def test_new_xml_working_text_can_be_corrected_before_annotation(tmp_path):
    source=tei(tmp_path,'<l n="293">μἀλλαʼ ἀκούσατε.</l>')
    model=WorkingText.create(tmp_path/'working',[source])
    assert len(model.records)==2 and all(r['lemma']=='' and r['stale'] for r in model.records)
    passage=model.passage_for(model.records[0])
    result=model.edit(passage['id'],"μἀλλ' ἀκούσατε.",expected_revision=model.revision)
    assert result['text']=="μἀλλ' ἀκούσατε."
    assert result['original_text']=='μἀλλαʼ ἀκούσατε.'
    assert Path(source).read_text(encoding='utf-8').find('μἀλλαʼ')>=0


def test_new_oga_preserves_supplied_annotations_boundaries_and_punctuation(tmp_path):
    source=tmp_path/'tlg0019.tlg001.perseus-grc2_oga020_src.conllu'
    source.write_text('# source = Acharnians\n1\tδʼαὐτόν\tδέ\tg\t_\t_\t0\t_\t_\tt_1|0|ref=Ach._293\n'
        '2\tἀκούσατε\tἀκούω\tv\t_\t_\t1\t_\t_\tt_2|t_1|ref=Ach._293\n'
        '3\t.\t.\tu\t_\t_\t1\t_\t_\tt_3|t_1|ref=Ach._293\n',encoding='utf-8')
    model=WorkingText.create(tmp_path/'out',[str(source)])
    assert [(r['form'],r['lemma'],r['pos']) for r in model.records]==[("δ'αὐτόν",'δέ','PART'),('ἀκούσατε','ἀκούω','VERB')]
    passage=model.passage_for(model.records[0])
    assert passage['text']=='δʼαὐτόν ἀκούσατε .'
    assert passage['tokens'][0]['record']['readings'][0]['oga']['token']=='t_1'
    assert (model.folder/model.data['sources'][0]['path']).read_bytes()==source.read_bytes()
    edited=model.edit(passage['id'],passage['text']+'!',expected_revision=model.revision)
    assert edited['tokens'][0]['uid']==passage['tokens'][0]['uid']
    assert edited['tokens'][0]['lemma']=='δέ'
    from ag_scaife2antconc.working_analysis import passage_tokens
    from ag_diogenes_spacy_txt2xml.tokenize import Word
    assert [w.form for w in passage_tokens(edited) if isinstance(w,Word)]==["δ'αὐτόν",'ἀκούσατε']


def test_failed_atomic_write_does_not_expose_an_uncommitted_edit(corpus,monkeypatch):
    _,folder,_=corpus
    model=WorkingText.ensure(folder); passage=model.passage_for(model.records[0]); original=model.revision
    def fail(*args,**kwargs): raise OSError('simulated write failure')
    monkeypatch.setattr('ag_scaife2antconc.working_text.atomic_json',fail)
    with pytest.raises(OSError,match='simulated'):
        model.edit(passage['id'],'σύ',expected_revision=original)
    assert model.revision==original and model.passage(passage['id'])['text']==passage['text']


@pytest.mark.parametrize('all_matches',[False,True])
def test_approval_matches_a_reopened_edition_and_keeps_raw_evidence(corpus,all_matches):
    _,folder,_=corpus
    model=WorkingText.ensure(folder)
    evidence=[deepcopy(t['record']) for p in model.data['passages'] for t in p['tokens']]
    model.choose(model.records[0],['ἥ'],['PRON'],all_matches=all_matches)
    fresh=WorkingText.load(folder)
    assert model.records==fresh.records
    assert model.priorities==fresh.priorities and model.contexts==fresh.contexts
    assert model.by_id==fresh.by_id
    assert [t['record'] for p in fresh.data['passages'] for t in p['tokens']]==evidence
    assert [r['lemma'] for r in model.records]==(['ἥ']*3 if all_matches else ['ἥ','ὁ','ὁ'])
    assert [model.checked(r) for r in model.records]==[True,False,False]
    assert len(fresh.data['history'])==(3 if all_matches else 1)
    assert all(e['kind']=='annotation' for e in fresh.data['history'])


def test_failed_annotation_save_restores_cached_and_durable_choices(corpus,monkeypatch):
    _,folder,_=corpus
    model=WorkingText.ensure(folder); before=model.path.read_bytes(); rows=deepcopy(model.records)
    def fail(*args,**kwargs): raise OSError('simulated annotation write failure')
    monkeypatch.setattr('ag_scaife2antconc.working_text.atomic_json',fail)
    with pytest.raises(OSError,match='simulated'):
        model.choose(model.records[0],['ἥ'],['PRON'],all_matches=True)
    assert model.path.read_bytes()==before and model.records==rows
    assert model.records==WorkingText.load(folder).records


def test_rejected_save_when_the_lock_is_busy_is_not_written_by_the_next_save(corpus,monkeypatch):
    from contextlib import contextmanager
    from ag_scaife2antconc import working_text
    _,folder,_=corpus
    model=WorkingText.ensure(folder); rows=deepcopy(model.records); lock=working_text._file_lock
    @contextmanager
    def busy(_folder):
        raise Conflict('being saved by another process')
        yield
    monkeypatch.setattr(working_text,'_file_lock',busy)
    with pytest.raises(Conflict):
        model.choose(model.records[1],['ZZZ'],['NOUN'])
    assert model.records==rows
    monkeypatch.setattr(working_text,'_file_lock',lock)
    model.choose(model.records[0],['ἥ'],['PRON'])
    assert WorkingText.load(folder).values(rows[1])==(rows[1]['lemma'],rows[1]['pos'])


def test_batch_analysis_is_atomic_and_keeps_manual_choice(corpus):
    _,folder,workspace=corpus
    workspace.choose(workspace.records[0],['ἥ'],['PRON'])
    model=WorkingText.ensure(folder)
    passages=[model.passage(p['id']) for p in model.data['passages']]
    updates=[]
    for passage in passages:
        rows=[dict(t['record'],lemma='ἐγώ',pos='PRON') for t in passage['tokens']]
        updates.append({'passage_id':passage['id'],'records':rows})
    invalid=deepcopy(updates); invalid[-1]['records'][0]['form']='λάθος'
    before=(folder/'working-text.json').read_bytes()
    with pytest.raises(ValueError,match='does not match'):
        model.apply_analyses(invalid,expected_revision=model.revision)
    assert (folder/'working-text.json').read_bytes()==before
    model.apply_analyses(updates,expected_revision=model.revision)
    assert model.records[0]['lemma']=='ἥ'
    assert all(r['lemma']=='ἐγώ' for r in model.records[1:])


def test_global_manual_choices_remain_authoritative_without_claiming_review(corpus):
    _,folder,workspace=corpus
    workspace.choose(workspace.records[0],['ἥ'],['PRON'],all_matches=True)
    model=WorkingText.ensure(folder)
    updates=[{'passage_id':p['id'],'records':[dict(t['record'],lemma='ἐγώ') for t in p['tokens']]}
             for p in model.data['passages']]
    model.apply_analyses(updates,expected_revision=model.revision)
    assert [r['lemma'] for r in model.records]==['ἥ']*3
    assert [model.checked(r) for r in model.records]==[True,False,False]


def test_archived_source_tampering_is_rejected(corpus):
    _,folder,_=corpus
    model=WorkingText.ensure(folder)
    (folder/model.data['sources'][0]['path']).write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='archived original text'):
        WorkingText.load(folder)


@pytest.mark.parametrize('invalid',['\x01','\ud800','\ufffe'])
def test_invalid_xml_characters_cannot_enter_the_working_text(corpus,invalid):
    _,folder,_=corpus
    model=WorkingText.ensure(folder); p=model.passage_for(model.records[0]); before=model.revision
    with pytest.raises(ValueError,match='control characters'):
        model.edit(p['id'],'οἱ'+invalid,expected_revision=before)
    assert WorkingText.load(folder).revision==before


def test_working_analysis_handles_damaged_word_without_token_mismatch(tmp_path):
    from ag_diogenes_spacy_txt2xml.tokenize import GAP_MARK
    from ag_scaife2antconc.working_analysis import analyse
    from .test_workspace import Engine
    source=tei(tmp_path,'<l n="1">οἱ ἀν<gap extent="1" unit="letter"/>πος οἱ!</l>')
    model=WorkingText.create(tmp_path/'working',[source])
    passage=model.data['passages'][0]
    assert GAP_MARK in passage['text']
    assert [t['form'] for t in passage['tokens']]==['οἱ','οἱ']
    analyse(model,pipeline.Options(),log=lambda m: None,engine=Engine())
    assert [r['form'] for r in model.records]==['οἱ','οἱ']
    assert all(r['lemma']=='ὁ' for r in model.records)


def test_working_analysis_changed_scope_expands_to_enclosing_verse_sentence(tmp_path):
    from ag_scaife2antconc.working_analysis import analyse
    from .test_workspace import Engine
    source=tei(tmp_path,'<l n="1">οἱ μὲν</l><l n="2">οἱ δὲ</l><l n="3">οἱ.</l><l n="4">οἱ.</l>')
    model=WorkingText.create(tmp_path/'working',[source])
    analyse(model,pipeline.Options(),log=lambda m: None,engine=Engine(),scope='all')
    p2=model.data['passages'][1]
    model.edit(p2['id'],'οἱ καί',expected_revision=model.revision)
    seen_words=[]
    class SpyEngine(Engine):
        def __call__(self,tokens,words):
            seen_words.append([w.form for w in words])
            return super().__call__(tokens,words)
    updated=analyse(model,pipeline.Options(),log=lambda m: None,engine=SpyEngine(),scope='changed')
    assert updated==1
    # Line 2 is inside sentence spanning lines 1–3 (line 4 is a separate sentence).
    assert seen_words==[['οἱ','μὲν','οἱ','καί','οἱ']]
