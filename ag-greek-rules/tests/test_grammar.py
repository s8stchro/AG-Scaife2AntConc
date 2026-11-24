import json
from pathlib import Path
import subprocess
import sys
import unittest

from ag_greek_rules import assess_pos


class CorroboratedPosTests(unittest.TestCase):
    def assess(self, surface=None, lemma=None, original='ADJ'):
        return assess_pos('προσεῖπον', 'προσλέγω', original_pos=original,
                          form_analyses=surface if surface is not None else [{'pos': 'VERB', 'msd': 'VerbForm=Fin'}],
                          lemma_analyses=lemma if lemma is not None else [{'pos': 'VERB'}])

    def test_finite_form_and_headword_agree(self):
        result = self.assess()
        self.assertEqual((result.status, result.proposed_pos, result.clear_msd), ('applied', 'VERB', True))
        self.assertEqual(result.original_pos, 'ADJ')
        self.assertEqual(result.candidate_lemma, 'προσλέγω')

    def test_unknown_missing_and_ambiguous_evidence_abstains(self):
        for surface, lemma in [([], [{'pos': 'VERB'}]), ([{'pos': 'VERB'}], []),
                               ([{'pos': None}], [{'pos': 'VERB'}]),
                               ([{'pos': 'X'}], [{'pos': 'VERB'}]),
                               ([{'pos': 'VERB'}, {'pos': 'NOUN'}], [{'pos': 'VERB'}]),
                               ([{'pos': 'VERB'}], [{'pos': 'VERB'}, {'pos': None}]),
                               ([{'pos': 'VERB'}], [{'pos': 'NOUN'}])]:
            with self.subTest(surface=surface, lemma=lemma):
                self.assertEqual(self.assess(surface, lemma).status, 'abstained')

    def test_nonfinite_and_unspecified_verbs_abstain(self):
        for msd in ('VerbForm=Part', 'VerbForm=Inf', '', 'VerbForm=Fin,Part'):
            self.assertEqual(self.assess([{'pos': 'VERB', 'msd': msd}]).status, 'abstained')

    def test_compatible_functional_labels_are_retained(self):
        self.assertEqual(self.assess(original='AUX').status, 'not_applicable')
        result = self.assess([{'pos': 'PART'}], [{'pos': 'PART'}], 'ADV')
        self.assertEqual(result.status, 'not_applicable')

    def test_json_lines_interface_without_external_packages(self):
        request = dict(rule_id='grc.grammar.corroborated-pos', form='προσεῖπον', candidate_lemma='προσλέγω',
                       original_pos='ADJ', form_analyses=[{'pos': 'VERB', 'msd': 'VerbForm=Fin'}],
                       lemma_analyses=[{'pos': 'VERB'}])
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-S', '-m', 'ag_greek_rules'],
                                cwd=Path(__file__).resolve().parents[1], input=json.dumps(request),
                                encoding='utf-8', capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)['proposed_pos'], 'VERB')
