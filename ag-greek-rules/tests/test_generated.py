import unittest

from ag_greek_rules import assess_generated_lemma, catalog, headword_problems


class GeneratedLemmaTests(unittest.TestCase):
    def test_impossible_spellings_are_recognised(self):
        self.assertIn('impossible_final_letter', headword_problems('Ποσειδεώπ'))
        self.assertIn('two_accents', headword_problems('ἀκυκλοαόρέω'))
        self.assertIn('initial_vowel_without_breathing', headword_problems('ύαΐιος'))
        for good in ('Ποσείδιππος', 'ἐξάρχω', 'αὐτός', 'Εὔβοια', 'ἐκ', 'οὐκ', 'τις', 'ἄνθρωπος', 'γαστήρ', 'ἅλς'):
            self.assertEqual(headword_problems(good), (), good)

    def test_same_word_in_the_text_replaces_a_low_confidence_invention(self):
        for invented, log_p in (('Ποσειδέπαος', -3.2), ('Ποσειδεώπ', -2.39)):
            result = assess_generated_lemma('Ποσειδίππῳ', invented, log_probability=log_p,
                                            reliable_lemmata={'Ποσείδιππος', 'Κλεώνυμος'})
            self.assertEqual((result.status, result.reason, result.proposed_lemma),
                             ('applied', 'same_word_in_text', 'Ποσείδιππος'))

    def test_a_fitting_proposal_or_an_ambiguous_match_is_kept(self):
        fits = assess_generated_lemma('Δήμητρι', 'Δημήτηρ', log_probability=-2.06,
                                      reliable_lemmata={'Δημητρίη'})
        self.assertEqual(fits.status, 'not_applicable')
        ambiguous = assess_generated_lemma('ἀνθρώποισι', 'ἀάθρυπος', log_probability=-3.6,
                                           reliable_lemmata={'ἀνθρώπειος', 'ἀνθρώπιος'})
        self.assertEqual(ambiguous.reason, 'plausible_proposal')

    def test_confident_or_plausible_proposals_are_kept_and_impossible_ones_removed(self):
        confident = assess_generated_lemma('Ποσειδίππῳ', 'Ποσειδέπαος', log_probability=-0.2,
                                           reliable_lemmata={'Ποσείδιππος'})
        self.assertEqual(confident.status, 'not_applicable')
        removed = assess_generated_lemma('τοφλαττοθρατ', 'τοφλαττοθρατ', log_probability=-0.04)
        self.assertEqual((removed.status, removed.reason, removed.proposed_lemma), ('applied', 'impossible_spelling', None))

    def test_catalogue_examples_match_the_implementation(self):
        rule = next(r for r in catalog()['rules'] if r['id'] == 'grc.selection.generated-lemma-check')
        for example in rule['examples']:
            data = dict(example['input'])
            result = assess_generated_lemma(data.pop('form'), data.pop('candidate_lemma'), **data)
            self.assertEqual((result.status, result.reason), (example['status'], example['reason']))
            if 'proposed_lemma' in example:
                self.assertEqual(result.proposed_lemma, example['proposed_lemma'])


if __name__ == '__main__':
    unittest.main()
