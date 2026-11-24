"""Benchmark GreBERTa joint: one sentence per call (current) vs. length-bucketed batches, and thread counts.
Checks that batched argmax decisions equal the single-sentence ones. Reads only the test fixture."""
import sys, time, os
import numpy as np
from ag_scaife2antconc.pipeline import Options, tokens_for
from ag_scaife2antconc.reader import read
from ag_diogenes_spacy_txt2xml.hybrid import sentence_groups
from ag_diogenes_spacy_txt2xml.tokenize import Word
from ag_diogenes_spacy_txt2xml.greberta import Joint

src = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 120
work = read(src)
tokens, _ = tokens_for(work, Options())
sentences = [[w.form for w in g if isinstance(w, Word)] for g in sentence_groups(tokens)]
sentences = [s for s in sentences if s][:N]
print('sentences', len(sentences), 'words', sum(map(len, sentences)))

HEADS = ['upos', *('x' + str(i) for i in range(9)), 'lemma']


def single(joint):
    out = []
    for words in sentences:
        words = joint.prep.normalize_tokens(words)
        a = joint.prep.align_pretokenized(joint.tokenizer, words, joint.manifest.max_subwords)
        feed = {'input_ids': np.asarray([a.input_ids], dtype=np.int64),
                'attention_mask': np.asarray([a.attention_mask], dtype=np.int64),
                'word_pos': np.asarray([a.first_subword_positions], dtype=np.int64)}
        r = dict(zip(HEADS, joint.session.run(HEADS, feed)))
        out.append((r['upos'][0, a.first_subword_positions].argmax(-1).tolist(), r['lemma'][0, :len(words)].argmax(-1).tolist()))
    return out


def batched(joint, size):
    aligned = []
    for words in sentences:
        words = joint.prep.normalize_tokens(words)
        aligned.append((words, joint.prep.align_pretokenized(joint.tokenizer, words, joint.manifest.max_subwords)))
    order = sorted(range(len(aligned)), key=lambda i: len(aligned[i][1].input_ids))
    out = [None] * len(aligned)
    for start in range(0, len(order), size):
        idx = order[start:start + size]
        S = max(len(aligned[i][1].input_ids) for i in idx)
        W = max(len(aligned[i][1].first_subword_positions) for i in idx)
        ids = np.zeros((len(idx), S), np.int64)   # pad id 1 for RoBERTa; masked anyway
        ids[:] = 1
        mask = np.zeros((len(idx), S), np.int64)
        wp = np.zeros((len(idx), W), np.int64)
        for row, i in enumerate(idx):
            a = aligned[i][1]
            ids[row, :len(a.input_ids)] = a.input_ids
            mask[row, :len(a.attention_mask)] = a.attention_mask
            wp[row, :len(a.first_subword_positions)] = a.first_subword_positions
        r = dict(zip(HEADS, joint.session.run(HEADS, {'input_ids': ids, 'attention_mask': mask, 'word_pos': wp})))
        for row, i in enumerate(idx):
            a, n = aligned[i][1], len(aligned[i][0])
            out[i] = (r['upos'][row, a.first_subword_positions].argmax(-1).tolist(), r['lemma'][row, :n].argmax(-1).tolist())
    return out


for threads in (4, 8, 12):
    joint = Joint(threads=threads)
    single(joint)  # warm-up on a few? run full once for warm caches
    t = time.perf_counter(); ref = single(joint); ts = time.perf_counter() - t
    print(f'threads={threads:2d} single: {ts:6.2f}s', flush=True)
    for size in (4, 8, 16):
        t = time.perf_counter(); got = batched(joint, size); tb = time.perf_counter() - t
        same = sum(g == r for g, r in zip(got, ref))
        words_diff = sum(sum(x != y for x, y in zip(g[1], r[1])) + sum(x != y for x, y in zip(g[0], r[0])) for g, r in zip(got, ref))
        print(f'threads={threads:2d} batch={size:2d}: {tb:6.2f}s  x{ts / tb:4.2f}  identical sentences {same}/{len(ref)}  differing labels {words_diff}', flush=True)
