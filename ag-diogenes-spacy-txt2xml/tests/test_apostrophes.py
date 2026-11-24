import pytest

from ag_diogenes_spacy_txt2xml.crasis import Crasis
from ag_diogenes_spacy_txt2xml.expansion import Expander, load_elisions
from ag_diogenes_spacy_txt2xml.hybrid import Tag
from ag_diogenes_spacy_txt2xml.normalize import Normalizer
from ag_diogenes_spacy_txt2xml.source import Line, add_milestones
from ag_diogenes_spacy_txt2xml.tokenize import Token, Word, tokenize
from .test_hybrid import Grammar, Neural, TinyStore, a, process


def words(text, apostrophe='’'):
    lines = [Line(None, '1', Normalizer(apostrophe)(text))]
    add_milestones(lines)
    return [w.form for w in tokenize(lines, apostrophe) if isinstance(w, Word)]


@pytest.mark.parametrize('mark', ["'", '’', '‘', 'ʼ', '᾽', '᾿', '´'])
@pytest.mark.parametrize('chosen', ['’', "'", 'ʼ'])
def test_apostrophe_variants_preserve_both_elision_directions(mark, chosen):
    assert words(mark + 'γὼ δ' + mark + ' ἐγώ', chosen) == [chosen + 'γὼ', 'δ' + chosen, 'ἐγώ']


@pytest.mark.parametrize('mark', ["'", '’', 'ʼ', '᾽', '᾿', '\u0313'])
def test_glued_elision_is_split_consistently(mark):
    assert words('δ' + mark + 'αὐτόν') == ['δ’', 'αὐτόν']


def test_real_breathings_coronis_quotations_and_numerals_are_not_elisions():
    assert words('᾿Αθηναῖοι κἀγώ ῥήτωρ ‘λόγος’ λʹ') == ['Ἀθηναῖοι', 'κἀγώ', 'ῥήτωρ', 'λόγος']
    assert words('παρ\u0313 ἐμοί') == ['παρ’', 'ἐμοί']
    assert words('ἄῤῥητος') == ['ἄῤῥητος']


def test_combining_apostrophe_after_uncertain_letter_keeps_the_uncertainty():
    lines = [Line(None, '1', Normalizer()('δ\u0323\u0313 ἐγώ'))]
    add_milestones(lines)
    w = next(w for w in tokenize(lines) if isinstance(w, Word))
    assert w.form == 'δ’' and '\u0323' in w.pieces[0][1]


def test_initial_combining_apostrophe_is_preserved_without_changing_breathings():
    assert words('\u0313γώ αὐτόν') == ['’γώ', 'αὐτόν']


def test_initial_elision_is_restored_before_models_and_spelling_is_preserved():
    store = TinyStore({'ἐγώ': [a('ἐγώ', 'ἐγώ', 'PRON', 'Case=Nom|Person=1')]})
    neural, grammar = Neural({'ἐγώ': 'ἐγώ'}), Grammar({'ἐγώ': Tag('PRON', 'Case=Nom|Person=1')})
    w = process(Normalizer()("'γώ"), store, neural, grammar)[0]
    assert w.form == '’γώ' and w.expansion == ['ἐγώ'] and w.lemma == 'ἐγώ'
    assert grammar.seen == [['ἐγώ']] and neural.called == ['ἐγώ']


def test_initial_restoration_does_not_manufacture_double_accents():
    store = TinyStore({'ἐγώ': [a('ἐγώ', 'ἐγώ', 'PRON')],
                       'ἄγώ': [a('ἄγώ', 'ἄγω', 'VERB')]})
    expander = Expander(store, Crasis.load(), load_elisions())
    assert [r.atoms[0].form for r in expander.readings('’γώ')] == ['ἐγώ']


def test_nominal_diphthongs_and_optatives_are_not_generic_elision_restorations():
    store = TinyStore({'φέρε': [a('φέρε', 'φέρω', 'VERB', 'VerbForm=Fin|Mood=Imp')],
                       'φέροι': [a('φέροι', 'φέρω', 'VERB', 'VerbForm=Fin|Mood=Opt')],
                       'φεραί': [a('φεραί', 'Φεραί', 'PROPN', 'Case=Nom|Number=Plur')],
                       'ἔτι': [a('ἔτι', 'ἔτι', 'ADV')]})
    expander = Expander(store, Crasis.load(), load_elisions())
    assert [r.atoms[0].form for r in expander.readings('φέρ’')] == ['φέρε']
    assert expander.readings('ἔτ’')[0].atoms[0].form == 'ἔτι'


def test_poetic_verbal_ai_and_exceptional_oi_are_kept_with_visible_qualification():
    store = TinyStore({'βούλομαι': [a('βούλομαι', 'βούλομαι', 'VERB', 'VerbForm=Fin|Voice=Mid')],
                       'μοι': [a('μοι', 'ἐγώ', 'PRON', 'Case=Dat')],
                       'με': [a('με', 'ἐγώ', 'PRON', 'Case=Acc')]})
    expander = Expander(store, Crasis.load(), load_elisions())
    assert any(r.atoms[0].form == 'βούλομαι' for r in expander.readings('βούλομ’'))
    rare = next(r for r in expander.readings('μ’') if r.atoms[0].form == 'μοι')
    assert rare.prior < 0 and rare.notes


def test_restoration_does_not_supply_its_own_corroborating_context():
    store = TinyStore({'με': [a('με', 'ἐγώ', 'PRON', 'Case=Acc')],
                       'μοι': [a('μοι', 'ἐγώ', 'PRON', 'Case=Dat')]})
    grammar = Grammar({'με': Tag('PRON', 'Case=Acc'), 'μοι': Tag('PRON', 'Case=Dat')})
    w = process('μ’', store, Neural({'με': 'ἐγώ', 'μοι': 'ἐγώ'}), grammar)[0]
    assert w.expansion == ['με'] and w.msd == 'Case=Acc'
    assert grammar.seen == [['με']]
    alternative = next(r for r in w.evidence if r['expansion'] == ['μοι'])
    assert alternative['components'][0]['tagger']['msd'] == 'Case=Acc'
    assert alternative['components'][0]['context_form'] == 'με'


def test_known_particle_expansion_retains_lexical_hint_when_models_disagree():
    store = TinyStore({'δέ': [a('δέ', 'δέ', 'PART')],
                       "δ'": [a("δ'", 'δέ', 'PART')]})
    neural = Neural({'δέ': 'οὐδέ', "δ'": 'δέ'})
    # Real model near-tie: δέ is mistagged ADV and GreTa proposes οὐδέ.
    neural.score_many = lambda pairs: {(f, l): -0.61 if l == 'οὐδέ' else -0.72 for f, l in pairs}
    w = process('δ’', store, neural, Grammar({'δέ': Tag('ADV')}))[0]
    assert w.expansion == ['δέ'] and w.lemma == 'δέ'
    assert any('disagree' in reason for reason in w.review_reasons)


@pytest.mark.parametrize('mark', ["'", '’', 'ʼ', '᾽', '᾿'])
def test_elided_crasis_dictionary_recognises_all_apostrophe_spellings(mark):
    assert Crasis.load().match('κᾆτ' + mark) == (1, ['καί', 'εἶτα'])


def test_elision_can_hide_a_whole_crasis():
    store = TinyStore({'ἐγᾦδα': [a('ἐγᾦδα', 'οἶδα', 'VERB')],
                       'ἐγώ': [a('ἐγώ', 'ἐγώ', 'PRON')], 'οἶδα': [a('οἶδα', 'οἶδα', 'VERB')]})
    w = process('ἐγᾦδ’', store, Neural({'ἐγώ': 'ἐγώ', 'οἶδα': 'οἶδα'}))[0]
    assert w.expansion == ['ἐγώ', 'οἶδα']
    assert ''.join(c[0] for c in w.crasis) == 'ἐγᾦδ’'


def test_guessed_crasis_vowel_does_not_inherit_another_words_elision_preference():
    store = TinyStore({'καί': [a('καί', 'καί', 'CCONJ')],
                       'εἶτα': [a('εἶτα', 'εἶτα', 'ADV')],
                       'ἔτι': [a('ἔτι', 'ἔτι', 'ADV')]})
    neural = Neural({'καί': 'καί', 'εἶτα': 'εἶτα', 'ἔτι': 'ἔτι'})
    neural.score_many = lambda pairs: {(f, l): -0.7 if f == 'εἶτα' else -0.01 for f, l in pairs}
    w = process('κᾆτ’', store, neural, Grammar({'καί': Tag('CCONJ'), 'εἶτα': Tag('ADV')}))[0]
    assert w.expansion == ['καί', 'εἶτα']
    other = next(r for r in w.evidence if r['expansion'] == ['καί', 'ἔτι'])
    assert other['restoration_prior'] <= 0
