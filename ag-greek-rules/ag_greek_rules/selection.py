"""Owner-approved provider responsibilities, independent of model runtimes.

This is an engineering selection policy, not a claim about Greek grammar or
calibrated accuracy. Raw provider proposals are inputs and are never modified.

1.1.0 (owner decision 2026-11-19): when the local inventory offers several
lemmata, a contextual proposal must be one of them. Otherwise the model-owned
canonical lookup, then GreTa, may supply one of those candidates. Failing both,
the proposal is kept only if it is an attested headword of the local morphology
(`known_lemma`), since a form's own inventory can be incomplete; otherwise the
policy abstains. Unique local lemmata, unseen forms and unresolved context keep
their 1.0.0 behaviour.

1.2.0 (owner decision 2026-11-20): before falling back to context-free canonical
lookup or GreTa on an ambiguous form, inspect the contextual model's ranked
`top_k` edit-script candidates (if provided) and accept the highest-probability
candidate that belongs to the local inventory (`context_topk_inventory_disambiguation`).
Raw top-1 proposals and full `top_k` distributions remain untouched in evidence.

1.3.0 (owner decision 2026-11-20): on an ambiguous form whose top-1 contextual
proposal falls outside the local inventory, check the model-owned `canonical_lemma`
(UPOS-conditioned lookup) in the local inventory first; if it is absent or outside
the inventory, inspect ranked `top_k` edit-script candidates before falling back to
GreTa. When top-1 is already an attested headword (`known_lemma`), reject deep-tail
`top_k` overrides lagging top-1 by more than `TOPK_KNOWN_HEADWORD_MAX_GAP` nats so
attested headwords missing from a form's local table (e.g. `Φαλῆς`) are retained for
review rather than overwritten by low-probability spelling neighbours.
"""
import unicodedata

RULE_ID = 'grc.selection.contextual-primary'
RULE_VERSION = '1.3.0'
TOPK_KNOWN_HEADWORD_MAX_GAP = 2.5


def lemma_key(lemma):
    """Case and grave/acute are presentation; breathings, other accents and diaeresis are not."""
    decomposed = unicodedata.normalize('NFD', (lemma or '').lower()).replace('̀', '́')
    return unicodedata.normalize('NFC', decomposed)


def _in(lemma, lexical):
    return bool(lemma) and lemma_key(lemma) in {lemma_key(l) for l in lexical}


def _best_topk(context, lexical):
    """Return the highest-ranked contextual top-k entry matching the local inventory."""
    if not lexical:
        return None
    keys = {lemma_key(l) for l in lexical}
    for item in context.get('top_k') or ():
        cand = item.get('lemma') if isinstance(item, dict) else item
        if cand and lemma_key(cand) in keys:
            return item if isinstance(item, dict) else {'lemma': cand}
    return None


def _topk_allowed(context, topk_hit, proposal, known_lemma):
    """Do not let a deep-tail top-k script override a high-confidence attested headword."""
    if not (proposal and known_lemma is not None and known_lemma(proposal)):
        return True
    top1_lp = context.get('script_log_probability')
    cand_lp = topk_hit.get('log_probability')
    if isinstance(top1_lp, (int, float)) and isinstance(cand_lp, (int, float)):
        return (top1_lp - cand_lp) <= TOPK_KNOWN_HEADWORD_MAX_GAP
    return True


def select_lemma(context, lexical_lemmas=(), comparator=None, known_lemma=None):
    """Unique local lemma; context for ambiguity/unseen forms; GreTa as helper.

    Morpho deliberately supplies no selection argument: its role is additional
    inspectable evidence. Model-owned lookups must identify their own source in
    `context`, rather than being represented as a neural edit prediction.
    `known_lemma(lemma) -> bool` says whether a lemma is an attested headword of the
    local morphology as a whole; without it, the last fallback abstains.
    """
    lexical = list(dict.fromkeys(l for l in lexical_lemmas if l))
    proposal = context.get('lemma') if context.get('resolved') else None
    canonical = context.get('canonical_lemma')
    topk_hit = None
    if len(lexical) == 1:
        lemma, source, reason = lexical[0], 'diogenes', 'unique_local_lemma'
    elif proposal and (not lexical or _in(proposal, lexical)):
        lemma, source, reason = proposal, 'context', 'contextual_disambiguation'
    elif proposal and _in(canonical, lexical):
        lemma, source, reason = canonical, 'context_lookup', 'context_outside_inventory_lookup'
    elif (len(lexical) >= 2 and (candidate_topk := _best_topk(context, lexical)) is not None
          and _topk_allowed(context, candidate_topk, proposal, known_lemma)):
        topk_hit = candidate_topk
        lemma, source, reason = topk_hit['lemma'], 'context_topk', 'context_topk_inventory_disambiguation'
    elif proposal and _in(comparator, lexical):
        lemma, source, reason = comparator, 'greta', 'context_outside_inventory_comparator'
    elif proposal and known_lemma is not None and known_lemma(proposal):
        lemma, source, reason = proposal, 'context', 'context_outside_inventory_known_headword'
    elif proposal:
        lemma, source, reason = None, 'unresolved', 'context_outside_inventory_abstained'
    elif comparator:
        lemma, source, reason = comparator, 'greta', 'unresolved_context_helper'
    else:
        lemma, source, reason = None, 'unresolved', 'no_resolved_proposal'
    return {'rule_id': RULE_ID, 'rule_version': RULE_VERSION, 'category': 'engineering_selection_policy',
            'status': 'applied' if lemma else 'abstained', 'selected_lemma': lemma,
            'selected_by': source, 'reason': reason, 'context_source': context.get('lemma_source'),
            'context_lemma': proposal, 'canonical_lemma': canonical,
            'lexical_lemmas': lexical, 'comparator': comparator,
            **({'context_topk_candidate': dict(topk_hit)} if topk_hit is not None else {})}
