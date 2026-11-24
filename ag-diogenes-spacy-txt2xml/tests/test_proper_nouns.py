from ag_diogenes_spacy_txt2xml.proper_nouns import LOWER_CASE_FORM, apply_proper_noun_record


def record(form, lemma, pos, **extra):
    return dict(form=form, expansion=[form], lemma=lemma, pos=pos, resp='diogenes greberta',
                readings=[{'components': []}], review_reasons=[], **extra)


def test_machine_noun_with_capitalised_lemma_and_form_becomes_propn_once():
    r = record('Κλεωνύμου', 'Κλεώνυμος', 'NOUN')
    assert apply_proper_noun_record(r)['proper nouns'] == 1
    assert r['pos'] == 'PROPN' and r['review_reasons'] == []
    traces = [t for t in r['rule_contributions'] if t['rule_id'] == 'grc.grammar.proper-noun']
    assert len(traces) == 1 and traces[0]['status'] == 'applied'
    assert not apply_proper_noun_record(r)                          # idempotent
    assert len(r['readings'][0]['rule_contributions']) == 1


def test_lower_case_form_keeps_noun_and_is_listed_for_review_once():
    r = record('μυρρίνας', 'Μυρρίνη', 'NOUN')
    assert apply_proper_noun_record(r)['capitalised lemma, lower-case form'] == 1
    apply_proper_noun_record(r)
    assert r['pos'] == 'NOUN' and r['review_reasons'] == [LOWER_CASE_FORM]


def test_manual_and_reviewed_choices_and_other_classes_are_untouched():
    for r in (record('Κλέων', 'Κλέων', 'NOUN', reviewed=True),
              dict(record('Κλέων', 'Κλέων', 'NOUN'), resp='corrector'),
              record('Ἀθηναῖοι', 'Ἀθηναῖος', 'ADJ')):
        before = r['pos']
        assert not apply_proper_noun_record(r)
        assert r['pos'] == before


def test_crasis_components_are_assessed_separately():
    r = dict(record('κἀγώ', 'καί+ἐγώ', 'CCONJ+PRON'), expansion=['καί', 'ἐγώ'])
    assert not apply_proper_noun_record(r) and r['pos'] == 'CCONJ+PRON'
