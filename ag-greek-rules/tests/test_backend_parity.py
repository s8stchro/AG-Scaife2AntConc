"""Optional extraction check against backend 0.3.1; no model inference."""
import unicodedata
import unittest
from ag_greek_rules import assess_pronoun, catalog

try:
    from ag_diogenes_spacy_txt2xml import __version__ as backend_version
    from ag_diogenes_spacy_txt2xml.pronouns import pronoun_score
except ImportError:
    backend_version = None


@unittest.skipUnless(backend_version == '0.3.1', 'requires the declared source backend version 0.3.1')
class BackendParityTests(unittest.TestCase):
    def test_full_supported_sets_unicode_variants_and_following_contexts(self):
        parameters = catalog()['rules'][0]['parameters']
        base = parameters['interrogative_forms'] + parameters['indefinite_forms'] + ['τοῦ', 'τῷ', '', 'λόγος']
        forms = {variant for form in base for variant in (form, form.upper(), unicodedata.normalize('NFD', form))}
        following = [None, '', 'δέ', 'οὗτος'] + parameters['possible_following_enclitics']
        for form in forms:
            for lemma in ('τίς', 'τις', 'τίη'):
                for next_form in following:
                    with self.subTest(form=form, lemma=lemma, following=next_form):
                        expected = pronoun_score(lemma, form, following=next_form)
                        self.assertEqual(assess_pronoun(form, lemma, following=next_form).score_delta, expected)


if __name__ == '__main__':
    unittest.main()
