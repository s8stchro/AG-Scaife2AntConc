"""Apply grc.grammar.proper-noun to machine records after headword standardization.

Runs after naming, because the headword standard can change a lemma's spelling and the rule
reads its capital. Manual and reviewed choices are never changed. Idempotent: a record that
already carries this rule version for a component is not assessed again.
"""
from collections import Counter

from ag_greek_rules.proper_noun import RULE_ID, RULE_VERSION, assess_proper_noun

LOWER_CASE_FORM = 'capitalised lemma for a lower-case form; check the lemma or a personification'


def apply_proper_noun_record(record):
    """Change NOUN to PROPN in one record (form, expansion, lemma and pos joined by '+')."""
    stats = Counter()
    if record.get('reviewed') or record.get('manual') or 'corrector' in (record.get('resp') or '').split():
        return stats
    lemmas, tags = (record.get('lemma') or '').split('+'), (record.get('pos') or 'X').split('+')
    forms = record.get('expansion') or [record['form']]
    if not (len(forms) == len(lemmas) == len(tags)):
        return stats
    readings = record.setdefault('readings', [])
    if not readings:
        readings.append({'kind': 'retained analysis', 'expansion': forms, 'components': []})
    traces = readings[0].setdefault('rule_contributions', [])
    done = {t.get('component') for t in traces if t.get('rule_id') == RULE_ID and t.get('rule_version') == RULE_VERSION}
    for index, (lemma, pos) in enumerate(zip(lemmas, tags)):
        if index in done:
            continue
        # The written word decides the capital; a crasis component has only its restored form.
        written = record['form'] if len(forms) == 1 else forms[index]
        assessment = assess_proper_noun(written, lemma, original_pos=pos)
        if assessment.status == 'not_applicable':
            continue
        traces.append(dict(assessment.as_dict(), component=index))
        if assessment.status == 'applied':
            tags[index] = assessment.proposed_pos
            stats['proper nouns'] += 1
        else:
            record.setdefault('review_reasons', []).append(LOWER_CASE_FORM)
            stats['capitalised lemma, lower-case form'] += 1
    record['pos'] = '+'.join(tags)
    record['rule_contributions'] = traces
    record['review_reasons'] = list(dict.fromkeys(record.get('review_reasons') or []))
    return stats
