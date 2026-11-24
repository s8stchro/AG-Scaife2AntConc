import json
from pathlib import Path
import subprocess
import sys
import unittest
import unicodedata

from ag_greek_rules import assess_deictic, catalog
from ag_greek_rules.deictic import FAMILIES, HEADWORDS, RULE_ID


class DeicticRuleTests(unittest.TestCase):
    def test_supported_forms_share_the_suffixed_headword_and_one_token(self):
        for base, forms in FAMILIES.items():
            for form in forms:
                for variant in (form, form.upper(), form.replace('ί', 'ὶ'), unicodedata.normalize('NFD', form)):
                    with self.subTest(form=variant):
                        result = assess_deictic(variant, base)
                        self.assertEqual(result.status, 'applied')
                        self.assertEqual(result.headword, HEADWORDS[base])
                        self.assertEqual(result.token_count, 1)
                        self.assertEqual(result.form, variant)
                        self.assertEqual(result.candidate_lemma, base)

    def test_already_suffixed_headwords_remain_distinct_from_base(self):
        for form, lemma in [('νυνί', 'νυνί'), ('οὑτοσί', 'οὑτοσί'), ('τουτονί', 'οὑτοσί')]:
            result = assess_deictic(form, lemma)
            self.assertEqual(result.headword, lemma)
            self.assertEqual(result.status, 'applied')
            self.assertNotEqual(result.headword, result.base_lemma)

    def test_unknown_endings_compounds_and_unsuffixed_words_are_not_rewritten(self):
        for form in ('τί', 'τι', 'περί', 'ἐστί', 'οὗτος', 'νῦν', 'νυν', 'νυνγαρί', 'τουτογί', 'λόγοι', 'νυνι', 'καὶ'):
            self.assertEqual(assess_deictic(form, form).status, 'not_applicable')

    def test_unrelated_or_missing_selected_lemma_abstains(self):
        for lemma in ('ἑαυτοῦ', 'δείκνυμι', ''):
            result = assess_deictic('αὑτηί', lemma)
            self.assertEqual(result.status, 'abstained')
            self.assertEqual(result.candidate_lemma, lemma)

    def test_catalogue_examples_match_the_independent_interface(self):
        rule = next(r for r in catalog()['rules'] if r['id'] == RULE_ID)
        for example in rule['examples']:
            result = assess_deictic(**example['input']).as_dict()
            for name, value in example['expected'].items():
                self.assertEqual(result[name], value)

    def test_cli_without_models_or_site_packages(self):
        request = dict(rule_id=RULE_ID, form='τουτονὶ', candidate_lemma='οὗτος')
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-S', '-m', 'ag_greek_rules'],
            cwd=Path(__file__).resolve().parents[1], input=json.dumps(request), encoding='utf-8', capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['headword'], 'οὑτοσί')

    def test_expanded_attic_paradigms_and_families(self):
        for form, base, headword in [
            ('αὑταιί', 'οὗτος', 'οὑτοσί'),
            ('τουτουσί', 'οὗτος', 'οὑτοσί'),
            ('τουτοισίν', 'οὗτος', 'οὑτοσί'),
            ('τονδί', 'ὅδε', 'ὁδί'),
            ('ἐκεινονί', 'ἐκεῖνος', 'ἐκεινοσί'),
            ('τοσουτί', 'τοσοῦτος', 'τοσουτοσί'),
            ('τοιουτοσί', 'τοιοῦτος', 'τοιουτοσί'),
            ('ἐνθαδί', 'ἐνθάδε', 'ἐνθαδί'),
            ('δευρί', 'δεῦρο', 'δευρί'),
        ]:
            result = assess_deictic(form, base)
            self.assertEqual((result.status, result.headword), ('applied', headword))
