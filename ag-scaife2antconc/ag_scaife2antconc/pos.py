"""Keep @pos consistent with the lemma for indeclinable words.

OdyCy chooses the part of speech on its own, so it can contradict the lemma that Diogenes
gave (μὲν: lemma μέν, pos AUX). Morpheus marks indeclinable words with their class:
`indeclform (particle)`, `(conj)`, `(prep)`, `(adverb)`, `(exclam)`. For a lemma that is
only ever indeclinable, a POS outside its class is replaced by the class, and the
(meaningless) morphological features are dropped.
"""
import os
import pickle
import re
from collections import Counter, defaultdict

from ag_diogenes_spacy_txt2xml.diogenes import compound, greek

CACHE_VERSION = 3
ANALYSIS = re.compile(r'\{\d+ \d+ ([^\t]*)\t[^\t]*\t([^}]*)\}')
CLASSES = {'particle': 'PART', 'conj': 'CCONJ', 'prep': 'ADP', 'adverb': 'ADV', 'exclam': 'INTJ'}
# UD tags that can stand for each class (OdyCy, like the UD Perseus/PROIEL treebanks,
# tags many particles ADV or CCONJ, and prepositions in adverbial use ADV)
COMPATIBLE = {'PART': {'PART', 'ADV', 'CCONJ', 'SCONJ'}, 'CCONJ': {'CCONJ', 'SCONJ', 'ADV', 'PART'},
              'ADP': {'ADP', 'ADV'}, 'ADV': {'ADV', 'PART', 'SCONJ', 'CCONJ'}, 'INTJ': {'INTJ'}}
COMPATIBLE['SCONJ'] = COMPATIBLE['CCONJ']
# Morpheus has one class "conj"; UD separates subordinators, and tags the negation μή as ADV
OVERRIDE = {'μή': 'ADV', 'εἰ': 'SCONJ', 'ἐάν': 'SCONJ', 'ἤν': 'SCONJ', 'ὅτι': 'SCONJ', 'ἐπεί': 'SCONJ',
            'ἐπειδή': 'SCONJ', 'ἵνα': 'SCONJ', 'ὅταν': 'SCONJ', 'ὅτε': 'SCONJ', 'ὥστε': 'SCONJ',
            'ὅπως': 'SCONJ', 'ἐπειδάν': 'SCONJ', 'ὁπότε': 'SCONJ'}


def parse(path):
    """{lemma: UPOS} for lemmas whose every analysis is indeclinable."""
    classes, other = defaultdict(Counter), set()
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            for lemma, morph in ANALYSIS.findall(line):
                lemma = compound(greek(re.sub(r'\d+$', '', lemma.split(',')[-1])))
                m = re.search(r'indeclform\s*\(?(\w+)', morph)    # also 'epic (proclitic indeclform prep)'
                if m and m.group(1) in CLASSES:
                    classes[lemma][CLASSES[m.group(1)]] += 1
                else:
                    other.add(lemma)
    table = {lemma: c.most_common(1)[0][0] for lemma, c in classes.items() if lemma not in other}
    table.update((k, v) for k, v in OVERRIDE.items() if k in table)
    return table


def load(path, cache_dir=None):
    st = os.stat(path)
    cache_dir = cache_dir or os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/.cache'),
                                          'AG-Diogenes-SpaCy-txt2xml')
    cache = os.path.join(cache_dir, f'greek-indeclinables-v{CACHE_VERSION}-{st.st_size}-{int(st.st_mtime)}.pickle')
    if os.path.exists(cache):
        with open(cache, 'rb') as fh:
            return pickle.load(fh)
    table = parse(path)
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache + '.tmp', 'wb') as fh:
        pickle.dump(table, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(cache + '.tmp', cache)
    return table


def harmonize(words, indeclinables):
    """Correct w.pos (and crasis parts) where it contradicts an indeclinable lemma; return the count."""
    fixed = 0
    for w in words:
        if w.crasis:
            for i, (surface, lemma, pos, resp) in enumerate(w.crasis):
                upos = indeclinables.get(lemma)
                if upos and pos not in COMPATIBLE[upos]:
                    w.crasis[i] = (surface, lemma, upos, resp)
                    fixed += 1
            continue
        upos = indeclinables.get(w.lemma)
        if upos and w.pos not in COMPATIBLE[upos]:
            w.pos, w.msd = upos, None
            fixed += 1
    return fixed
