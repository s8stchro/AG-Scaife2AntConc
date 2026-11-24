"""Corroborate a part of speech from a form and its selected dictionary headword."""
from dataclasses import asdict, dataclass

RULE_ID = 'grc.grammar.corroborated-pos'
RULE_VERSION = '1.0.0'
UPOS = {'ADJ', 'ADP', 'ADV', 'AUX', 'CCONJ', 'DET', 'INTJ', 'NOUN', 'NUM',
        'PART', 'PRON', 'PROPN', 'PUNCT', 'SCONJ', 'SYM', 'VERB', 'X'}

# Preserve the application's existing compatible labels for uninflected words.
COMPATIBLE = {
    'VERB': {'VERB', 'AUX'}, 'AUX': {'VERB', 'AUX'},
    'PART': {'PART', 'ADV', 'CCONJ', 'SCONJ'},
    'CCONJ': {'CCONJ', 'SCONJ', 'ADV', 'PART'},
    'SCONJ': {'CCONJ', 'SCONJ', 'ADV', 'PART'},
    'ADP': {'ADP', 'ADV'}, 'ADV': {'ADV', 'PART', 'SCONJ', 'CCONJ'},
}


@dataclass(frozen=True)
class PosAssessment:
    rule_id: str
    rule_version: str
    status: str
    reason: str
    form: str
    candidate_lemma: str
    original_pos: str | None
    form_classes: list
    lemma_classes: list
    proposed_pos: str | None = None
    clear_msd: bool = False

    def as_dict(self):
        return asdict(self)


def assess_pos(form, candidate_lemma, *, original_pos, form_analyses, lemma_analyses):
    """Assess caller-supplied morphological evidence without selecting a lemma.

    Each analysis supplies `pos` and optional UD `msd`. Unknown classes count as
    uncertainty. Nonfinite and unspecified verbal forms abstain because their
    contextual functions can differ. Dictionary coverage is not completeness.
    """
    if not isinstance(form, str) or not isinstance(candidate_lemma, str):
        raise TypeError('form and candidate_lemma must be strings')
    if original_pos is not None and not isinstance(original_pos, str):
        raise TypeError('original_pos must be a string or None')
    if original_pos and original_pos not in UPOS:
        raise ValueError('original_pos must be a UD part of speech')
    for analyses in (form_analyses, lemma_analyses):
        if not isinstance(analyses, (list, tuple)):
            raise TypeError('analyses must be lists or tuples')
        if any(not isinstance(a, dict) or (a.get('pos') is not None and not isinstance(a['pos'], str))
               or (a.get('msd') is not None and not isinstance(a['msd'], str)) for a in analyses):
            raise TypeError('each analysis needs a string or null pos and msd')
        if any(a.get('pos') and a['pos'] not in UPOS for a in analyses):
            raise ValueError('analysis pos must be a UD part of speech')
    surface = sorted({a.get('pos') or None for a in form_analyses}, key=lambda value: value or '')
    headword = sorted({a.get('pos') or None for a in lemma_analyses}, key=lambda value: value or '')

    def result(status, reason, proposed=None):
        return PosAssessment(RULE_ID, RULE_VERSION, status, reason, form, candidate_lemma,
                             original_pos, surface, headword, proposed, status == 'applied')

    if not surface or not headword:
        return result('abstained', 'missing_form_or_headword_evidence')
    if len(surface) != 1 or len(headword) != 1 or any(value in (None, 'X') for value in surface + headword):
        return result('abstained', 'ambiguous_or_unknown_dictionary_class')
    if surface != headword:
        return result('abstained', 'form_and_headword_classes_disagree')
    expected = surface[0]
    if original_pos in COMPATIBLE.get(expected, {expected}):
        return result('not_applicable', 'compatible_selected_class')
    if expected in ('VERB', 'AUX'):
        if any('VerbForm=Fin' not in (a.get('msd') or '').split('|')
               for a in form_analyses):
            return result('abstained', 'nonfinite_or_unspecified_verbal_form')
    return result('applied', 'form_and_headword_pos_corroborated', expected)
