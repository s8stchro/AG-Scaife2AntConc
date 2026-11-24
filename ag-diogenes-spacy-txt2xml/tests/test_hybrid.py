"""Scientific decision regressions; no downloaded model or network needed for these checks."""
from dataclasses import replace

import pytest

from ag_diogenes_spacy_txt2xml import analyses, diogenes
from ag_diogenes_spacy_txt2xml.analyses import Analysis
from ag_diogenes_spacy_txt2xml.crasis import Crasis
from ag_diogenes_spacy_txt2xml.expansion import Expander, load_elisions
from ag_diogenes_spacy_txt2xml.greta import Greta, GretaError, clean
from ag_diogenes_spacy_txt2xml.hybrid import Hybrid, Tag, same_lemma
from ag_diogenes_spacy_txt2xml.pronouns import pronoun_score
from ag_diogenes_spacy_txt2xml.source import Line, add_milestones
from ag_diogenes_spacy_txt2xml.tokenize import Word, tokenize


def a(surface, lemma, pos=None, msd=None, ident=None):
    return Analysis(surface, lemma, ident or 'test:' + lemma, pos, msd)


class TinyStore:
    def __init__(self, entries):
        self.entries = entries

    def lookup(self, form):
        return tuple(self.entries.get(diogenes.key(form), ()))


class Neural:
    """Deterministic neural evidence makes source conflicts and ranking testable."""
    skipped = set()

    def __init__(self, predictions):
        self.predictions = predictions
        self.called = []

    def predict_many(self, forms):
        forms = list(forms)
        self.called.extend(forms)
        return {f: self.predictions.get(f) for f in forms}

    def score_many(self, pairs):
        return {(f, l): 0.0 if same_lemma(self.predictions.get(f), l) else -5.0 for f, l in pairs}


class Grammar:
    def __init__(self, tags=None):
        self.tags = tags or {}
        self.seen = []

    def pipe(self, sentences):
        for sentence in sentences:
            self.seen.append(sentence)
            yield [self.tags.get(f, Tag()) for f in sentence]


def process(text, store, neural, grammar=None):
    lines = [Line(None, '1', text)]
    add_milestones(lines)
    tokens = tokenize(lines)
    words = [t for t in tokens if isinstance(t, Word) and t.lang == 'grc']
    Hybrid(store, neural, grammar or Grammar()).run(tokens, words)
    return words


def test_full_analyses_keep_homograph_identity_and_all_grammar(tmp_path):
    path = tmp_path / 'analyses.txt'
    path.write_text("e)/xeis\t{1 9 e)/xw1\tone\tpres ind act 2nd sg}"
                    "{2 9 e)/xw2\ttwo\tpres ind act 2nd sg}\n", encoding='utf-8')
    store = analyses.load(str(path), str(tmp_path / 'cache'))
    try:
        entries = store.lookup('ἔχεις')
        assert len(entries) == 2 and len({c.lemma_id for c in entries}) == 2
        assert {c.lemma for c in entries} == {'ἔχω'}
        assert all(c.pos == 'VERB' and 'Person=2' in c.msd and 'Mood=Ind' in c.msd for c in entries)
        assert store.headwords() == {'ἔχω'}
    finally:
        store.close()
    again = analyses.load(str(path), str(tmp_path / 'cache'))
    assert again.lookup('ἔχεις') == entries
    again.close()


def test_crasis_uses_inflected_forms_and_retains_original_spelling():
    store = TinyStore({'ἐμοί': [a('ἐμοί', 'ἐγώ', 'PRON', 'Case=Dat|Number=Sing')],
                       'καί': [a('καί', 'καί', 'CCONJ')]})
    tagger = Grammar({'ἐμοί': Tag('PRON', 'Case=Dat|Number=Sing'), 'καί': Tag('CCONJ')})
    neural = Neural({'ἐμοί': 'ἐγώ', 'καί': 'καί'})
    w = process('κἀμοί', store, neural, tagger)[0]
    assert tagger.seen == [['καί', 'ἐμοί']]
    assert w.form == 'κἀμοί' and w.expansion == ['καί', 'ἐμοί']
    assert [p[1] for p in w.crasis] == ['καί', 'ἐγώ']
    assert w.crasis_msd[1] == 'Case=Dat|Number=Sing'
    assert 'ἐμοί' in neural.called and 'ἐγώ' not in neural.called


def test_unsupported_crasis_keeps_whole_word_analysis_before_model_calls():
    form = 'κἄχεζεν'
    store = TinyStore({form: [a(form, 'χέζω', 'VERB', 'Mood=Ind|Tense=Past|Person=3')]})
    neural = Neural({'ἆχεζεν': 'ἀχεύω'})
    grammar = Grammar()
    word = process(form, store, neural, grammar)[0]

    assert neural.called == [form]
    assert grammar.seen == [[form]]
    assert word.expansion == [form] and not word.crasis
    assert word.lemma == 'χέζω'
    assert 'crasis detected but no supported inflected expansion' in word.review_reasons
    evidence = word.evidence[0]
    assert evidence['kind'] == 'unresolved crasis'
    assert evidence['components'][0]['candidates'][0]['lemma'] == 'χέζω'
    assert evidence['restoration_rule'] == {'rule_id': 'grc.expansion.crasis', 'rule_version': '1.2.0'}


def test_crasis_preserves_uncertain_letters_and_component_features_in_xml():
    import xml.etree.ElementTree as ET
    from ag_diogenes_spacy_txt2xml.tei import body
    store = TinyStore({'ἐμοί': [a('ἐμοί', 'ἐγώ', 'PRON', 'Case=Dat|Number=Sing')]})
    w = process('κ̣ἀμοί', store, Neural({'καί': 'καί', 'ἐμοί': 'ἐγώ'}))[0]
    assert ''.join(p[0] for p in w.crasis) == 'κ̣ἀμοί'
    root = ET.fromstring(body([w], {'pos', 'msd'}))
    assert root.tag == 'seg' and root.get('type') == 'crasis'
    assert root[1].get('msd') == 'Case=Dat|Number=Sing'


def test_morpho_keeps_raw_response_without_claiming_manual_verification(tmp_path):
    from ag_diogenes_spacy_txt2xml.morpho import Morpho, Reply
    payload = {'shortdef': ['ἀνήρ, man'], 'parses': [{'case': 'nom'}]}
    provider = Morpho(cache_dir=str(tmp_path), delay=0, fetch=lambda q: Reply(('ἀνήρ',), payload))
    provider.ask(['ἀνήρ'])
    assert provider.lemmas('ἀνήρ') == ('ἀνήρ',)
    assert provider.records['ἀνήρ']['response'] == payload
    assert provider.evidence('ἀνήρ')[0]['verification'] == 'not established'
    again = Morpho(cache_dir=str(tmp_path), online=False)
    assert again.records['ἀνήρ']['response'] == payload


def test_article_expansion_obeys_case_gender_number():
    store = TinyStore({'ἀληθῆ': [a('ἀληθῆ', 'ἀληθής', 'ADJ', 'Case=Acc,Nom|Gender=Neut|Number=Plur')]})
    readings = Expander(store, Crasis.load(), load_elisions()).readings('τἀληθῆ')
    assert [[a.form for a in r.atoms] for r in readings] == [['τά', 'ἀληθῆ']]


def test_crasis_article_is_not_relabelled_as_relative_pronoun_by_model_agreement():
    store = TinyStore({'ἀληθῆ': [a('ἀληθῆ', 'ἀληθής', 'ADJ', 'Case=Acc|Gender=Neut|Number=Plur')]})
    tagger = Grammar({'τά': Tag('PRON', 'Case=Acc|Gender=Neut|Number=Plur'),
                      'ἀληθῆ': Tag('ADJ', 'Case=Acc|Gender=Neut|Number=Plur')})
    neural = Neural({'τά': 'ὅς', 'ἀληθῆ': 'ἀληθής'})
    # Real GreTa scores differ only slightly for these conventions; preserve structural evidence.
    neural.score_many = lambda pairs: {(f, l): -0.25 if l == 'ὅς' else -0.71 if l == 'ὁ' else 0 for f, l in pairs}
    w = process('τἀληθῆ', store, neural, tagger)[0]
    assert w.crasis[0][1:3] == ('ὁ', 'DET')
    assert w.review_reasons


def test_lexicon_cannot_replace_correct_neural_lemmas_with_inflected_words():
    store = TinyStore({'μοι': [a('μοι', 'ἐγώ', 'PRON', 'Case=Dat|Number=Sing')],
                       'ἐστί': [a('ἐστί', 'εἰμί', 'VERB', 'Mood=Ind|Person=3|Number=Sing')]})
    neural = Neural({'μοι': 'ἐγώ', 'ἐστί': 'εἰμί'})
    w = process('μοὔστι', store, neural)[0]
    assert w.expansion == ['μοι', 'ἐστί']
    assert [p[1] for p in w.crasis] == ['ἐγώ', 'εἰμί']


def test_elided_second_word_of_crasis_is_expanded_before_inference():
    store = TinyStore({'εἶτα': [a('εἶτα', 'εἶτα', 'ADV')]})
    choices = Expander(store, Crasis.load(), load_elisions()).readings('κᾆτ’')
    assert ['καί', 'εἶτα'] in [[a.form for a in r.atoms] for r in choices]


def test_k_an_keeps_modal_and_conditional_alternatives():
    store = TinyStore({'ἄν': [a('ἄν', 'ἄν', 'PART')], 'ἐάν': [a('ἐάν', 'ἐάν', 'SCONJ')]})
    w = process('κἄν', store, Neural({'καί': 'καί', 'ἄν': 'ἄν', 'ἐάν': 'ἐάν'}))[0]
    assert {tuple(r['expansion']) for r in w.evidence} == {('καί', 'ἄν'), ('καί', 'ἐάν')}
    assert any('ambiguous expansion' in reason for reason in w.review_reasons)


def test_single_diogenes_candidate_can_be_challenged_by_greta():
    store = TinyStore({'συνεχομένην': [a('συνεχομένην', 'συνόχωκα', 'VERB', 'Case=Acc|Gender=Fem|VerbForm=Part')]})
    w = process('συνεχομένην', store, Neural({'συνεχομένην': 'συνέχω'}))[0]
    assert w.lemma == 'συνέχω' and w.resp == 'greta'
    assert any(c['lemma'] == 'συνόχωκα' for c in w.evidence[0]['components'][0]['candidates'])
    assert w.review_reasons  # unsupported generated analysis remains visible for review


def test_grammatical_evidence_resolves_same_lemma_multiple_analyses():
    store = TinyStore({'λόγοι': [a('λόγοι', 'λόγος', 'NOUN', 'Case=Nom|Number=Plur', 'nom'),
                              a('λόγοι', 'λόγος', 'NOUN', 'Case=Voc|Number=Plur', 'voc')]})
    w = process('λόγοι', store, Neural({'λόγοι': 'λόγος'}), Grammar({'λόγοι': Tag('NOUN', 'Case=Voc|Number=Plur')}))[0]
    assert w.lemma_id == 'voc' and w.msd == 'Case=Voc|Number=Plur'


def test_corroborated_pos_repairs_fallback_without_changing_provider_or_ranking(tmp_path):
    path = tmp_path / 'analyses.txt'
    path.write_text('prosei=pon\t{1 1 prosei=pon\tverb\taor ind act 1st sg}\n'
                    'prose/lekto\t{2 1 prosle/gw\tverb\taor ind pass 3rd sg}\n', encoding='utf-8')
    store = analyses.load(str(path), str(tmp_path / 'cache'))
    try:
        tagger = Grammar({'προσεῖπον': Tag('ADJ', 'Case=Acc|Gender=Masc|Number=Sing')})
        w = process('προσεῖπον', store, Neural({'προσεῖπον': 'προσλέγω'}), tagger)[0]
        assert (w.lemma, w.pos, w.msd) == ('προσλέγω', 'VERB', None)
        component = w.evidence[0]['components'][0]
        assert component['tagger']['pos'] == component['selected_pos'] == 'ADJ'
        assert component['candidates'][0]['lemma'] == 'προσλέγω'
        trace = w.evidence[0]['rule_contributions'][0]
        assert trace['rule_id'] == 'grc.grammar.corroborated-pos' and trace['status'] == 'applied'
        assert trace['original_pos'] == 'ADJ' and trace['proposed_pos'] == 'VERB'
        assert component['dictionary_pos_evidence'][0]['lemma_id'].startswith('morpheus:')
    finally:
        store.close()


def test_one_grammatical_conflict_is_flagged_even_when_other_features_agree():
    store = TinyStore({'συνεχομένην': [a('συνεχομένην', 'συνόχωκα', 'VERB',
        'Case=Acc|Gender=Fem|Number=Sing|Tense=Pres|VerbForm=Part')]})
    tagger = Grammar({'συνεχομένην': Tag('VERB', 'Case=Acc|Gender=Fem|Number=Sing|Tense=Past|VerbForm=Part')})
    w = process('συνεχομένην', store, Neural({'συνεχομένην': 'συνόχωκα'}), tagger)[0]
    assert any('Tense' in reason for reason in w.review_reasons)


def test_neural_matches_preserve_different_accented_lexemes():
    assert not same_lemma('εἰμί', 'εἶμι')
    store = TinyStore({'εἶ': [a('εἶ', 'εἰμί', 'VERB'), a('εἶ', 'εἶμι', 'VERB')]})
    assert process('εἶ', store, Neural({'εἶ': 'εἶμι'}))[0].lemma == 'εἶμι'


@pytest.mark.parametrize('form, expected', [('τί', 'τίς'), ('τίς', 'τίς'), ('τίνος', 'τίς'), ('τίνι', 'τίς'),
                                         ('τι', 'τις'), ('τις', 'τις'), ('τὶ', 'τις'), ('τινός', 'τις')])
def test_pronoun_accent_outweighs_a_small_neural_preference(form, expected):
    store = TinyStore({diogenes.key(form): [a(form, 'τις', None, 'Case=Acc,Nom|Number=Sing'),
                                          a(form, 'τίς', None, 'Case=Acc,Nom|Number=Sing')]})
    neural = Neural({form: 'τις'})
    neural.score_many = lambda pairs: {(f, l): -0.1 if l == 'τις' else -0.4 for f, l in pairs}
    word = process(form, store, neural, Grammar({form: Tag('PRON')}))[0]
    assert word.lemma == expected
    assert 'pronoun_accent_score' in word.evidence[0]['components'][0]['candidates'][0]


def test_accented_indefinite_before_another_enclitic_is_not_forced_to_interrogative():
    assert pronoun_score('τις', 'τίς', following='τινα') == 0
    assert pronoun_score('τίς', 'τίς', following='τινα') == 0
    assert pronoun_score('τίς', 'τίς', msd='PronType=Int', following='γε') == 0
    assert pronoun_score('τίς', 'τοῦ') == 0
    assert pronoun_score('τις', 'τῷ') == 0


def test_frequency_of_unrelated_words_does_not_change_selection():
    store = TinyStore({'ὦ': [a('ὦ', 'ὦ', 'INTJ'), a('ὦ', 'εἰμί', 'VERB')],
                       'εἰμί': [a('εἰμί', 'εἰμί', 'VERB')]})
    neural = Neural({'ὦ': 'ὦ', 'εἰμί': 'εἰμί'})
    assert process('ὦ', store, neural)[0].lemma == process('ὦ εἰμί εἰμί εἰμί', store, neural)[0].lemma == 'ὦ'


@pytest.mark.parametrize('text', ['ὄζουσι. ὄζουσ’', 'ὄζουσ’. ὄζουσι'])
def test_full_form_and_elision_keep_their_own_allowed_analyses_in_either_order(text):
    participle = 'Case=Dat|Gender=Masc,Neut|Number=Plur|Tense=Pres|VerbForm=Part|Voice=Act'
    finite = 'Mood=Ind|Number=Plur|Person=3|Tense=Pres|VerbForm=Fin|Voice=Act'
    store = TinyStore({'ὄζουσι': [a('ὄζουσι', 'ὄζω', 'VERB', participle, 'participle'),
                                a('ὄζουσι', 'ὄζω', 'VERB', finite, 'finite')]})
    # Context favours the participle. Elision's allowed inventory must still exclude it.
    grammar = Grammar({'ὄζουσι': Tag('VERB', participle)})
    words = process(text, store, Neural({'ὄζουσι': 'ὄζω'}), grammar)
    plain = next(w for w in words if w.form == 'ὄζουσι')
    elided = next(w for w in words if w.form != 'ὄζουσι')

    assert plain.expansion == elided.expansion == ['ὄζουσι']
    assert plain.lemma_id == 'participle' and 'VerbForm=Part' in plain.msd
    assert elided.lemma_id == 'finite' and 'VerbForm=Fin' in elided.msd
    plain_candidates = plain.evidence[0]['components'][0]['candidates']
    elided_candidates = elided.evidence[0]['components'][0]['candidates']
    assert {c['lemma_id'] for c in plain_candidates} == {'participle', 'finite'}
    assert {c['lemma_id'] for c in elided_candidates} == {'finite'}


@pytest.mark.parametrize('text', ['χὠ. ἁνήρ', 'ἁνήρ. χὠ'])
def test_direct_and_synthetic_crasis_articles_keep_provenance_in_either_order(text):
    msd = 'Case=Nom|Gender=Masc|Number=Sing'
    store = TinyStore({'καί': [a('καί', 'καί', 'CCONJ')],
                       'ὁ': [a('ὁ', 'ὁ', msd=msd, ident='diogenes-article')],
                       'ἀνήρ': [a('ἀνήρ', 'ἀνήρ', 'NOUN', msd)]})
    predictions = {'καί': 'καί', 'ὁ': 'ὁ', 'ἀνήρ': 'ἀνήρ'}
    tags = {'καί': Tag('CCONJ'), 'ὁ': Tag('DET', msd), 'ἀνήρ': Tag('NOUN', msd)}
    alone = {form: process(form, store, Neural(predictions), Grammar(tags))[0]
             for form in ('χὠ', 'ἁνήρ')}

    for word in process(text, store, Neural(predictions), Grammar(tags)):
        index = 1 if word.form == 'χὠ' else 0
        component = word.evidence[0]['components'][index]
        candidate = component['candidates'][0]
        if word.form == 'χὠ':
            assert component['selected_id'] == 'diogenes-article'
            assert candidate['source'] == 'diogenes' and candidate['pos'] is None
        else:
            assert component['selected_id'] == 'article:ὁ'
            assert candidate['source'] == 'lexicon' and candidate['pos'] == 'DET'
        assert word.crasis == alone[word.form].crasis
        assert word.evidence[0]['score'] == alone[word.form].evidence[0]['score']


def test_damaged_words_are_not_sent_to_greta():
    neural = Neural({'λόγος': 'λόγος'})
    words = process('ἀν!πος λόγος', TinyStore({}), neural)
    assert all('…' not in f for f in neural.called)
    assert words[0].lemma is None


def test_unresolved_word_does_not_export_an_undefined_source():
    import xml.etree.ElementTree as ET
    from ag_diogenes_spacy_txt2xml.tei import body
    w = process('ν’', TinyStore({}), Neural({}))[0]
    assert w.lemma is None and w.resp is None
    assert ET.fromstring(body([w], {'resp'})).get('resp') is None
    assert w.review_reasons


def test_missing_greta_is_a_setup_failure_not_an_odycy_fallback(tmp_path):
    with pytest.raises(GretaError, match='prepare-greta'):
        Greta(tmp_path / 'missing')
    assert clean('λέγω λέγω') is None and clean('<unk>') is None
    assert clean('ἐγώ') == 'ἐγώ'


def test_verified_model_preparation_repairs_corruption_and_preserves_other_folders(tmp_path, monkeypatch):
    import io
    import tarfile
    from ag_diogenes_spacy_txt2xml import greta
    archive = tmp_path / 'model.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in greta.FILES:
            content = ('test ' + name).encode()
            member = tarfile.TarInfo('upstream/' + name)
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
    monkeypatch.setattr(greta, 'SHA256', greta.digest(archive))
    target = tmp_path / 'trained'
    greta.prepare(target, archive_path=archive)
    assert greta.verified(target)
    (target / 'decoder_model.onnx').write_bytes(b'interrupted download')
    assert not greta.verified(target)
    greta.prepare(target, archive_path=archive)
    assert greta.verified(target) and not list(tmp_path.glob('greta-previous-*'))
    unrelated = tmp_path / 'other'
    unrelated.mkdir()
    note = unrelated / 'notes.txt'
    note.write_text('keep me', encoding='utf-8')
    with pytest.raises(GretaError, match='άλλα αρχεία'):
        greta.prepare(unrelated, archive_path=archive)
    assert note.read_text(encoding='utf-8') == 'keep me'


def test_o_fused_with_a_vocative_is_restored_only_with_vocative_evidence():
    voc = 'Case=Voc|Gender=Masc|Number=Sing'
    store = TinyStore({'ἀναίσχυντε': [a('ἀναίσχυντε', 'ἀναίσχυντος', 'ADJ', voc)],
                       'ἑταῖρε': [a('ἑταῖρε', 'ἑταῖρος', 'NOUN', voc)],
                       'ἐσθίω': [a('ἐσθίω', 'ἐσθίω', 'VERB', 'Mood=Ind|Person=1|Number=Sing')],
                       'ὦμος': [a('ὦμος', 'ὦμος', 'NOUN', 'Case=Nom|Gender=Masc|Number=Sing')],
                       'ἀμος': [a('ἀμος', 'ἄμος', 'NOUN', 'Case=Nom|Gender=Masc|Number=Sing')]})
    expander = Expander(store, Crasis.load(), load_elisions())
    for form, second, lemma in (('ὦναίσχυντε', 'ἀναίσχυντε', 'ἀναίσχυντος'), ('ὦταῖρε', 'ἑταῖρε', 'ἑταῖρος')):
        readings = expander.readings(form)
        assert [(r.kind, [x.form for x in r.atoms]) for r in readings] == [('crasis', ['ὦ', second])]
        assert readings[0].atoms[1].analyses[0].lemma == lemma
        assert readings[0].atoms[0].analyses[0].lemma == 'ὦ'
    assert expander.readings('ὦσθίω')[0].kind != 'crasis'       # restoration is not vocative/nominative
    assert expander.readings('ὦμος')[0].atoms[0].form == 'ὦμος'  # a word of its own stays whole
    assert expander.readings('ὦξύλε')[0].kind != 'crasis'       # no evidence for the restoration
