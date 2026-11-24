"""Proper nouns: a capitalised headword written with a capital is PROPN, not NOUN.

The contextual tagger (trained on AGDT-style treebanks) has no PROPN label and Morpheus gives
no part of speech for nouns, so every name arrives as NOUN. Owner decision, 24 November 2026:
NOUN becomes PROPN when both the selected lemma and the written form begin with a capital.
A capitalised lemma on a lower-case form is left NOUN and reported for review: it is mostly a
wrongly capitalised lemma (μυρρίνας -> Μυρρίνη), sometimes a personification. Adjectives
(ethnics such as Ἀθηναῖος), adverbs (Ἀθήναζε) and verbs (Μεγαρίζω) keep their class.
"""
from dataclasses import asdict, dataclass

RULE_ID = 'grc.grammar.proper-noun'
RULE_VERSION = '1.0.0'


@dataclass(frozen=True)
class ProperNounAssessment:
    rule_id: str
    rule_version: str
    status: str
    reason: str
    form: str
    candidate_lemma: str
    original_pos: str | None
    proposed_pos: str | None = None

    def as_dict(self):
        return asdict(self)


def _initial_is_capital(text):
    """The first letter decides; apostrophes, editorial signs and digits before it are ignored."""
    letter = next((c for c in text if c.isalpha()), '')
    return letter.isupper()


def assess_proper_noun(form, candidate_lemma, *, original_pos):
    if not isinstance(form, str) or not isinstance(candidate_lemma, str):
        raise TypeError('form and candidate_lemma must be strings')

    def result(status, reason, proposed=None):
        return ProperNounAssessment(RULE_ID, RULE_VERSION, status, reason, form, candidate_lemma,
                                    original_pos, proposed)

    if original_pos != 'NOUN':
        return result('not_applicable', 'not_a_noun')
    if not candidate_lemma or not _initial_is_capital(candidate_lemma):
        return result('not_applicable', 'lower_case_lemma')
    if not _initial_is_capital(form):
        return result('abstained', 'capitalised_lemma_lower_case_form')
    return result('applied', 'capitalised_lemma_and_form', 'PROPN')
