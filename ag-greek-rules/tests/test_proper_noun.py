import unittest

from ag_greek_rules import assess_proper_noun, catalog


class ProperNounTests(unittest.TestCase):
    def test_capitalised_lemma_and_form_make_a_proper_noun(self):
        for form, lemma in (('Κλεωνύμου', 'Κλεώνυμος'), ('Ζεῦ', 'Ζεύς'), ("'Ἀπόλλων", 'Ἀπόλλων'), ('ΣΩΚΡΑΤΗΣ', 'Σωκράτης')):
            result = assess_proper_noun(form, lemma, original_pos='NOUN')
            self.assertEqual((result.status, result.proposed_pos), ('applied', 'PROPN'), form)

    def test_lower_case_form_abstains_for_review(self):
        result = assess_proper_noun('μυρρίνας', 'Μυρρίνη', original_pos='NOUN')
        self.assertEqual((result.status, result.reason, result.proposed_pos),
                         ('abstained', 'capitalised_lemma_lower_case_form', None))

    def test_other_classes_and_common_nouns_are_untouched(self):
        cases = [('Ἀθηναῖοι', 'Ἀθηναῖος', 'ADJ'), ('Ἀθήναζε', 'Ἀθήναζε', 'ADV'), ('Μεγαριεῖς', 'Μεγαρίζω', 'VERB'),
                 ('Λόγος', 'λόγος', 'NOUN'), ('Κλέων', 'Κλέων', 'PROPN'), ('Χ', '', 'NOUN')]
        for form, lemma, pos in cases:
            self.assertEqual(assess_proper_noun(form, lemma, original_pos=pos).status, 'not_applicable', form)

    def test_catalogue_examples_match_the_implementation(self):
        rule = next(r for r in catalog()['rules'] if r['id'] == 'grc.grammar.proper-noun')
        for example in rule['examples']:
            data = dict(example['input'])
            result = assess_proper_noun(data.pop('form'), data.pop('candidate_lemma'), **data)
            self.assertEqual(result.status, example['status'])
            if 'proposed_pos' in example:
                self.assertEqual(result.proposed_pos, example['proposed_pos'])
            if 'reason' in example:
                self.assertEqual(result.reason, example['reason'])


if __name__ == '__main__':
    unittest.main()
