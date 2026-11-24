"""The first portable rule, extracted without changing its existing decisions."""
from dataclasses import asdict, dataclass
from importlib.resources import files
import json
import re
import unicodedata

RULE_ID = 'grc.pronoun.interrogative-indefinite-accent'


def catalog():
    """Return fresh JSON-compatible metadata, including backend-only rule records."""
    return json.loads(files(__package__).joinpath('catalog.json').read_text(encoding='utf-8'))


_RULE = next(r for r in catalog()['rules'] if r['id'] == RULE_ID)
_PARAMETERS = _RULE['parameters']
_INTERROGATIVE = frozenset(_PARAMETERS['interrogative_forms'])
_INDEFINITE = frozenset(_PARAMETERS['indefinite_forms'])
_ENCLITICS = frozenset(_PARAMETERS['possible_following_enclitics'])
_ELIDED_ENCLITICS = frozenset(_PARAMETERS.get('possible_following_elided_enclitics', ()))
_APOS_LIKE = "'´‘’ʼ᾽᾿"
_VOWELS = frozenset('αεηιουω')
_DIPHTHONGS = frozenset(('αι', 'ει', 'οι', 'υι', 'αυ', 'ευ', 'ηυ', 'ου'))
_ACCENTS = frozenset(('\u0301', '\u0300', '\u0342'))


@dataclass(frozen=True)
class Assessment:
    rule_id: str
    rule_version: str
    status: str                   # applied / abstained / not_applicable
    reason: str                   # stable code, independent of display language
    score_delta: float
    form: str
    candidate_lemma: str
    following_form: str | None
    expected_lemma: str | None

    def as_dict(self):
        """A serialisable trace; original forms and proposals remain unmodified."""
        return asdict(self)


def _key(form):
    # Same comparison as the backend's diogenes.key, implemented with stdlib only.
    nfd = unicodedata.normalize('NFD', form.lower()).replace('\u0300', '\u0301')
    return ''.join("'" if c in _APOS_LIKE else c for c in unicodedata.normalize('NFC', nfd))


def _bare(form):
    nfd = unicodedata.normalize('NFD', re.sub(r'\d+$', '', form).lower())
    return ''.join(c for c in nfd if not unicodedata.combining(c))


def _has_orthotonic_accent(word):
    """True if `word` bears an accent incompatible with a following enclitic (Smyth §181).

    An enclitic is unaccented, or (if disyllabic) bears an accent only on its ultima.
    Monosyllabic circumflexes (νῦν, πῶς, ποῦ, πῇ, ποῖ) and non-ultima accents
    (πότε, πόθεν, εἶμι, ἔστι) are orthotonic and cannot throw an accent onto τίς/τί.
    """
    nfd = unicodedata.normalize('NFD', word.rstrip(_APOS_LIKE).lower())
    letters = []
    for ch in nfd:
        if unicodedata.combining(ch) and letters:
            letters[-1] += ch
        elif not unicodedata.combining(ch):
            letters.append(ch)
    syllables = []
    i = 0
    while i < len(letters):
        base = letters[i][0]
        if base in _VOWELS:
            if (i + 1 < len(letters) and letters[i + 1][0] in 'ιυ'
                    and (base + letters[i + 1][0]) in _DIPHTHONGS
                    and '\u0308' not in letters[i + 1]):
                syllables.append(letters[i] + letters[i + 1])
                i += 2
            else:
                syllables.append(letters[i])
                i += 1
        else:
            i += 1
    if not syllables:
        return False
    if any(ch in _ACCENTS for syl in syllables[:-1] for ch in syl):
        return True
    if len(syllables) == 1 and '\u0342' in syllables[0]:
        return True
    return False


def _is_possible_enclitic(following):
    if not following or _has_orthotonic_accent(following):
        return False
    stripped = following.rstrip(_APOS_LIKE)
    if stripped != following:
        return _bare(stripped) in _ELIDED_ENCLITICS
    return _bare(following) in _ENCLITICS


def assess_pronoun(form, candidate_lemma, *, following=None):
    """Assess an existing candidate; the caller decides how to use the score.

    `following` is the immediately following word in the same uninterrupted
    phrase, or None at punctuation/boundaries. No POS features are required:
    the existing backend's reserved `msd` argument does not affect this rule.
    No missing candidates are invented and no final lemma is selected here.
    """
    if not isinstance(form, str) or not isinstance(candidate_lemma, str):
        raise TypeError('form and candidate_lemma must be strings')
    if following is not None and not isinstance(following, str):
        raise TypeError('following must be a string or None')
    def result(status, reason, delta=0.0, expected=None):
        return Assessment(RULE_ID, _RULE['version'], status, reason, delta,
                          form, candidate_lemma, following, expected)
    if candidate_lemma not in ('τίς', 'τις'):
        return result('not_applicable', 'candidate_outside_pronoun_family')
    original = unicodedata.normalize('NFC', form.lower())
    key = _key(form)
    if original in ('τὶ', 'τὶς'):
        expected, reason = 'τις', 'indefinite_grave'
    elif key in _INTERROGATIVE:
        if key in ('τίς', 'τί') and _is_possible_enclitic(following):
            return result('abstained', 'possible_chained_enclitic')
        expected, reason = 'τίς', 'interrogative_accent'
    elif key in _INDEFINITE:
        expected, reason = 'τις', 'indefinite_spelling'
    else:
        return result('not_applicable', 'unsupported_surface')
    delta = _PARAMETERS['support_delta'] if candidate_lemma == expected else _PARAMETERS['conflict_delta']
    return result('applied', reason, float(delta), expected)
