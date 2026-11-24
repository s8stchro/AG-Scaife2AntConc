"""Apply grc.selection.generated-lemma-check to one analysed text, after selection.

Only single-word forms whose local inventory is empty and whose lemma the contextual model
generated are assessed; crasis restorations, manual and reviewed choices are left alone. The
reliable lemmata of the same text (local analyses, manual choices, confident generated lemmata)
are collected first, so the check needs the whole text.
"""
from collections import Counter

from ag_greek_rules.generated import MIN_STEM, RELIABLE, RULE_ID, assess_generated_lemma, bare, stem

REPLACED = 'generated lemma replaced by the lemma of another form of the same word in this text'
REMOVED = 'generated lemma has an impossible spelling; no lemma assigned'


def _component(word):
    if word.crasis or not word.evidence or '…' in word.form:
        return None
    components = word.evidence[0].get('components') or []
    return components[0] if len(components) == 1 else None


def _manual(word):
    return bool(getattr(word, 'reviewed', False)) or 'corrector' in (word.resp or '').split()


def _generated(component):
    selection = component.get('selection') or {}
    return selection.get('lexical_lemmas') == [] and selection.get('selected_by') == 'context'


def check_generated_lemmata(words):
    stats = Counter()
    reliable, candidates = {}, []
    for word in words:
        component = _component(word)
        if component is None or not word.lemma:
            continue
        log_p = (component.get('context') or {}).get('script_log_probability')
        if _manual(word) or not _generated(component):
            reliable.setdefault(word.lemma, word.lemma_id)
        elif log_p is not None and log_p > RELIABLE:
            reliable.setdefault(word.lemma, word.lemma_id)
            candidates.append((word, component, log_p))
        else:
            candidates.append((word, component, log_p))
    # A matching stem has at least MIN_STEM letters and begins the form, so only lemmata whose stem
    # shares the form's first four letters can match: the same result, without comparing every lemma.
    by_prefix = {}
    for lemma in reliable:
        lemma_stem = stem(lemma)
        if lemma_stem and len(lemma_stem) >= MIN_STEM:
            by_prefix.setdefault(lemma_stem[:4], []).append(lemma)
    for word, component, log_p in candidates:
        traces = word.evidence[0].setdefault('rule_contributions', [])
        if any(t.get('rule_id') == RULE_ID for t in traces):
            continue
        assessment = assess_generated_lemma(word.form, word.lemma, log_probability=log_p,
                                            reliable_lemmata=by_prefix.get(bare(word.form)[:4], ()))
        if assessment.status != 'applied':
            continue
        traces.append(dict(assessment.as_dict(), component=0))
        component['generated_lemma_check'] = assessment.as_dict()
        word.resp = ' '.join(filter(None, (word.resp, 'lemma_policy')))
        if assessment.proposed_lemma:
            word.lemma, word.lemma_id = assessment.proposed_lemma, reliable.get(assessment.proposed_lemma)
            word.review_reasons = list(dict.fromkeys([*word.review_reasons, REPLACED]))
            stats['generated lemmata replaced'] += 1
        else:
            word.lemma, word.lemma_id = None, None
            word.review_reasons = list(dict.fromkeys([*word.review_reasons, REMOVED]))
            stats['generated lemmata removed'] += 1
    return stats
