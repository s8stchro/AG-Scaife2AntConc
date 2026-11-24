"""Parity and timing: GreBERTa one sentence per call vs. batched pipe(), and the persistent cache.

Usage: python tools/parity_greberta.py <input.xml> <empty scratch folder>
Compares every Prediction field for every context group of the input. Writes only to the scratch folder.
"""
import sys
import time

from ag_scaife2antconc.pipeline import Options, tokens_for
from ag_scaife2antconc.reader import read
from ag_diogenes_spacy_txt2xml.greberta import Joint
from ag_diogenes_spacy_txt2xml.hybrid import sentence_groups
from ag_diogenes_spacy_txt2xml.tokenize import Word

source, scratch = sys.argv[1], sys.argv[2]
tokens, _ = tokens_for(read(source), Options())
sentences = [[w.form for w in g if isinstance(w, Word) and w.lang == 'grc'] for g in sentence_groups(tokens)]
sentences = [s for s in sentences if s]
print(f'{len(sentences)} context groups, {sum(map(len, sentences)):,} words', flush=True)

single = Joint(cache_dir=False)
t = time.perf_counter()
reference = [single._predict(list(s)) for s in sentences]
t_single = time.perf_counter() - t
print(f'one sentence per call: {t_single:7.1f} s', flush=True)

batched = Joint(cache_dir=False)
t = time.perf_counter()
got = list(batched.pipe(sentences))
t_batch = time.perf_counter() - t
print(f'batched pipe():        {t_batch:7.1f} s  (x{t_single / t_batch:.2f})', flush=True)

fields = [f for f in reference[0][0].as_dict() if f != 'script_log_probability']
different, worst, words = 0, 0.0, 0
for ref, new in zip(reference, got):
    assert len(ref) == len(new)
    for a, b in zip(ref, new):
        words += 1
        da, db = a.as_dict(), b.as_dict()
        if any(da[f] != db[f] for f in fields):
            different += 1
            print('  DIFF', {f: (da[f], db[f]) for f in fields if da[f] != db[f]})
        if da['script_log_probability'] is not None and db['script_log_probability'] is not None:
            worst = max(worst, abs(da['script_log_probability'] - db['script_log_probability']))
print(f'words with any differing field: {different}/{words}; max |Δ log p| = {worst:.2e}', flush=True)

cold = Joint(cache_dir=scratch)
t = time.perf_counter(); first = list(cold.pipe(sentences)); t_cold = time.perf_counter() - t
warm = Joint(cache_dir=scratch)   # new process-like instance: memory cache empty, disk cache full
t = time.perf_counter(); second = list(warm.pipe(sentences)); t_warm = time.perf_counter() - t
same = all([p.as_dict() for p in a] == [p.as_dict() for p in b] for a, b in zip(first, second))
print(f'cache: cold {t_cold:.1f} s, warm {t_warm:.2f} s, hits {warm.disk.hits}, identical {same}', flush=True)
