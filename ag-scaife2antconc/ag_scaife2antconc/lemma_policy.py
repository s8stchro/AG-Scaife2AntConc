"""Apply the agreed export convention separately from raw provider proposals."""
from ag_greek_rules import assess_deictic


def contributions(word):
    return word.evidence[0].get('rule_contributions', []) if word.evidence else []


def apply_deictic_policy(words):
    count = 0
    for word in words:
        if not word.evidence or any(trace.get('rule_id') == 'grc.lemma.deictic-iota-headword' for trace in contributions(word)):
            continue
        reading = word.evidence[0]
        forms = word.expansion or [word.form]
        parts = word.crasis or [(word.form, word.lemma, word.pos, word.resp)]
        if len(forms) != len(parts):
            continue
        traces, updated = [], []
        for index, (form, part) in enumerate(zip(forms, parts)):
            surface, lemma, pos, resp = part
            result = assess_deictic(form, lemma or '')
            if result.status != 'not_applicable':
                traces.append(dict(result.as_dict(), component=index))
            if result.status == 'applied':
                count += 1
                if lemma != result.headword:
                    resp = ' '.join(filter(None, (resp, 'lemma_policy')))
                lemma = result.headword
            updated.append((surface, lemma, pos, resp))
        if traces:
            reading.setdefault('rule_contributions', []).extend(traces)
        if word.crasis:
            word.crasis = updated
        else:
            old = word.lemma
            _, word.lemma, word.pos, word.resp = updated[0]
            # A provider's base-headword ID must not masquerade as the new ID.
            if old != word.lemma:
                word.lemma_id = None
    return count
