"""Provider responsibilities, provenance and restoration boundaries without models."""
from dataclasses import replace
import hashlib
import json

import pytest

from ag_diogenes_spacy_txt2xml import analyses, diogenes
from ag_diogenes_spacy_txt2xml.contextual import Contextual, MORPHO_LIMIT
from ag_diogenes_spacy_txt2xml.greberta import Prediction
from .test_hybrid import TinyStore, Neural, a
from ag_diogenes_spacy_txt2xml.source import Line, add_milestones
from ag_diogenes_spacy_txt2xml.tokenize import Word, tokenize


class Comparator(Neural):
    def score_many(self,pairs):
        pytest.fail('The GreTa comparator must not rescore the candidate inventory')


class Context:
    receipt={'model':'test joint','lemma_composition':'neural-first'}
    def __init__(self,predictions):
        self.predictions=predictions; self.seen=[]
    def pipe(self,sentences):
        for sentence in sentences:
            self.seen.append(sentence)
            yield [self.predictions.get(form,Prediction()) for form in sentence]


def process(text,store,context,comparator,morpho=None):
    lines=[Line(None,'1',text)]; add_milestones(lines)
    tokens=tokenize(lines)
    words=[t for t in tokens if isinstance(t,Word) and t.lang=='grc']
    Contextual(store,comparator,context,morpho).run(tokens,words)
    return words


def pred(lemma,pos='PRON',source='edit_script'):
    return Prediction(lemma=lemma,pos=pos,resolved=True,lemma_source=source,script_log_probability=-.1)


def test_context_selects_ambiguous_lemma_without_greta_vote():
    store=TinyStore({'ἡμῶν':[a('ἡμῶν','ἡμός'),a('ἡμῶν','ἐγώ')]})
    context=Context({'ἡμῶν':replace(pred('ἐγώ'),msd='Case=Gen|Person=1|Number=Plur')})
    word=process('ἡμῶν',store,context,Comparator({'ἡμῶν':'ἡμός'}))[0]
    assert word.lemma=='ἐγώ' and word.resp=='diogenes greberta'
    evidence=word.evidence[0]['components'][0]
    assert evidence['context']['lemma']=='ἐγώ' and evidence['greta']=='ἡμός'
    assert {c['lemma'] for c in evidence['candidates']}=={'ἐγώ','ἡμός'}
    assert all(c['score'] is None for c in evidence['candidates'])
    assert evidence['selection']['reason']=='contextual_disambiguation'


def test_same_form_can_receive_different_contextual_lemmas():
    store=TinyStore({'οἱ':[a('οἱ','ὁ','DET'),a('οἱ','ἕ','PRON')]})
    class Occurrences(Context):
        def pipe(self,sentences):
            for sentence in sentences:
                chosen='ἕ' if 'μοι' in sentence else 'ὁ'
                yield [pred(chosen,'PRON' if chosen=='ἕ' else 'DET') if form=='οἱ' else Prediction()
                       for form in sentence]
    words=process('οἱ μοι. οἱ ἥκω.',store,Occurrences({}),Comparator({'οἱ':'ὁ'}))
    assert [w.lemma for w in words if w.form=='οἱ']==['ἕ','ὁ']


def test_contextual_lemma_outside_the_local_candidates_is_not_exported():
    store=TinyStore({diogenes.key('Κλεωνύμου'):[a('Κλεωνύμου','Κλεώνυμος','PROPN'),a('Κλεωνύμου','Κλεωνύμης','PROPN')]})
    invented=replace(pred('Κλείνομος','PROPN'),canonical_lemma='Κλεώνυμος',canonical_source='lookup_form_upos')
    word=process('Κλεωνύμου',store,Context({'Κλεωνύμου':invented}),Comparator({'Κλεωνύμου':'κλεώνυμος'}))[0]
    assert word.lemma=='Κλεώνυμος' and word.resp=='diogenes greberta'
    component=word.evidence[0]['components'][0]
    assert component['context']['lemma']=='Κλείνομος'                      # raw model output retained
    assert component['selection']['reason']=='context_outside_inventory_lookup'
    assert 'contextual lemma outside the local candidates; context_lookup used' in word.review_reasons
    # Neither the lookup nor GreTa is a candidate: no lemma, queued for review.
    store=TinyStore({'ἀπεχθήσεσθαι':[a('ἀπεχθήσεσθαι','ἀπέσσομαι','VERB'),a('ἀπεχθήσεσθαι','ἀπεχθέω','VERB')]})
    guess=replace(pred('ἀπεχθίζομαι','VERB'),canonical_lemma='ἀπεχθίζομαι')
    word=process('ἀπεχθήσεσθαι',store,Context({'ἀπεχθήσεσθαι':guess}),Comparator({'ἀπεχθήσεσθαι':'ἀπεχθάνομαι'}))[0]
    assert word.lemma is None
    assert word.evidence[0]['components'][0]['selection']['status']=='abstained'


def test_attested_headword_missing_from_the_form_inventory_is_kept_for_review():
    class Attested(TinyStore):
        def headwords(self):
            return {'Φαλής','φαλός','Φαλῆς'}
    store=Attested({diogenes.key('Φαλῆς'):[a('Φαλῆς','Φαλής','PROPN'),a('Φαλῆς','φαλός','NOUN')]})
    proposal=replace(pred('Φαλῆς','PROPN'),canonical_lemma='Φαλῆς')
    word=process('Φαλῆς',store,Context({'Φαλῆς':proposal}),Comparator({'Φαλῆς':'φαλή'}))[0]
    assert word.lemma=='Φαλῆς'
    assert word.evidence[0]['components'][0]['selection']['reason']=='context_outside_inventory_known_headword'
    assert 'contextual lemma outside the local candidates; context used' in word.review_reasons
    assert 'lemma lacks a full local morphological analysis' in word.review_reasons   # queued for review


def test_unique_local_lemma_keeps_model_disagreement_as_evidence():
    word=process('ἐσπεισάμην',TinyStore({'ἐσπεισάμην':[a('ἐσπεισάμην','σπένδω','VERB')]}),
                 Context({'ἐσπεισάμην':pred('ἔσπεισομαι','VERB')}),Comparator({'ἐσπεισάμην':'σπένδω'}))[0]
    assert word.lemma=='σπένδω'
    component=word.evidence[0]['components'][0]
    assert component['context']['lemma']=='ἔσπεισομαι'
    assert component['selection']['reason']=='unique_local_lemma'


def test_greta_only_helps_when_context_and_unique_local_lemma_are_unavailable():
    word=process('ἡμῶν',TinyStore({'ἡμῶν':[a('ἡμῶν','ἡμός'),a('ἡμῶν','ἐγώ')]}),
                 Context({}),Comparator({'ἡμῶν':'ἐγώ'}))[0]
    assert word.lemma=='ἐγώ'
    assert word.evidence[0]['components'][0]['selection']['selected_by']=='greta'
    assert 'contextual model unresolved; fallback needs review' in word.review_reasons


def test_unsupported_crasis_never_invents_an_inflected_component():
    form='κἄχεζεν'
    comparator=Comparator({form:'χέζω','ἆχεζεν':'ἀχεύω'})
    context=Context({form:pred('αἄχίζω','VERB')})
    word=process(form,TinyStore({form:[a(form,'χέζω','VERB')]}),context,comparator)[0]
    assert word.expansion==[form] and word.crasis is None and word.lemma=='χέζω'
    assert comparator.called==[form] and context.seen==[[form]]
    assert 'crasis detected but no supported inflected expansion' in word.review_reasons


def test_supported_crasis_uses_inflected_context_and_stable_written_form():
    store=TinyStore({'ἐμοί':[a('ἐμοί','ἐγώ','PRON','Case=Dat|Person=1')]})
    context=Context({'καί':pred('καί','CCONJ'),'ἐμοί':pred('ἐγώ')})
    word=process('κἀμοί',store,context,Comparator({'καί':'καί','ἐμοί':'ἐγώ'}))[0]
    assert context.seen==[['καί','ἐμοί']]
    assert word.form=='κἀμοί' and [c[1] for c in word.crasis]==['καί','ἐγώ']


def test_morpho_adds_candidates_without_changing_contextual_selection():
    class Evidence:
        def ask(self,forms): self.asked=list(forms)
        def lemmas(self,form): return ('ἡμός',)
        def evidence(self,form): return [{'query':form,'verification':'not established'}]
    provider=Evidence()
    word=process('ἡμῶν',TinyStore({'ἡμῶν':[a('ἡμῶν','ἐγώ'),a('ἡμῶν','ἡμεῖς')]}),
                 Context({'ἡμῶν':pred('ἐγώ')}),Comparator({'ἡμῶν':'ἡμός'}),provider)[0]
    assert word.lemma=='ἐγώ' and provider.asked==[]
    assert word.evidence[0]['components'][0]['morpho'][0]['verification']=='not established'


def test_dictionary_marker_and_actual_link_match_are_separate(tmp_path):
    dictionary=tmp_path/'grc.lsj.xml'
    first=b'<div2 key="e)gw/">first</div2>\n'
    dictionary.write_bytes(first+b'<div2 key="h)=mos">second</div2>\n')
    source=tmp_path/'greek-analyses.txt'
    source.write_text(f'h(mw=n\t{{0 9 e)gw/\tone\tpron gen pl}}'
                      f'{{{len(first)} 1 h(mo/s\ttwo\tmasc gen pl}}'
                      f'{{{len(first)} 0 h(me/teros\tthree\tmasc gen pl}}\n',encoding='utf-8')
    store=analyses.load(str(source),str(tmp_path/'cache'))
    try:
        rows=store.lookup('ἡμῶν')
        assert [r.dictionary_link['provider_status'] for r in rows]==['linked','guessed','missing']
        assert [r.dictionary_link['headword_match'] for r in rows]==['exact','mismatch','mismatch']
        assert rows[1].lemma=='ἡμός'  # A bad link does not erase morphological evidence.
        assert all(r.dictionary_link['source_sha256']==hashlib.sha256(source.read_bytes()).hexdigest() for r in rows)
        assert rows[1].dictionary_link['dictionary_headword']=='h)=mos'
    finally: store.close()


def test_targeted_morpho_requests_are_bounded():
    forms=['λόγ'+chr(n) for n in range(0x3b1,0x3ca)]
    class Evidence:
        def ask(self,forms): self.asked=list(forms)
        def lemmas(self,form): return ()
        def evidence(self,form): return []
    provider=Evidence()
    process(' '.join(forms),TinyStore({}),Context({f:pred('τίς') for f in forms}),
            Comparator({f:'ἕ' for f in forms}),provider)
    assert len(provider.asked)==MORPHO_LIMIT
    assert all(form in forms for form in provider.asked)


def test_dictionary_replacement_invalidates_its_link_cache(tmp_path):
    dictionary=tmp_path/'grc.lsj.xml'
    dictionary.write_text('<div2 key="h(mo/s">old</div2>\n',encoding='utf-8')
    source=tmp_path/'greek-analyses.txt'
    source.write_text('h(mw=n\t{0 1 h(mo/s\tdefinition\tgen pl}\n',encoding='utf-8')
    first=analyses.load(str(source),str(tmp_path/'cache'))
    old=first.lookup('ἡμῶν')[0].dictionary_link; first.close()
    dictionary.write_text('<div2 key="h)=mos">a different dictionary entry</div2>\n',encoding='utf-8')
    second=analyses.load(str(source),str(tmp_path/'cache'))
    new=second.lookup('ἡμῶν')[0].dictionary_link; second.close()
    assert old['headword_match']=='exact' and new['headword_match']=='mismatch'
    assert new['dictionary_sha256']!=old['dictionary_sha256']


def test_case_variants_keep_the_selected_spelling():
    # Diogenes lists Πόλις (name) beside πόλις without POS; identifiers must not decide the case.
    store=TinyStore({diogenes.key('πόλιν'):[a('πόλιν','Πόλις',ident='a:Πόλις'),a('πόλιν','πόλις',ident='b:πόλις'),
                                            a('πόλιν','πολίζω',ident='c:πολίζω')]})
    word=process('πόλιν',store,Context({'πόλιν':pred('πόλις','NOUN')}),Comparator({'πόλιν':'πόλις'}))[0]
    assert word.lemma=='πόλις'
    # GreTa writes lower case; a capitalised written form keeps the proper name.
    store=TinyStore({diogenes.key('Πολυάρχου'):[a('Πολυάρχου','Πολύαρχος',ident='a:P'),a('Πολυάρχου','πολύαρχος',ident='b:p')]})
    proposal=replace(pred('Πολυάρχας','NOUN'),canonical_lemma='Πολυάρχας')
    word=process('Πολυάρχου',store,Context({'Πολυάρχου':proposal}),Comparator({'Πολυάρχου':'πολύαρχος'}))[0]
    assert word.lemma=='Πολύαρχος'
    # An ALL-CAPS heading is not title-cased into a proper name when GreTa is the fallback.
    store=TinyStore({diogenes.key('ΜΑΡΤΥΡΕΣ'):[a('ΜΑΡΤΥΡΕΣ','Μάρτυς',ident='a:M'),a('ΜΑΡΤΥΡΕΣ','μάρτυς',ident='b:m')]})
    word=process('ΜΑΡΤΥΡΕΣ',store,Context({}),Comparator({'ΜΑΡΤΥΡΕΣ':'μάρτυς'}))[0]
    assert word.lemma=='μάρτυς'


def test_topk_inventory_disambiguation_precedes_greta_when_canonical_outside_inventory():
    store=TinyStore({diogenes.key('εἶπε'):[a('εἶπε','εἶπον','VERB'),a('εἶπε','εἰπεῖν','VERB')]})
    proposal=replace(
        pred('λέγω','VERB'),
        canonical_lemma='λέγω',
        canonical_source='lookup_form_upos',
        top_k=(
            {'lemma':'λέγω','script_id':1,'log_probability':-0.2},
            {'lemma':'εἶπον','script_id':2,'log_probability':-1.1},
            {'lemma':'εἰπεῖν','script_id':3,'log_probability':-2.5},
        ),
    )
    word=process('εἶπε',store,Context({'εἶπε':proposal}),Comparator({'εἶπε':'εἰπεῖν'}))[0]
    assert word.lemma=='εἶπον' and word.resp=='diogenes greberta'
    component=word.evidence[0]['components'][0]
    assert component['context']['lemma']=='λέγω'
    assert component['selection']['selected_by']=='context_topk'
    assert component['selection']['reason']=='context_topk_inventory_disambiguation'
    assert 'contextual top-1 outside local candidates; top-k contextual candidate used' in word.review_reasons
    # When canonical_lemma IS in the local inventory, it takes precedence over top_k.
    with_canon=replace(proposal,canonical_lemma='εἰπεῖν')
    word_c=process('εἶπε',store,Context({'εἶπε':with_canon}),Comparator({'εἶπε':'εἶπον'}))[0]
    assert word_c.lemma=='εἰπεῖν'
    assert word_c.evidence[0]['components'][0]['selection']['reason']=='context_outside_inventory_lookup'


def test_shared_context_elision_tie_breaks_by_context_match_and_grammar_fit():
    # ἄγ’ expands to both ἄγα (ἄγη, NOUN) and ἄγε (ἄγω, VERB) under reading 0's shared context (ἄγα).
    store=TinyStore({
        diogenes.key('ἄγα'):[a('ἄγα','ἄγη','NOUN','Case=Nom|Number=Sing')],
        diogenes.key('ἄγε'):[a('ἄγε','ἄγω','VERB','Mood=Imp|Number=Sing|Person=2|Tense=Pres|Voice=Act')],
        diogenes.key('ὦ'):[a('ὦ','ὦ','INTJ')],
    })
    context=Context({
        'ἄγα':replace(pred('ἄγω','VERB'),msd='Mood=Imp|Number=Sing|Person=2|Tense=Pres|Voice=Act'),
        'ὦ':pred('ὦ','INTJ'),
    })
    word=process('ἄγ’ ὦ',store,context,Comparator({'ἄγα':'ἄγη','ἄγε':'ἄγω','ὦ':'ὦ'}))[0]
    assert word.lemma=='ἄγω' and word.pos=='VERB'
    assert word.expansion==['ἄγε']
    assert word.evidence[0]['context_match']==1 and word.evidence[0]['grammar_fit']>0


def test_empty_script_scores_does_not_outscore_valid_reading():
    store=TinyStore({
        diogenes.key('ἄγα'):[a('ἄγα','ἄγη','NOUN')],
        diogenes.key('ἄγε'):[a('ἄγε','ἄγω','VERB')],
        diogenes.key('ὦ'):[a('ὦ','ὦ','INTJ')],
    })
    context=Context({
        'ἄγα':Prediction(lemma='ἄγω',pos='VERB',resolved=True,lemma_source='lookup_form',script_log_probability=None),
        'ὦ':pred('ὦ','INTJ'),
    })
    word=process('ἄγ’ ὦ',store,context,Comparator({'ἄγα':'ἄγη','ἄγε':'ἄγω','ὦ':'ὦ'}))[0]
    assert word.lemma=='ἄγω'
    assert json.dumps(word.evidence,allow_nan=False)


def test_lemma_evidence_and_corroborated_pos_work_for_selected_lemmas_and_filter_homographs():
    from ag_diogenes_spacy_txt2xml.analyses import Analysis
    class EvStore(TinyStore):
        def lemma_evidence(self,lemmas):
            out={}
            for l in lemmas:
                if l=='πόλις': out[l]=[{'lemma':l,'lemma_id':'test:πόλις','pos':'NOUN','msd':'Case=Nom|Number=Sing','raw':'','source':'diogenes'}]
                elif l=='πολίζω': out[l]=[{'lemma':l,'lemma_id':'test:πολίζω','pos':'VERB','msd':'Mood=Ind|Tense=Pres','raw':'','source':'diogenes'}]
                else: out[l]=[]
            return out
    # Surface has Diogenes NOUN for πόλις, a lexicon entry with pos=None for πόλις (which avoids the
    # POS mismatch penalty against GreBERTa's wrong VERB tag), plus a Diogenes homograph πολίζω (VERB).
    store=EvStore({
        diogenes.key('πόλις'):[
            a('πόλις','πόλις','NOUN'),
            Analysis('πόλις','πόλις','lexicon:πόλις',None,None,source='lexicon'),
            a('πόλις','πολίζω','VERB'),
        ],
    })
    # GreBERTa outputs unrelated raw lemma 'πόλιος' with canonical 'πόλις' and wrong pos 'VERB'.
    # lemma_evidence must be queried for selected lemma 'πόλις' (not just raw 'πόλιος'), and
    # homograph 'πολίζω' (VERB) must NOT pollute surface analyses and block correcting VERB -> NOUN.
    proposal=replace(pred('πόλιος','VERB'),canonical_lemma='πόλις',canonical_source='lookup_form_upos')
    word=process('πόλις',store,Context({'πόλις':proposal}),Comparator({'πόλις':'πόλις'}))[0]
    assert word.lemma=='πόλις' and word.pos=='NOUN'
    assert any(t.get('status')=='applied' and t.get('proposed_pos')=='NOUN'
               for t in word.evidence[0]['rule_contributions'])


def test_dictionary_headword_match_ignores_lsj_length_marks_and_indexes_lemmas(tmp_path):
    dictionary=tmp_path/'grc.lsj.xml'
    dictionary.write_text('<div2 key="a)_ga^qo/s">good</div2>\n',encoding='utf-8')
    source=tmp_path/'greek-analyses.txt'
    source.write_text('a)gaqo/s\t{0 9 a)gaqo/s\tgood\tmasc nom sg}\n',encoding='utf-8')
    store=analyses.load(str(source),str(tmp_path/'cache'))
    try:
        rows=store.lookup('ἀγαθός')
        assert rows[0].dictionary_link['headword_match']=='exact'
        indexes={r[0] for r in store.connection.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert 'lemmas' in indexes
    finally:
        store.close()


def test_greberta_decode_row_handles_keep_canonical_and_topk():
    import numpy as np
    from types import SimpleNamespace
    from ag_diogenes_spacy_txt2xml.greberta import Joint, _logsumexp

    joint=object.__new__(Joint)
    joint.np=np
    joint.labels={'upos':['NOUN'],**{f'x{i}':['-'] for i in range(9)}}
    joint.feats=lambda xpos: ''
    joint.trees=[['keep'],['strip_s'],['add_accent'],['keep_dup']]
    joint.lookup={'form_upos':{},'form':{},'form_lower':{}}

    def apply_tree(tree,probe):
        if tree==['keep']: return probe
        if tree==['strip_s']: return probe[:-1]
        if tree==['add_accent']: return 'πόλις'
        if tree==['keep_dup']: return 'πόλις'
        return None

    class FakePrep:
        @staticmethod
        def _strip_editorial_apparatus(w): return w
        @staticmethod
        def compose_lemma_detail(form,pos,script,lookup_form_upos,lookup_form,lookup_lower,trees,apply_edit_script,mode):
            # Reproduce pyaegean's applied == probe discard behavior
            applied=apply_edit_script(trees[script],form)
            if applied==form:
                applied=None
            if mode=='canonical':
                return form,False,'identity_fallback'
            return (applied or form),bool(applied),'edit_script' if applied else 'identity_fallback'

    joint.prep=FakePrep
    joint.apply_tree=apply_tree
    alignment=SimpleNamespace(first_subword_positions=[0])
    out={
        'upos':np.array([[[1.0]]],dtype=np.float32),
        **{f'x{i}':np.array([[[1.0]]],dtype=np.float32) for i in range(9)},
        'lemma':np.array([[[3.0,1.0,2.0,0.5]]],dtype=np.float32),
    }
    pred_row=joint._decode_row(['πόλις'],alignment,out,0)[0]
    assert pred_row.lemma=='πόλις'
    assert pred_row.lemma_source=='edit_script'
    assert pred_row.neural_lemma=='πόλις'
    assert pred_row.canonical_lemma is None
    assert pred_row.resolved is True
    # top_k merges scripts 0, 2, 3 (all 'πόλις') via logsumexp and keeps 'πόλι' separately
    assert [c['lemma'] for c in pred_row.top_k]==['πόλις','πόλι']
    assert pred_row.top_k[0]['log_probability']>pred_row.top_k[1]['log_probability']
    assert pred_row.top_k[0]['log_probability']>_logsumexp(pred_row.script_log_probability,-100.0)


def test_unicode_normalization_pluperfect_compatibility_and_final_sigma():
    from ag_diogenes_spacy_txt2xml.normalize import Normalizer
    from ag_diogenes_spacy_txt2xml.hybrid import Tag, grammatical_score, grammatical_conflicts, sentence_groups
    from ag_diogenes_spacy_txt2xml.greta import clean

    norm=Normalizer()
    assert norm('Ϲωκράτηϲ ϑεόϲ ϕίλοϲ ϵἶπϵν')=='Σωκράτης θεός φίλος εἶπεν'
    assert norm('οὐ γάρ ϲ’ ἔλεγον')=='οὐ γάρ σ’ ἔλεγον'          # elided σέ keeps the medial sigma
    assert norm('δ‘ ἐγὼ δ’ εἶπον')=='δ’ ἐγὼ δ’ εἶπον'
    assert norm('πα\u0301ρ\u0313 αὐτόν')=='πάρ’ αὐτόν'
    assert norm('τη\u0303ς')=='τῆς'
    assert clean('πρέσβωσ')=='πρέσβως'

    pqp=a('ἐπεπόνθει','πάσχω','VERB','Mood=Ind|Number=Sing|Person=3|Tense=Past|Aspect=Perf|Voice=Act')
    tag=Tag('VERB','Mood=Ind|Number=Sing|Person=3|Tense=Pqp|Voice=Act')
    assert grammatical_conflicts(pqp,tag)==[]
    assert grammatical_score(pqp,tag)>0

    lines=[Line(None,'1','τίς; ἐγώ! νῦν.')]
    add_milestones(lines)
    groups=list(sentence_groups(tokenize(lines,tlg_gap=False)))
    assert len(groups)==3


def test_elision_deaspiration_crasis_vowels_double_elision_and_compound_joining():
    from ag_diogenes_spacy_txt2xml.crasis import Crasis
    from ag_diogenes_spacy_txt2xml.expansion import Expander, load_elisions

    assert 'ἐγένετο' in diogenes.unelided('ἐγένεθ’')
    assert 'σώματα' in diogenes.unelided('σώμαθ’')
    assert 'εἶπε' in diogenes.unelided('εἶφ’')
    assert 'νύκτα' in diogenes.unelided('νύχθ’')
    assert 'ὕδωρ' in diogenes.uncontracted('θ', 'οὔδωρ')
    assert 'ἀνδρός' in diogenes.uncontracted('τ', 'ὠνδρός')
    assert 'εἶπεν' in diogenes.uncontracted('κ', 'ᾆπεν')
    assert diogenes.compound('σύν-σιτέω')=='συσσιτέω'
    assert diogenes.compound('ἐν-ῥίπτω')=='ἐνρίπτω'
    assert diogenes.compound('παρά-κεῖμαι')=='παράκειμαι'
    assert diogenes.compound('κατά-καθαίρω')=='κατακαθαίρω'
    assert diogenes.compound('ἐν-ἔννυμι')=='ἐνέννυμι'

    store=TinyStore({
        diogenes.key('ἐστί'):[a('ἐστί','εἰμί','AUX')],
        diogenes.key('ἐγώ'):[a('ἐγώ','ἐγώ','PRON')],
        diogenes.key('οἶμαι'):[a('οἶμαι','οἴομαι','VERB')],
    })
    exp=Expander(store,Crasis.load(),load_elisions())
    assert any(r.atoms[0].form=='ἐστί' for r in exp.readings('’στ’'))
    assert any([at.form for at in r.atoms]==['ἐγώ','οἶμαι'] for r in exp.readings('ἐγᾦμαι'))


def test_pronoun_accent_review_observation_and_orthotonic_enclitic_handling():
    from ag_greek_rules import assess_pronoun
    # Elided enclitic γ’ abstains; orthotonic νῦν / πῶς / εἶμι do NOT abstain; Attic ἄττα is indefinite.
    assert assess_pronoun('τίς','τίς',following='γ’').status=='abstained'
    assert assess_pronoun('τίς','τίς',following='ποτ’').status=='abstained'
    assert assess_pronoun('τίς','τίς',following='νῦν').status=='applied'
    assert assess_pronoun('τίς','τίς',following='πῶς').status=='applied'
    assert assess_pronoun('τίς','τίς',following='εἶμι').status=='applied'
    assert assess_pronoun('ἄττα','τις').status=='applied'
    # In Contextual, pronoun accent acts as a non-overriding review observation when conflicting.
    store=TinyStore({
        diogenes.key('τίς'):[a('τίς','τίς','PRON'),a('τίς','τις','PRON')],
        diogenes.key('δέ'):[a('δέ','δέ','PART')],
    })
    word=process('τίς δέ',store,Context({'τίς':pred('τις','PRON'),'δέ':pred('δέ','PART')}),
                 Comparator({'τίς':'τίς','δέ':'δέ'}))[0]
    assert word.lemma=='τις'
    assert 'interrogative/indefinite accent conflicts with selected lemma' in word.review_reasons


def test_greberta_windows_of_different_lengths_are_never_padded_together():
    from types import SimpleNamespace
    from ag_diogenes_spacy_txt2xml.greberta import Joint

    joint=object.__new__(Joint)
    calls=[]
    def fake_run(items):
        calls.append({(len(a.input_ids),len(a.first_subword_positions)) for _,a in items})
        return [[w for w in ws] for ws,_ in items]
    joint._run=fake_run
    item=lambda ws,n: (ws,SimpleNamespace(input_ids=[0]*n,first_subword_positions=list(range(len(ws)))))
    items=[item(['a','b'],5),item(['c','d','e'],7),item(['f','g'],5),item(['h'],5)]
    assert joint._run_unpadded(items)==[['a','b'],['c','d','e'],['f','g'],['h']]
    assert all(len(shapes)==1 for shapes in calls) and len(calls)==3


def test_greberta_overlapping_windows_and_context_elision_alignment():
    from types import SimpleNamespace
    from ag_diogenes_spacy_txt2xml.greberta import Joint

    joint=object.__new__(Joint)
    joint.seen_windows=[]
    # Simulate a 6-word sentence where max subword window fits 4 words at a time.
    def fake_align(words):
        k=min(4,len(words))
        return list(words),SimpleNamespace(kept_indices=list(range(k)),first_subword_positions=list(range(k)),
                                           input_ids=[0]*(k+2))
    def fake_run(items):
        out=[]
        for ws,_ in items:
            joint.seen_windows.append(list(ws))
            out.append([Prediction(lemma=f'{w}@{len(ws)}',pos='NOUN',resolved=True,lemma_source='edit_script',
                                   script_log_probability=-0.1) for w in ws])
        return out
    joint._align=fake_align
    joint._run=fake_run
    preds=joint._predict(['w0','w1','w2','w3','w4','w5'])
    assert len(preds)==6
    assert len(joint.seen_windows)==2 and joint.seen_windows==[['w0','w1','w2','w3'],['w2','w3','w4','w5']]
    assert all(p.context_scope=='overlapping subword-limit window' for p in preds)
    # Unexpanded elision passes U+0313 to GreBERTa context.
    store=TinyStore({})
    ctx=Context({})
    process('ψυδ’ ἐγώ',store,ctx,Comparator({}))
    assert ctx.seen==[['ψυδ\u0313','ἐγώ']]
