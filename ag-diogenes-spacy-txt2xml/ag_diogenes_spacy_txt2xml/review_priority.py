"""Prioritise doubts that can change an exported lemma or its word segmentation.

All grammatical observations stay in the evidence. A model disagreement alone
does not make a locally supported lemma doubtful. Margins are heuristics, never
probabilities or a measured error rate.
"""
from .crasis import with_accents

MARGIN = 0.75
REVIEW_RULE_ID = 'grc.review.lemma-priority'
REVIEW_RULE_VERSION = '1.5.0'
LEXICAL_AMBIGUITY = 'competing lemmas; ranking margin below 0.75 (not a probability)'
EXPANSION_AMBIGUITY = 'competing lexical expansions; ranking margin below 0.75 (not a probability)'
URGENT = ('crasis detected but no supported inflected expansion',
          'internal coronis detected; no supported independent-word expansion',
          'expansion candidate limit reached (8)', 'unexpanded elision; restoration uncertain',
          'unresolved initial elision', 'lemma lacks a full local morphological analysis',
          'GreTa length limit or incomplete prediction',
          'contextual model unresolved; fallback needs review',
          'contextual model used a shortened context',
          'contextual model used a lookup fallback for an ambiguous form',
          'contextual top-1 outside local candidates; top-k contextual candidate used',
          'interrogative/indefinite accent conflicts with selected lemma',
          'headword standard unresolved; proposal retained for review',
          'capitalised lemma for a lower-case form; check the lemma or a personification',
          'generated lemma replaced by the lemma of another form of the same word in this text',
          'generated lemma has an impossible spelling; no lemma assigned',
          'selected Diogenes dictionary association is provisional')


def lemma_key(lemma):
    return with_accents(lemma or '')


def selected(component):
    candidates = component.get('candidates') or []
    return next((c for c in candidates if c.get('lemma_id') == component.get('selected_id')),
                candidates[0] if candidates else None)


def lexical_signature(reading):
    components = reading.get('components') or []
    values = [selected(c) for c in components]
    if not values or any(not v or not v.get('lemma') for v in values):
        return None
    return tuple(lemma_key(v['lemma']) for v in values)


def _contextual_topk_close(component):
    """True when GreBERTa's top-2 in-inventory edit-script lemmas are within MARGIN."""
    context = component.get('context') or {}
    top_k = context.get('top_k') or ()
    if not top_k:
        return False
    local = {lemma_key(c.get('lemma')) for c in (component.get('candidates') or ())
             if c.get('lemma') and c.get('source') in ('diogenes', 'lexicon')}
    if len(local) < 2:
        return False
    in_local = []
    seen = set()
    for item in top_k:
        lem = item.get('lemma') if isinstance(item, dict) else None
        lp = item.get('log_probability') if isinstance(item, dict) else None
        k = lemma_key(lem)
        if lem and k in local and k not in seen and isinstance(lp, (int, float)):
            seen.add(k)
            in_local.append(lp)
            if len(in_local) == 2:
                break
    return len(in_local) == 2 and (in_local[0] - in_local[1]) < MARGIN


def priority_reasons(record):
    observations = record.get('review_reasons') or []
    reasons = [r for r in observations if r in URGENT or r.startswith('contextual lemma outside the local candidates;')]
    if not record.get('lemma') or any(not part for part in record['lemma'].split('+')):
        reasons.append('unresolved lemma')
    readings = record.get('readings') or []
    if not readings:
        # Older/incomplete evidence cannot justify quietly hiding an existing warning.
        return list(dict.fromkeys(reasons + observations))
    best = readings[0]
    for component in best.get('components') or []:
        top = selected(component)
        if not top:
            reasons.extend(observations)
            continue
        if top.get('source') not in ('diogenes', 'lexicon'):
            reasons.append('lemma lacks a full local morphological analysis')
        ambiguous = False
        for other in component.get('candidates') or []:
            if (lemma_key(other.get('lemma')) != lemma_key(top.get('lemma')) and
                    other.get('lemma') and top.get('score') is not None and other.get('score') is not None and
                    top['score'] - other['score'] < MARGIN):
                reasons.append(LEXICAL_AMBIGUITY)
                ambiguous = True
                break
        if not ambiguous and _contextual_topk_close(component):
            reasons.append(LEXICAL_AMBIGUITY)
        # Keep legacy OdyCy interrogative grammar check only for reopened pre-GreBERTa snapshots.
        if (not component.get('context') and top.get('lemma') == 'τις' and
                'PronType=Int' in (component.get('tagger', {}).get('msd') or '')):
            reasons.append('interrogative grammar conflicts with indefinite lemma')
    signature = lexical_signature(best)
    if signature is not None:
        for other in readings[1:]:
            alternative = lexical_signature(other)
            if (alternative is not None and alternative != signature and
                    best.get('score') is not None and other.get('score') is not None and
                    best['score'] - other['score'] < MARGIN):
                reasons.append(EXPANSION_AMBIGUITY)
                break
    return list(dict.fromkeys(reasons))


def word_priority(word):
    lemma = '+'.join(p[1] or '' for p in word.crasis) if word.crasis else word.lemma
    return priority_reasons({'lemma': lemma, 'readings': word.evidence, 'review_reasons': word.review_reasons})
