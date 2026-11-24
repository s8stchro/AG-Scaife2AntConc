"""Check lemmata that the contextual model generated for forms without any local analysis.

Owner decision, 24 November 2026. A form that Diogenes does not know takes the contextual
model's proposal, which can be an invented string (Isaeus 1 Ποσειδίππῳ -> Ποσειδεώπ, log p -2.4,
while Ποσείδιππον two sections earlier is Ποσείδιππος with log p -0.03). Two checks, in order:

1. Same word elsewhere in the text. A low-confidence proposal (log p <= LOW_CONFIDENCE) whose own
   stem does not begin the form is replaced by the one reliable lemma of the text whose stem does
   (Ποσειδίππῳ -> Ποσείδιππος). Reliable: a lemma from a local analysis or a manual choice, or a
   generated lemma with log p > RELIABLE. Several matching lemmata: no replacement.
2. Impossible spelling. A proposal that cannot be a Greek headword (two accents, an initial vowel
   without breathing, a final letter no Greek word ends in, a non-letter) is removed: no lemma.

Both outcomes are review observations. The proposal itself stays in the evidence.
"""
from dataclasses import asdict, dataclass
from functools import lru_cache
import unicodedata

RULE_ID = 'grc.selection.generated-lemma-check'
RULE_VERSION = '1.0.0'
LOW_CONFIDENCE = -1.0       # log p at or below which a generated lemma may be replaced
RELIABLE = -0.5             # log p above which a generated lemma may serve as the text's lemma
MIN_STEM = 5                # letters of a lemma stem that must begin the form
MAX_ENDING = 4              # letters of the form after that stem

_ACCENTS = ('́', '̀', '͂')
_BREATHINGS = ('̓', '̔')
_VOWELS = set('αεηιουω')
_FINALS = set('αεηιουωνρςξψ')
_CONSONANT_FINAL = {'ἐκ', 'ἐξ', 'οὐκ', 'οὐχ'}
# Citation-form endings removed to find a lemma's stem (longest first).
_ENDINGS = sorted({'ος', 'ης', 'ας', 'ων', 'ις', 'υς', 'ευς', 'ους', 'ως', 'α', 'η', 'ον', 'ω', 'ωρ', 'ηρ',
                   'ξ', 'ψ', 'ομαι', 'εω', 'αω', 'οω', 'μι', 'υμι', 'ημι'}, key=len, reverse=True)


@dataclass(frozen=True)
class GeneratedLemmaAssessment:
    rule_id: str
    rule_version: str
    status: str
    reason: str
    form: str
    candidate_lemma: str
    log_probability: float | None
    proposed_lemma: str | None = None
    problems: tuple = ()

    def as_dict(self):
        data = asdict(self)
        data['problems'] = list(self.problems)
        return data


def bare(text):
    """Lower case without diacritics; final sigma as σ."""
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if not unicodedata.combining(c)).replace('ς', 'σ')


@lru_cache(maxsize=65536)     # pure; a text's reliable lemmata are compared with every candidate
def stem(lemma):
    """The lemma without its citation ending, if at least MIN_STEM - 1 letters remain."""
    b = bare(lemma)
    for ending in _ENDINGS:
        e = bare(ending)
        if b.endswith(e) and len(b) - len(e) >= MIN_STEM - 1:
            return b[:-len(e)]
    return b if len(b) >= MIN_STEM else None


def headword_problems(lemma):
    """Reasons a string cannot be a Greek dictionary headword; empty if none is found."""
    out = []
    nfd = unicodedata.normalize('NFD', lemma)
    if sum(c in _ACCENTS for c in nfd) > 1:
        out.append('two_accents')
    if not nfd or not all(unicodedata.category(c).startswith('L') or unicodedata.combining(c) for c in nfd):
        out.append('non_letter')
    letters = []
    for c in nfd:
        if unicodedata.combining(c):
            if letters: letters[-1] += c
        else:
            letters.append(c)
    if letters and lemma not in _CONSONANT_FINAL and letters[-1][0].lower() not in _FINALS:
        out.append('impossible_final_letter')
    if letters and letters[0][0].lower() in _VOWELS:
        marks = letters[0][1:]
        if len(letters) > 1 and letters[1][0].lower() in 'ιυ' and '̈' not in letters[1]:
            marks += letters[1][1:]          # a diphthong carries its breathing on the second vowel
        if not any(b in marks for b in _BREATHINGS):
            out.append('initial_vowel_without_breathing')
    return tuple(out)


def _fits(lemma_stem, form):
    return bool(lemma_stem) and len(lemma_stem) >= MIN_STEM and form.startswith(lemma_stem) and \
        len(form) - len(lemma_stem) <= MAX_ENDING


def assess_generated_lemma(form, candidate_lemma, *, log_probability, reliable_lemmata=()):
    """Assess a contextual lemma generated for a form with no local analysis.

    `reliable_lemmata` are the other reliable lemmata of the same text (see the module docstring).
    Status `applied` with `proposed_lemma` replaces the lemma; with `proposed_lemma` None it removes it.
    """
    if not isinstance(form, str) or not isinstance(candidate_lemma, str):
        raise TypeError('form and candidate_lemma must be strings')

    def result(status, reason, proposed=None, problems=()):
        return GeneratedLemmaAssessment(RULE_ID, RULE_VERSION, status, reason, form, candidate_lemma,
                                        log_probability, proposed, tuple(problems))

    if not candidate_lemma:
        return result('not_applicable', 'no_proposal')
    written = bare(form.rstrip("'’ʼ᾽"))
    low = log_probability is None or log_probability <= LOW_CONFIDENCE
    own = stem(candidate_lemma)
    if low and not (own and written.startswith(own)):
        capital = form[:1].isupper()
        hits = {lemma for lemma in reliable_lemmata
                if lemma and lemma != candidate_lemma and lemma[:1].isupper() == capital
                and _fits(stem(lemma), written)}
        if len(hits) == 1:
            return result('applied', 'same_word_in_text', hits.pop(), headword_problems(candidate_lemma))
    problems = headword_problems(candidate_lemma)
    if problems:
        return result('applied', 'impossible_spelling', None, problems)
    return result('not_applicable', 'plausible_proposal')
