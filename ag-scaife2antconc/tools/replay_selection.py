"""Replay the current select_lemma on saved analysis evidence (no models), and compare with a reference.

Usage: python tools/replay_selection.py <analysis-review.jsonl> [reference.json]

Only the lemma selection is replayed, from each component's saved context prediction, lexical
inventory and GreTa proposal. Restorations, grammar and the later headword/deictic policies are
those of the saved run; a changed component takes the matching local candidate's spelling.
This is a development diagnostic, not an accuracy estimate.
"""
import json
import sys
from collections import Counter

from ag_greek_rules import select_lemma
from ag_greek_rules.selection import lemma_key
from ag_scaife2antconc import review


def known_headwords():
    """The installed Diogenes headwords, as the analysis run passes them to select_lemma."""
    from ag_diogenes_spacy_txt2xml import analyses, diogenes
    store = analyses.load(diogenes.find())
    keys = {lemma_key(h) for h in store.headwords()}
    store.close()
    return lambda lemma: lemma_key(lemma) in keys


KNOWN = None


def replay(record):
    changes = []
    reading = (record.get('readings') or [{}])[0]
    lemmas = []
    for component in reading.get('components') or []:
        old = component.get('selection') or {}
        if 'lexical_lemmas' not in old:
            return record['lemma'], []
        new = select_lemma(component.get('context') or {}, old['lexical_lemmas'], component.get('greta'), KNOWN)
        if new['reason'] == old.get('reason') and lemma_key(new['selected_lemma']) == lemma_key(old.get('selected_lemma')):
            lemmas.append(None)
            continue
        chosen = new['selected_lemma'] or ''
        spelled = [l for l in old['lexical_lemmas'] if lemma_key(l) == lemma_key(chosen)]
        lemmas.append(next((l for l in spelled if l == chosen), spelled[0] if spelled else chosen))
        changes.append((component.get('form'), old.get('selected_lemma'), lemmas[-1], new['reason']))
    if not changes:
        return record['lemma'], []
    saved = (record['lemma'] or '').split('+')
    if len(saved) != len(lemmas):
        return record['lemma'], changes
    return '+'.join(n if n is not None else s for n, s in zip(lemmas, saved)), changes


def main(evidence, reference=None):
    global KNOWN
    KNOWN = known_headwords()
    rows = [json.loads(line) for line in open(evidence, encoding='utf-8') if line.strip()]
    after, reasons, examples = {}, Counter(), Counter()
    for r in rows:
        lemma, changes = replay(r)
        after[r['work'], r['citation'], r['token']] = (r, lemma)
        for form, old, new, reason in changes:
            reasons[reason] += 1
            examples[form, old, new or '—'] += 1
    print(f'{len(rows):,} words; changed components: {sum(reasons.values())}')
    for reason, n in reasons.most_common():
        print(f'  {n:5d}  {reason}')
    for (form, old, new), n in examples.most_common(40):
        print(f'  {n:3d}× {form}: {old} → {new}')
    if reference:
        gold = [g for g in json.load(open(reference, encoding='utf-8')) if not g.get('disputed')]
        before = now = 0
        for g in gold:
            r, lemma = after[g['work'], g['citation'], g['token']]
            ok_before = review.key(g['lemma']) == review.key(r['lemma'])
            ok_now = review.key(g['lemma']) == review.key(lemma)
            before += ok_before
            now += ok_now
            if ok_before != ok_now:
                print(f"  {'GAINED' if ok_now else 'LOST  '} {g['citation']} {g['form']}: {r['lemma']} → {lemma} (reference {g['lemma']})")
        print(f'Reference matches: {before}/{len(gold)} → {now}/{len(gold)}')


if __name__ == '__main__':
    main(*sys.argv[1:])
