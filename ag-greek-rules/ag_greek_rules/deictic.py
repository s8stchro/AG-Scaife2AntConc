"""Finite suffixed-headword policy for deictic iota; no models or token splitting."""
from dataclasses import asdict, dataclass
import unicodedata

RULE_ID = 'grc.lemma.deictic-iota-headword'
RULE_VERSION = '1.1.0'

# Original mapping, transcribed from cited grammatical/lexical examples, not a
# copy of provider data. Every spelling is deliberate; unsupported forms abstain.
FAMILIES = {
    'οὗτος': ('οὑτοσί', 'αὑτηί', 'αὑτηΐ', 'τουτί', 'τουτουί', 'τουτουΐ', 'τουτῳί',
              'τουτονί', 'ταυτηνί', 'ταυτηί', 'ταυτηΐ', 'ταυτησί', 'οὑτοιί', 'οὑτοιΐ',
              'αὑταιί', 'ταυτί', 'τουτωνί', 'τουτοισί', 'τουτοισίν', 'ταυταισί', 'ταυταισίν',
              'τουτουσί', 'ταυτασί'),
    'ὅδε': ('ὁδί', 'ἡδί', 'τοδί', 'τουδί', 'τησδί', 'τῳδί', 'τῃδί', 'τονδί', 'τηνδί',
            'οἱδί', 'αἱδί', 'ταδί', 'τωνδί', 'τοισδί', 'ταισδί', 'τοισιδί', 'τουσδί', 'τασδί'),
    'ἐκεῖνος': ('ἐκεινοσί', 'ἐκεινηί', 'ἐκεινί', 'ἐκεινουί', 'ἐκεινῳί', 'ἐκεινονί', 'ἐκεινηνί',
                'ἐκεινοιί', 'ἐκεινουσί', 'ἐκεινασί'),
    'τοσοῦτος': ('τοσουτοσί', 'τοσαυτηί', 'τοσουτί', 'τοσαυτί', 'τοσουτουί', 'τοσουτονί'),
    'τοιοῦτος': ('τοιουτοσί', 'τοιαυτηί', 'τοιουτί', 'τοιαυτί', 'τοιουτονί'),
    'τηλικοῦτος': ('τηλικουτοσί', 'τηλικαυτηί', 'τηλικουτί', 'τηλικουτονί'),
    'οὕτως': ('οὑτωσί',),
    'ὧδε': ('ὡδί',),
    'νῦν': ('νυνί',),
    'ἐνθάδε': ('ἐνθαδί',),
    'ἐνταῦθα': ('ἐνταυθί',),
    'δεῦρο': ('δευρί',),
    'ἐντεῦθεν': ('ἐντευθενί',),
}
HEADWORDS = {'οὗτος': 'οὑτοσί', 'ὅδε': 'ὁδί', 'ἐκεῖνος': 'ἐκεινοσί',
             'τοσοῦτος': 'τοσουτοσί', 'τοιοῦτος': 'τοιουτοσί', 'τηλικοῦτος': 'τηλικουτοσί',
             'οὕτως': 'οὑτωσί', 'ὧδε': 'ὡδί', 'νῦν': 'νυνί',
             'ἐνθάδε': 'ἐνθαδί', 'ἐνταῦθα': 'ἐνταυθί', 'δεῦρο': 'δευρί', 'ἐντεῦθεν': 'ἐντευθενί'}


def _key(value):
    return unicodedata.normalize('NFC', unicodedata.normalize('NFD', value.casefold()).replace('\u0300', '\u0301'))


_FORMS = {_key(form): base for base, forms in FAMILIES.items() for form in forms}


@dataclass(frozen=True)
class DeicticAssessment:
    rule_id: str
    rule_version: str
    status: str
    reason: str
    form: str
    candidate_lemma: str
    base_lemma: str | None
    headword: str | None
    suffix: str | None
    token_count: int = 1

    def as_dict(self):
        return asdict(self)


def assess_deictic(form, candidate_lemma):
    """Propose the separate suffixed headword, preserving original provider input.

    A supported surface and a selected lemma in its documented family are both
    required. An unrelated or missing lemma is retained for review, not repaired.
    Inflected deictic forms share one suffixed headword, not individual lemmata.
    Grave/acute, Unicode composition and initial case are comparison variants.
    No intercalated γε/δέ/μέν/γάρ, unaccented forms or guessed endings are handled.
    """
    if not isinstance(form, str) or not isinstance(candidate_lemma, str):
        raise TypeError('form and candidate_lemma must be strings')
    base = _FORMS.get(_key(form))
    if base is None:
        status, reason, suffix = 'not_applicable', 'unsupported_surface', None
    elif _key(candidate_lemma) not in {_key(base), *(_key(f) for f in FAMILIES[base])}:
        status, reason, suffix = 'abstained', 'selected_lemma_outside_family', '-ι'
    else:
        status, reason, suffix = 'applied', 'separate_deictic_headword', '-ι'
    return DeicticAssessment(RULE_ID, RULE_VERSION, status, reason, form,
                            candidate_lemma, base, HEADWORDS.get(base), suffix)
