import unicodedata
import xml.etree.ElementTree as ET

import pytest

from ag_diogenes_spacy_txt2xml.crasis import Crasis, coronis_cuts
from ag_diogenes_spacy_txt2xml.expansion import Expander, load_elisions
from ag_diogenes_spacy_txt2xml.hybrid import Tag
from ag_diogenes_spacy_txt2xml.tei import body
from .test_hybrid import Grammar, Neural, TinyStore, a, process


@pytest.mark.parametrize('normalization', ['NFC', 'NFD'])
@pytest.mark.parametrize('form,left', [('δήξομἄρ’','δήξομ'), ('μέντἂν','μέντ'),
                                     ('καίτοὐστιν','καίτ'), ('σοὐστί','σ'), ('θοἰμάτιον','θ')])
def test_medial_detection_uses_vowel_nuclei_and_original_character_offsets(form, left, normalization):
    form, left = unicodedata.normalize(normalization, form), unicodedata.normalize(normalization, left)
    assert len(left) in coronis_cuts(form)


@pytest.mark.parametrize('form', ['αὐτός', 'οἶδα', 'εἰμί', 'αἱ', 'ὁ', 'ἄνδρες', 'ῥήτωρ', 'ἄῤῥητος'])
def test_initial_breathings_and_rho_are_not_medial_coronides(form):
    assert coronis_cuts(form) == ()


def test_productive_coronis_restores_future_middle_verb_and_elided_particle():
    store = TinyStore({'δήξομαι': [a('δήξομαι', 'δάκνω', 'VERB', 'Mood=Ind|Tense=Fut|Voice=Mid|Person=1|Number=Sing')],
                       'ἄρα': [a('ἄρα', 'ἄρα', 'PART')]})
    neural = Neural({'δήξομαι': 'δάκνω', 'ἄρα': 'ἄρα'})
    grammar = Grammar({'δήξομαι': Tag('VERB', 'Mood=Ind|Tense=Fut|Voice=Mid|Person=1|Number=Sing'),
                       'ἄρα': Tag('PART')})
    w = process('δήξομἄρ’', store, neural, grammar)[0]
    assert w.expansion == ['δήξομαι', 'ἄρα']
    assert [part[1] for part in w.crasis] == ['δάκνω', 'ἄρα']
    assert w.crasis_msd[0] == 'Mood=Ind|Number=Sing|Person=1|Tense=Fut|Voice=Mid'
    assert ''.join(part[0] for part in w.crasis) == 'δήξομἄρ’'
    root = ET.fromstring(body([w], {'pos', 'msd'}))
    assert [part.get('lemma') for part in root] == ['δάκνω', 'ἄρα']
    assert ''.join(root.itertext()) == 'δήξομἄρ’'


def test_productive_coronis_is_not_limited_to_verbs_or_single_letter_prefixes():
    store = TinyStore({'μέντοι': [a('μέντοι', 'μέντοι', 'PART')], 'ἄν': [a('ἄν', 'ἄν', 'PART')]})
    w = process('μέντἂν', store, Neural({'μέντοι': 'μέντοι', 'ἄν': 'ἄν'}))[0]
    assert w.expansion == ['μέντοι', 'ἄν']
    assert [part[1] for part in w.crasis] == ['μέντοι', 'ἄν']


def test_unsupported_internal_coronis_is_flagged_instead_of_silently_ignored():
    w = process('δήξομἄρ’', TinyStore({}), Neural({}))[0]
    assert not w.crasis
    assert any('internal coronis' in reason for reason in w.review_reasons)


def test_recognized_prefixed_verb_is_not_split_into_independent_words():
    store = TinyStore({'προὔδωκεν': [a('προὔδωκεν', 'προδίδωμι', 'VERB', 'Mood=Ind')],
                       'προ': [a('προ', 'πρό', 'ADP')],
                       'ἔδωκεν': [a('ἔδωκεν', 'δίδωμι', 'VERB', 'Mood=Ind')]})
    w = process('προὔδωκεν', store, Neural({'προὔδωκεν': 'προδίδωμι'}))[0]
    assert w.lemma == 'προδίδωμι' and not w.crasis


def test_initial_rough_breathing_article_crasis_restores_plural_article_and_noun():
    store = TinyStore({'ἄνδρες': [a('ἄνδρες', 'ἀνήρ', 'NOUN', 'Case=Nom|Gender=Masc|Number=Plur')]})
    w = process('ἅνδρες', store, Neural({'οἱ': 'ὁ', 'ἄνδρες': 'ἀνήρ'}))[0]
    assert w.expansion == ['οἱ', 'ἄνδρες']
    assert [part[1] for part in w.crasis] == ['ὁ', 'ἀνήρ']
    assert ''.join(part[0] for part in w.crasis) == 'ἅνδρες'


def test_aspirated_article_consonant_matches_the_unaspirated_article():
    store = TinyStore({'ἱμάτιον': [a('ἱμάτιον', 'ἱμάτιον', 'NOUN', 'Case=Acc|Gender=Neut|Number=Sing')]})
    w = process('θοἰμάτιον', store, Neural({'τό': 'ὁ', 'ἱμάτιον': 'ἱμάτιον'}))[0]
    assert w.expansion == ['τό', 'ἱμάτιον']
    assert [part[1] for part in w.crasis] == ['ὁ', 'ἱμάτιον']


def test_lexical_medial_aspiration_in_peacock_is_not_an_independent_word_boundary():
    store = TinyStore({'ταὧσι': [a('ταὧσι', 'ταὧς', 'NOUN', 'Case=Dat|Number=Plur')],
                       'τα': [a('τα', 'ὁ', 'DET')], 'ὧσι': [a('ὧσι', 'εἰμί', 'VERB')]})
    w = process('ταὧσι', store, Neural({'ταὧσι': 'ταὧς'}))[0]
    assert w.lemma == 'ταὧς' and not w.crasis
    assert w.evidence[0]['kind'] == 'lexical medial breathing'


def test_lexical_peacock_exception_also_applies_without_a_full_local_parse():
    w = process('ταὧσι', TinyStore({}), Neural({'ταὧσι': 'ταὧς'}))[0]
    assert w.lemma == 'ταὧς' and not w.crasis


def test_native_first_component_and_shared_context_resist_invented_coronis_readings():
    store = TinyStore({'μέντἄν': [a('μέντἄν', 'μέντοι', 'PART')],
                       'μέντοι': [a('μέντοι', 'μέντοι', 'PART')],
                       'μέντα': [a('μέντα', 'Μέντης', None, 'Case=Acc|Number=Dual')],
                       'ἄν': [a('ἄν', 'ἄν', 'PART')], 'ἔν': [a('ἔν', 'ἐν', 'ADP')]})
    grammar = Grammar({'μέντοι': Tag('PART'), 'ἂν': Tag('PART'),
                       'μέντα': Tag('VERB'), 'ἔν': Tag('ADP')})
    neural = Neural({'μέντοι': 'μέντοι', 'μέντα': 'Μέντης', 'ἂν': 'ἄν', 'ἄν': 'ἄν', 'ἔν': 'ἐν'})
    w = process('μέντἂν', store, neural, grammar)[0]
    assert [p[1] for p in w.crasis] == ['μέντοι', 'ἄν']
    assert len(grammar.seen) == 1
    assert all(r['shared_context'] for r in w.evidence)


def test_retained_function_word_survives_neural_expansion_to_a_different_headword():
    store = TinyStore({'μέντοι': [a('μέντοι', 'μέντοι', 'PART')],
                       'ἄν': [a('ἄν', 'ἄν', 'PART'), a('ἄν', 'ἐάν', 'CCONJ')]})
    neural = Neural({'μέντοι': 'μέντοι', 'ἂν': 'ἐάν', 'ἄν': 'ἐάν'})
    neural.score_many = lambda pairs: {(f,l): -1.37 if l == 'ἄν' else -0.61 if l == 'ἐάν' else 0 for f,l in pairs}
    w = process('μέντἂν', store, neural, Grammar({'μέντοι': Tag('ADV'), 'ἂν': Tag('ADV'), 'ἄν': Tag('ADV')}))[0]
    assert [p[1] for p in w.crasis] == ['μέντοι', 'ἄν']
    assert w.review_reasons
    candidates = w.evidence[0]['components'][1]['candidates']
    assert any(c['lemma'] == 'ἐάν' for c in candidates)
    assert candidates[0]['function_word_score'] == 1.0
