"""Accent evidence for interrogative τίς versus enclitic indefinite τις.

Smyth §§154, 181–183, 334. An acute on a monosyllabic indefinite can
come from a following enclitic; it is therefore defeasible evidence.
Contracted τοῦ/τῷ are deliberately outside this spelling rule.
"""
import unicodedata
from . import diogenes

INTERROGATIVE = frozenset(('τίς', 'τί', 'τίνος', 'τίνι', 'τίνα', 'τίνες', 'τίνων', 'τίσι', 'τίσιν', 'τίνας', 'τίνε', 'τίνοιν'))
INDEFINITE = frozenset(('τις', 'τι', 'τινος', 'τινός', 'τινι', 'τινί', 'τινα', 'τινά', 'τινες', 'τινές',
                        'τινων', 'τινῶν', 'τισι', 'τισί', 'τισιν', 'τισίν', 'τινας', 'τινάς', 'τινε', 'τινοιν', 'τινοῖν'))
ENCLITIC_BASES = frozenset(('μου', 'μοι', 'με', 'σου', 'σοι', 'σε', 'οι', 'μιν', 'νιν', 'σφισι', 'σφισιν', 'σφας', 'σφε',
                           'τις', 'τι', 'τινος', 'τινι', 'τινα', 'τινες', 'τινων', 'τισι', 'τισιν', 'τινας',
                           'που', 'ποθι', 'πη', 'ποι', 'ποθεν', 'ποτε', 'πω', 'πως', 'γε', 'τε', 'τοι', 'περ',
                           'νυ', 'νυν', 'κε', 'κεν', 'ρα', 'ειμι', 'εστι', 'εστιν', 'εισι', 'εισιν',
                           'φημι', 'φησι', 'φησιν', 'φασι', 'φασιν'))


def pronoun_score(lemma, form, msd=None, following=None):
    if lemma not in ('τίς', 'τις'):
        return 0.0
    original = unicodedata.normalize('NFC', form.lower())
    key = diogenes.key(form)
    expected = None
    if original in ('τὶ', 'τὶς'):
        expected = 'τις'
    elif key in INTERROGATIVE:
        # Chained enclitics can put an acute on indefinite τίς/τί.
        if key in ('τίς', 'τί') and following and diogenes.bare(following) in ENCLITIC_BASES:
            return 0.0
        expected = 'τίς'
    elif key in INDEFINITE:
        expected = 'τις'
    return (2.0 if lemma == expected else -2.0) if expected else 0.0
