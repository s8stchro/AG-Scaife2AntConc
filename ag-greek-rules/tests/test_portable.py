import json
from pathlib import Path
import subprocess
import sys
import unicodedata
import unittest

from ag_greek_rules import assess_pronoun, catalog


class PortableRuleTests(unittest.TestCase):
    def test_catalogue_examples_are_executable(self):
        rule = next(r for r in catalog()['rules'] if r['availability'] == 'portable')
        for example in rule['examples']:
            with self.subTest(example=example):
                result = assess_pronoun(**example['input'])
                self.assertEqual(result.score_delta, example['score_delta'])
                self.assertEqual(result.status, example['status'])

    def test_weights_support_or_conflict_without_selecting_a_lemma(self):
        self.assertEqual(assess_pronoun('τί', 'τίς').score_delta, 2)
        self.assertEqual(assess_pronoun('τί', 'τις').score_delta, -2)
        self.assertEqual(assess_pronoun('τι', 'τις').score_delta, 2)
        self.assertEqual(assess_pronoun('τι', 'τίς').score_delta, -2)

    def test_enclitic_exception_abstains_for_both_candidates(self):
        for lemma in ('τίς', 'τις'):
            result = assess_pronoun('τίς', lemma, following='τινα')
            self.assertEqual((result.status, result.score_delta), ('abstained', 0))
            self.assertIsNone(result.expected_lemma)

    def test_contracts_and_unrelated_candidates_are_outside_scope(self):
        for form, lemma in [('τοῦ', 'τίς'), ('τῷ', 'τις'), ('τί', 'τίη')]:
            result = assess_pronoun(form, lemma)
            self.assertEqual((result.status, result.score_delta), ('not_applicable', 0))

    def test_trace_preserves_original_unicode_and_case(self):
        form = unicodedata.normalize('NFD', 'Τίς')
        result = assess_pronoun(form, 'τίς', following='οὗτος')
        self.assertEqual(result.form, form)
        self.assertEqual(result.following_form, 'οὗτος')
        self.assertEqual(result.score_delta, 2)
        self.assertEqual(json.loads(json.dumps(result.as_dict(), ensure_ascii=False))['form'], form)

    def test_catalogue_callers_cannot_mutate_the_active_rule(self):
        data = catalog()
        data['rules'][0]['parameters']['support_delta'] = 999
        self.assertEqual(assess_pronoun('τί', 'τίς').score_delta, 2)
        self.assertEqual(catalog()['rules'][0]['parameters']['support_delta'], 2)

    def test_rule_ids_versions_and_provenance_are_recorded(self):
        data = catalog()
        self.assertEqual(len({r['id'] for r in data['rules']}), len(data['rules']))
        for rule in data['rules']:
            self.assertTrue(rule['version'])
            self.assertIn(rule['availability'], ('portable', 'backend_only'))
            self.assertTrue(rule['conditions'] and rule['effect'] and rule['sources'])
            reference = rule.get('backend_reference') or rule['implementation_reference']
            self.assertEqual(len(reference['sha256']), 64)
            self.assertIsNone(rule['validation']['independent_accuracy'])

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(TypeError):
            assess_pronoun(None, 'τίς')
        with self.assertRaises(TypeError):
            assess_pronoun('τί', 'τίς', following=3)

    def test_json_lines_cli_runs_without_site_packages(self):
        requests = [dict(form='τί', candidate_lemma='τίς', following='δέ'),
                    dict(form='τίς', candidate_lemma='τίς', following='τινα')]
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-S', '-m', 'ag_greek_rules'],
                                cwd=Path(__file__).resolve().parents[1],
                                input='\n'.join(json.dumps(r, ensure_ascii=False) for r in requests),
                                encoding='utf-8', capture_output=True, check=True)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r['status'] for r in rows], ['applied', 'abstained'])

    def test_json_lines_cli_reports_bad_requests_and_continues(self):
        lines = ['{"rule_id":"unported-rule","form":"τί","candidate_lemma":"τίς"}',
                 '{"form":"τί","candidate_lemma":"τίς","followng":"τινα"}',
                 '{"form":"τι","candidate_lemma":"τις"}']
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-S', '-m', 'ag_greek_rules'],
                                cwd=Path(__file__).resolve().parents[1], input='\n'.join(lines),
                                encoding='utf-8', capture_output=True)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(result.returncode, 1)
        self.assertIn('error', rows[0])
        self.assertIn('error', rows[1])
        self.assertEqual(rows[2]['score_delta'], 2)


if __name__ == '__main__':
    unittest.main()
