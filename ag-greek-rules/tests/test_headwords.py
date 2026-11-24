"""The headword convention is reusable without providers, models or a database."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
import unicodedata

from ag_greek_rules import assess_headword, headword_mappings
from ag_greek_rules.headwords import RULE_ID


def entry(lemma, ident='lsj:fixture:1', **kwargs):
    return {'headword': lemma, 'id': ident, 'verified': True, **kwargs}


class HeadwordTests(unittest.TestCase):
    def test_verified_identity_does_not_merge_homographs(self):
        entries = [entry('ἄπειμι', 'lsj:fixture:1'), entry('ἄπειμι', 'lsj:fixture:2')]
        before = deepcopy(entries)
        uncertain = assess_headword('ἀπιέναι', 'ἄπειμι', entries=entries)
        self.assertEqual(uncertain.reason, 'ambiguous_reference_identity')
        self.assertIsNone(uncertain.reference_id)
        chosen = assess_headword('ἀπιέναι', 'ἄπειμι', entries=entries, selected_reference_id='lsj:fixture:2')
        self.assertEqual(chosen.reference_id, 'lsj:fixture:2')
        self.assertEqual(entries, before)

    def test_documented_equivalents_need_a_verified_target(self):
        for original, target in [('τρία', 'τρεῖς'), ('χαίνω', 'χάσκω'), ('μόχθηρος', 'μοχθηρός')]:
            result = assess_headword(original, original, entries=[entry(target)])
            self.assertEqual((result.status, result.headword), ('applied', target))
            self.assertTrue(result.mapping_ids)
            self.assertEqual(assess_headword(original, original).status, 'abstained')
            self.assertEqual(assess_headword(original, original, entries=[entry(target, verified=False)]).headword, original)

    def test_scoped_aorist_mapping_does_not_rewrite_the_present(self):
        entries = [entry('προσεῖπον', 'aorist'), entry('προσλέγω', 'present')]
        aorist = assess_headword('προσεῖπον', 'προσλέγω', pos='VERB', entries=entries, form_entry_ids=['aorist'])
        self.assertEqual((aorist.headword, aorist.reference_id), ('προσεῖπον', 'aorist'))
        present = assess_headword('προσλέγω', 'προσλέγω', pos='VERB', entries=entries, form_entry_ids=['present'])
        self.assertEqual((present.headword, present.reference_id), ('προσλέγω', 'present'))

    def test_nominal_correspondence_does_not_choose_a_noun_over_a_verb(self):
        reference = [entry('κάνεον')]
        self.assertEqual(assess_headword('κανοῦν', 'κανοῦν', pos='NOUN', entries=reference).headword, 'κάνεον')
        for pos in ('VERB', None):
            self.assertEqual(assess_headword('κανοῦν', 'κανοῦν', pos=pos, entries=reference).status, 'abstained')
        self.assertEqual(assess_headword('κανοῦν', 'καίνω', pos='VERB', entries=reference).headword, 'καίνω')

    def test_provisional_links_cannot_claim_canonical_identity(self):
        result = assess_headword('ἡμῶν', 'ἡμός', entries=[entry('ἡμός')], provisional_link=True)
        self.assertEqual((result.status, result.reason), ('abstained', 'provisional_provider_link'))
        self.assertEqual(result.headword, 'ἡμός')
        self.assertIsNone(result.reference_id)

    def test_manual_and_separate_deictic_choices_remain_authoritative(self):
        manual = assess_headword('τρία', 'τρία', entries=[entry('τρεῖς')], manual=True)
        self.assertEqual((manual.status, manual.headword), ('not_applicable', 'τρία'))
        for form, lemma in [('αὑτηί', 'οὑτοσί'), ('νυνί', 'νυνί')]:
            result = assess_headword(form, lemma, entries=[entry('οὗτος'), entry('νῦν')])
            self.assertEqual((result.status, result.headword, result.reference_id), ('not_applicable', lemma, None))
        anchored = assess_headword('νυνί', 'νυνί', entries=[entry('νυνί', 'lsj:fixture:nuni')])
        self.assertEqual((anchored.status, anchored.headword, anchored.reference_id),
                         ('not_applicable', 'νυνί', 'lsj:fixture:nuni'))

    def test_normalization_preserves_linguistic_accents_and_raw_input(self):
        original = unicodedata.normalize('NFD', 'Τίς')
        result = assess_headword('Τίς', original, entries=[entry('τίς'), entry('τις', 'indefinite')], selected_reference_id='lsj:fixture:1')
        self.assertEqual(result.headword, 'τίς')
        self.assertEqual(result.candidate_lemma, original)
        self.assertEqual(assess_headword('ὁδοιπόρων', 'ὁδοάπορος', entries=[entry('ὁδοιπόρος')]).status, 'abstained')
        case_only = assess_headword('ἐπένευσαν', 'πανεύω', entries=[entry('Πανεύω')])
        self.assertEqual((case_only.headword, case_only.status), ('πανεύω', 'abstained'))

    def test_catalogue_and_cli_keep_mapping_sources_and_trace(self):
        maps = headword_mappings()
        self.assertEqual(len({m['id'] for m in maps['mappings']}), len(maps['mappings']))
        self.assertTrue(all(m['source'] and m['url'] and m['counterexample'] and m['version'] for m in maps['mappings']))
        request = dict(rule_id=RULE_ID, form='τρία', candidate_lemma='τρία', entries=[entry('τρεῖς')])
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-S', '-m', 'ag_greek_rules'],
            cwd=Path(__file__).resolve().parents[1], input=json.dumps(request), encoding='utf-8', capture_output=True, check=True)
        trace = json.loads(result.stdout)
        self.assertEqual(trace['headword'], 'τρεῖς')
        self.assertEqual(trace['rule_id'], RULE_ID)


if __name__ == '__main__': unittest.main()
