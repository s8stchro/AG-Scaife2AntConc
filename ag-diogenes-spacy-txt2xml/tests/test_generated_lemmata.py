from ag_diogenes_spacy_txt2xml.generated_lemmata import REMOVED, REPLACED, check_generated_lemmata
from ag_diogenes_spacy_txt2xml.tokenize import Word


def word(form, lemma, *, lexical, log_p, lemma_id=None, resp='greberta', **extra):
    w = Word([('t', form)], lemma=lemma, pos='NOUN', resp=resp, lemma_id=lemma_id, **extra)
    w.evidence = [{'components': [{'selection': {'lexical_lemmas': lexical, 'selected_by': 'diogenes' if lexical else 'context'},
                                   'context': {'script_log_probability': log_p}}], 'rule_contributions': []}]
    return w


def test_the_text_supplies_the_lemma_and_impossible_inventions_are_removed():
    words = [word('Ποσείδιππον', 'Ποσείδιππος', lexical=[], log_p=-0.03, lemma_id='greberta:Ποσείδιππος'),
             word('Ποσειδίππῳ', 'Ποσειδέπαος', lexical=[], log_p=-3.2),
             word('Ποσειδίππῳ', 'Ποσειδεώπ', lexical=[], log_p=-2.39),
             word('Κλεωνύμου', 'Κλεώνυμος', lexical=['Κλεώνυμος'], log_p=-5.0, lemma_id='morpheus:1'),
             word('τοφλαττοθρατ', 'τοφλαττοθρατ', lexical=[], log_p=-0.05),
             word('ἐξάρξαν', 'ἐξάρχω', lexical=[], log_p=-2.97)]
    stats = check_generated_lemmata(words)
    assert stats == {'generated lemmata replaced': 2, 'generated lemmata removed': 1}
    assert [w.lemma for w in words] == ['Ποσείδιππος', 'Ποσείδιππος', 'Ποσείδιππος', 'Κλεώνυμος', None, 'ἐξάρχω']
    assert words[1].lemma_id == 'greberta:Ποσείδιππος' and REPLACED in words[1].review_reasons
    assert REMOVED in words[4].review_reasons and words[4].lemma_id is None
    assert words[1].evidence[0]['rule_contributions'][0]['rule_id'] == 'grc.selection.generated-lemma-check'
    assert not check_generated_lemmata(words)                    # idempotent


def test_manual_choices_and_crasis_are_untouched_but_count_as_reliable():
    manual = word('Ποσείδιππον', 'Ποσείδιππος', lexical=[], log_p=-4.0, resp='corrector')
    reviewed = word('τοφλαττοθρατ', 'τοφλαττοθρατ', lexical=[], log_p=-0.05, reviewed=True)
    invented = word('Ποσειδίππῳ', 'Ποσειδεώπ', lexical=[], log_p=-2.39)
    crasis = word('κἀκυκλοβόρει', 'ἀκυκλοαόρέω', lexical=[], log_p=-4.3)
    crasis.crasis = [('κ', 'καί', 'CCONJ', 'lexicon'), ('ἀκυκλοβόρει', 'ἀκυκλοαόρέω', 'VERB', 'greberta')]
    check_generated_lemmata([manual, reviewed, invented, crasis])
    assert (manual.lemma, reviewed.lemma, invented.lemma) == ('Ποσείδιππος', 'τοφλαττοθρατ', 'Ποσείδιππος')
    assert crasis.crasis[1][1] == 'ἀκυκλοαόρέω'
