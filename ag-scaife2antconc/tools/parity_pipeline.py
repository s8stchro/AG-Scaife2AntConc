"""End-to-end parity: the full analysis with GreBERTa batch size 1 (the pre-0.7.2 arithmetic) vs. the default.

Usage: python tools/parity_pipeline.py <input.xml> <empty scratch folder>
Runs with the inference cache off, then compares lemma, POS, MSD and restoration of every word.
"""
import json
import os
import sys
import time

os.environ['AG_INFERENCE_CACHE'] = 'off'
from ag_diogenes_spacy_txt2xml import greberta
from ag_scaife2antconc.pipeline import Options, run

source, scratch = sys.argv[1], sys.argv[2]
results = {}
for label, batch in (('single', 1), ('batched', greberta.BATCH)):
    greberta.BATCH = batch
    folder = os.path.join(scratch, label)
    started = time.perf_counter()
    run([source], folder, Options(), log=lambda m: None)
    print(f'{label}: {time.perf_counter() - started:.1f} s', flush=True)
    rows = [json.loads(line) for line in open(os.path.join(folder, 'analysis-review.jsonl'), encoding='utf-8')]
    results[label] = {(r['citation'], r['token']): (r['lemma'], r['pos'], r['msd'], r['expansion']) for r in rows}
a, b = results['single'], results['batched']
diff = [(k, a[k], b[k]) for k in a if a[k] != b[k]]
print(f'words: {len(a):,}; differing lemma/POS/MSD/restoration: {len(diff)}')
for k, x, y in diff[:20]:
    print('  ', k, x, '→', y)
