"""Full Morpheus analyses, indexed on disk; lemma identities and grammatical evidence stay intact."""
from __future__ import annotations

import os
import hashlib
import json
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from functools import lru_cache

from . import diogenes

VERSION = 2  # preserve dictionary associations separately from morphological evidence
ENTRY = re.compile(r'\{(\d+) (\d+) ([^\t]*)\t([^\t]*)\t([^}]*)\}')
CLASSES = {'particle': 'PART', 'conj': 'CCONJ', 'prep': 'ADP', 'adverb': 'ADV', 'exclam': 'INTJ'}


@dataclass(frozen=True)
class Analysis:
    surface: str
    lemma: str
    lemma_id: str
    pos: str | None = None
    msd: str | None = None
    raw: str = ''
    source: str = 'diogenes'
    dictionary_link: dict | None = None


def features(msd):
    return {k: set(v.split(',')) for field in (msd or '').split('|') if '=' in field
            for k, v in [field.split('=', 1)]}


@lru_cache(maxsize=16384)
def morphology(raw):
    """Map only explicit Morpheus features. A case ending alone does not distinguish noun/adjective."""
    words = set(re.findall(r'[a-z]+|[123](?:st|nd|rd)', raw.lower()))
    f = {}
    groups = {
        'Case': {'nom': 'Nom', 'acc': 'Acc', 'gen': 'Gen', 'dat': 'Dat', 'voc': 'Voc'},
        'Gender': {'masc': 'Masc', 'fem': 'Fem', 'neut': 'Neut'},
        'Number': {'sg': 'Sing', 'pl': 'Plur', 'dual': 'Dual'},
        'Person': {'1st': '1', '2nd': '2', '3rd': '3'},
        'Mood': {'ind': 'Ind', 'subj': 'Sub', 'opt': 'Opt', 'imperat': 'Imp'},
        'Degree': {'comp': 'Cmp', 'superl': 'Sup'},
    }
    for feature, mapping in groups.items():
        values = sorted({value for key, value in mapping.items() if key in words})
        if values:
            f[feature] = ','.join(values)
    if 'pres' in words:
        f['Tense'] = 'Pres'
    elif 'fut' in words:
        f['Tense'] = 'Fut'
    elif words & {'imperf', 'aor', 'perf', 'plup', 'pluperf'}:
        f['Tense'] = 'Past'
    if words & {'aor', 'perf', 'plup', 'pluperf'}:
        f['Aspect'] = 'Perf'
    elif 'imperf' in words:
        f['Aspect'] = 'Imp'
    voices = {'act': 'Act', 'mid': 'Mid', 'pass': 'Pass'}
    if 'mp' in words:
        f['Voice'] = 'Mid,Pass'
    elif found := sorted(v for k, v in voices.items() if k in words):
        f['Voice'] = ','.join(found)
    pos = None
    if 'part' in words:
        pos, f['VerbForm'] = 'VERB', 'Part'
    elif 'inf' in words:
        pos, f['VerbForm'] = 'VERB', 'Inf'
    elif 'Mood' in f:
        pos, f['VerbForm'] = 'VERB', 'Fin'
    else:
        match = re.search(r'indeclform\s*\(?(\w+)', raw)
        if match:
            pos = CLASSES.get(match.group(1))
        if pos is None:
            pos = next((value for key, value in CLASSES.items() if key in words), None)
    return pos, '|'.join(f'{k}={v}' for k, v in sorted(f.items())) or None


@lru_cache(maxsize=200000)
def headword(beta):
    return diogenes.compound(diogenes.greek(re.sub(r'\d+$', '', beta.split(',')[-1])))


def parse_line(line):
    beta, _, data = line.rstrip('\n').partition('\t')
    if not beta or beta.startswith(('!', '*!')):
        return []
    surface = diogenes.greek(beta)
    out = []
    for ident, marker, lemma, gloss, raw in ENTRY.findall(data):
        pos, msd = morphology(raw)
        confidence = int(marker)
        link = {'offset': int(ident), 'marker': confidence, 'provider_headword': lemma, 'gloss': gloss,
                'provider_status': 'missing' if confidence == 0 else 'guessed' if confidence <= 2 else 'linked',
                'headword_match': 'not_checked'}
        out.append(Analysis(surface, headword(lemma), f'morpheus:{ident}:{lemma}', pos, msd, raw,
                            dictionary_link=link))
    return out


class Store:
    """Read-only SQLite index. Each worker owns its connection and a bounded query cache."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute('PRAGMA query_only = ON')
        self.metadata = dict(self.connection.execute('SELECT key,value FROM metadata'))
        self._dict_stream = None

    def _dictionary_stream(self):
        if self._dict_stream is None:
            path = self.metadata.get('dictionary_file')
            if path and os.path.isfile(path):
                self._dict_stream = open(path, 'rb')
        return self._dict_stream

    @lru_cache(maxsize=32768)
    def dictionary_entry(self, offset):
        stream = self._dictionary_stream()
        if stream is None:
            return None
        stream.seek(offset)
        header = stream.read(1024).decode('utf-8', errors='replace')
        match = re.match(r'<div2\b[^>]*\bkey="([^"]+)"', header)
        return match.group(1) if match else None

    @lru_cache(maxsize=32768)
    def _lookup(self, key):
        out = []
        for surface, lemma, ident, pos, msd, raw, encoded_link in self.connection.execute(
                'SELECT surface,lemma,lemma_id,pos,msd,raw,dictionary_link FROM analyses WHERE form_key=?', (key,)):
            link = json.loads(encoded_link)
            linked = self.dictionary_entry(link['offset'])
            linked_clean = linked.replace('_', '').replace('^', '') if linked else None
            link.update(source_file=self.metadata['source_file'], source_sha256=self.metadata['source_sha256'],
                        dictionary_file=self.metadata.get('dictionary_file'), dictionary_sha256=self.metadata.get('dictionary_sha256'),
                        dictionary_headword=linked,
                        headword_match=('exact' if diogenes.key(headword(linked_clean)) == diogenes.key(lemma) else 'mismatch')
                                       if linked else 'not_checked')
            out.append(Analysis(surface, lemma, ident, pos, msd, raw, dictionary_link=link))
        return tuple(out)

    def lookup(self, form):
        if not form or '…' in form:
            return ()
        found = self._lookup(diogenes.key(form))
        if not found and (plain := diogenes.without_enclitic_accent(form)):
            found = self._lookup(diogenes.key(plain))
        return found

    def headwords(self):
        return {r[0] for r in self.connection.execute('SELECT DISTINCT lemma FROM analyses')}

    def lemma_evidence(self, lemmas):
        """One preserved example per dictionary POS class, including unknown classes."""
        lemmas = tuple(dict.fromkeys(lemmas))
        found = {lemma: {} for lemma in lemmas}
        for start in range(0, len(lemmas), 900):
            batch = lemmas[start:start + 900]
            placeholders = ','.join('?' for _ in batch)
            rows = self.connection.execute(
                f'SELECT lemma,lemma_id,pos,msd,raw FROM analyses WHERE lemma IN ({placeholders})', batch)
            for lemma, ident, pos, msd, raw in rows:
                if pos not in found[lemma]:
                    found[lemma][pos] = {'lemma': lemma, 'lemma_id': ident, 'pos': pos,
                                         'msd': msd, 'raw': raw, 'source': 'diogenes'}
        return {lemma: list(classes.values()) for lemma, classes in found.items()}

    def close(self):
        self._lookup.cache_clear()
        self.dictionary_entry.cache_clear()
        if self._dict_stream is not None:
            try:
                self._dict_stream.close()
            except OSError:
                pass
            self._dict_stream = None
        self.connection.close()


def load(path, cache_dir=None, log=lambda m: None):
    st = os.stat(path)
    cache_dir = cache_dir or os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/.cache'),
                                         'AG-Diogenes-SpaCy-txt2xml')
    os.makedirs(cache_dir, exist_ok=True)
    dictionary = os.path.join(os.path.dirname(path), 'grc.lsj.xml')
    dictionary_stamp = ''
    if os.path.isfile(dictionary):
        ds = os.stat(dictionary)
        dictionary_stamp = f'-lsj-{ds.st_size}-{ds.st_mtime_ns}'
    cache = os.path.join(cache_dir, f'analyses-v{VERSION}-{st.st_size}-{st.st_mtime_ns}{dictionary_stamp}.sqlite')
    if not os.path.isfile(cache):
        fd, pending = tempfile.mkstemp(prefix='analyses-', suffix='.sqlite', dir=cache_dir)
        os.close(fd)
        connection = None
        try:
            connection = sqlite3.connect(pending)
            connection.execute('CREATE TABLE analyses (form_key TEXT,surface TEXT,lemma TEXT,lemma_id TEXT,'
                               'pos TEXT,msd TEXT,raw TEXT,dictionary_link TEXT)')
            connection.execute('CREATE TABLE metadata (key TEXT,value TEXT)')
            def fingerprint(filename):
                h = hashlib.sha256()
                with open(filename, 'rb') as stream:
                    for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
                return h.hexdigest()
            metadata = {'source_file': os.path.abspath(path), 'source_sha256': fingerprint(path)}
            dictionary = os.path.join(os.path.dirname(path), 'grc.lsj.xml')
            if os.path.isfile(dictionary):
                metadata.update(dictionary_file=os.path.abspath(dictionary), dictionary_sha256=fingerprint(dictionary))
            connection.executemany('INSERT INTO metadata VALUES (?,?)', metadata.items())
            batch = []
            with open(path, encoding='utf-8', errors='replace') as fh:
                for n, line in enumerate(fh, 1):
                    for a in parse_line(line):
                        batch.append((diogenes.key(a.surface), a.surface, a.lemma, a.lemma_id, a.pos, a.msd, a.raw,
                                      json.dumps(a.dictionary_link, ensure_ascii=False)))
                    if len(batch) >= 5000:
                        connection.executemany('INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?)', batch)
                        batch.clear()
                    if n % 100000 == 0:
                        log(f'  Morpheus: {n:,} μορφές')
            connection.executemany('INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?)', batch)
            connection.execute('CREATE INDEX forms ON analyses(form_key)')
            connection.execute('CREATE INDEX lemmas ON analyses(lemma)')
            connection.commit()
            connection.close()
            connection = None
            os.replace(pending, cache)
        finally:
            if connection is not None:
                connection.close()
            if os.path.exists(pending):
                os.remove(pending)
    else:
        # Lazily add the lemma index to existing v2 caches without triggering a full Morpheus rebuild.
        try:
            upgrade = sqlite3.connect(cache, timeout=5)
            try:
                has_idx = upgrade.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='index' AND name='lemmas'").fetchone()
                if not has_idx:
                    upgrade.execute('CREATE INDEX IF NOT EXISTS lemmas ON analyses(lemma)')
                    upgrade.commit()
            finally:
                upgrade.close()
        except sqlite3.Error:
            pass
    return Store(cache)
