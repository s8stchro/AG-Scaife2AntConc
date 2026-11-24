"""A fingerprinted local LSJ reference; headword naming never reranks analyses."""
from collections import Counter
from dataclasses import asdict
from functools import lru_cache
import hashlib
import html
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from ag_greek_rules.headwords import assess_headword, headword_key, headword_mappings, RULE_ID, RULE_VERSION
from ag_greek_rules import assess_deictic
from . import diogenes
from .analyses import headword

VERSION = 2
HEADER = re.compile(rb'^\s*<div2\b([^>]*)>')
ATTRIBUTE = re.compile(rb'([\w:]+)="([^"]*)"')
HEAD = re.compile(rb'<head\b[^>]*>(.*?)</head>', re.DOTALL)
WARNING = 'headword standard unresolved; proposal retained for review'
PROVISIONAL = 'selected Diogenes dictionary association is provisional'


@lru_cache(maxsize=8)
def _fingerprint(path, size, modified):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def _identity(beta):
    """A headword's case/accents AND homograph suffix; quantities are typography."""
    value = beta.split(',')[-1].replace('_', '').replace('^', '')
    number = re.search(r'(\d+)$', value)
    return headword_key(headword(value), case_sensitive=True), number[1] if number else ''


class Reference:
    """Locally built heading index, separate from copyrighted lexical contents.

    The cache is keyed by the dictionary's actual SHA-256, not byte offsets alone.
    No data from this index are shipped in the original MIT rules package.
    """
    def __init__(self, dictionary, *, cache_dir=None, store=None, log=lambda message: None):
        self.dictionary = Path(dictionary).resolve()
        stamp = self.dictionary.stat()
        digest = _fingerprint(str(self.dictionary), stamp.st_size, stamp.st_mtime_ns)
        self.sha256, self.store = digest, store
        mapping_catalogue = headword_mappings()
        self.mappings = mapping_catalogue['mappings']
        cache_dir = Path(cache_dir or (Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.cache') /
                                      'AG-Diogenes-SpaCy-txt2xml'))
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = cache_dir / f'headwords-v{VERSION}-{digest}.sqlite'
        if not self.path.is_file():
            log('  Προετοιμασία καταλόγου κεφαλών LSJ (μία φορά για αυτή την έκδοση λεξικού)…')
            self._build()
        self.connection = sqlite3.connect(self.path)
        self.connection.execute('PRAGMA query_only=ON')
        meta = dict(self.connection.execute('SELECT key,value FROM metadata'))
        if meta.get('sha256') != digest or meta.get('version') != str(VERSION):
            self.connection.close()
            raise ValueError('Ο κατάλογος κεφαλών δεν αντιστοιχεί στο λεξικό αναφοράς.')
        self.receipt = {'standard': 'Diogenes/Morpheus headwords anchored to LSJ entries',
                        'dictionary_file': str(self.dictionary), 'dictionary_sha256': digest,
                        'index_version': VERSION, 'entries': int(meta['entries']),
                        'mapping_version': mapping_catalogue['version'],
                        'verification_scope': 'headword identity only; not contextual or morphological correctness'}
        if store is not None:
            self.receipt['morphology_sha256'] = store.metadata.get('source_sha256')

    @classmethod
    def from_store(cls, store, **kwargs):
        path = getattr(store, 'metadata', {}).get('dictionary_file')
        return cls(path, store=store, cache_dir=Path(store.path).parent, **kwargs) if path else None

    @classmethod
    def available(cls, diogenes_path=None, **kwargs):
        source = diogenes.find(diogenes_path)
        dictionary = Path(source).with_name('grc.lsj.xml') if source else None
        return cls(dictionary, **kwargs) if dictionary and dictionary.is_file() else None

    def _build(self):
        fd, pending = tempfile.mkstemp(prefix='headwords-', suffix='.sqlite', dir=self.path.parent)
        os.close(fd)
        connection = None
        try:
            connection = sqlite3.connect(pending)
            connection.execute('CREATE TABLE entries (ident TEXT PRIMARY KEY, headword TEXT, match_key TEXT, dictionary_key TEXT, offset INTEGER)')
            connection.execute('CREATE TABLE metadata (key TEXT,value TEXT)')
            count, offset, batch = 0, 0, []
            with self.dictionary.open('rb') as stream:
                for line in stream:
                    match = HEADER.match(line)
                    if match:
                        attrs = {k.decode(): html.unescape(v.decode('utf-8')) for k, v in ATTRIBUTE.findall(match[1])}
                        beta = attrs.get('key', '')
                        try: lemma = headword(beta.replace('_', '').replace('^', '')) if beta else ''
                        except (IndexError, ValueError): lemma = ''
                        displayed = HEAD.search(line)
                        if displayed:
                            plain = html.unescape(re.sub(r'<[^>]*>', '', displayed[1].decode('utf-8'))).strip(' \t\r\n··:;.,')
                            plain = plain.replace('-', '').replace('‐', '')
                            if headword_key(plain) == headword_key(lemma):
                                lemma = headword_key(plain, case_sensitive=True)
                        if lemma and not any(c.isspace() for c in lemma):
                            position = offset + line.index(b'<div2')
                            # Some source entries repeat orig_id. The pinned byte
                            # position distinguishes them without merging records.
                            ident = f"lsj:{self.sha256}:{position}:{attrs.get('orig_id') or attrs.get('id') or beta}"
                            batch.append((ident, lemma, headword_key(lemma), beta, position))
                            count += 1
                    offset += len(line)
                    if len(batch) >= 2000:
                        connection.executemany('INSERT INTO entries VALUES (?,?,?,?,?)', batch)
                        batch.clear()
            connection.executemany('INSERT INTO entries VALUES (?,?,?,?,?)', batch)
            connection.execute('CREATE INDEX heading ON entries(match_key)')
            connection.execute('CREATE UNIQUE INDEX position ON entries(offset)')
            connection.executemany('INSERT INTO metadata VALUES (?,?)',
                [('sha256', self.sha256), ('version', str(VERSION)), ('entries', str(count))])
            connection.commit()
            connection.close(); connection = None
            os.replace(pending, self.path)
        finally:
            if connection is not None: connection.close()
            if os.path.exists(pending): os.remove(pending)

    @staticmethod
    def _entry(row):
        return dict(zip(('id', 'headword', 'dictionary_key', 'offset'), row), verified=True)

    @lru_cache(maxsize=16384)
    def lookup(self, lemma):
        return tuple(self._entry(row) for row in self.connection.execute(
            'SELECT ident,headword,dictionary_key,offset FROM entries WHERE match_key=?', (headword_key(lemma),)))

    @lru_cache(maxsize=16384)
    def at(self, offset):
        row = self.connection.execute('SELECT ident,headword,dictionary_key,offset FROM entries WHERE offset=?', (offset,)).fetchone()
        return self._entry(row) if row else None

    def linked_entry(self, candidate):
        """A declared association must match the actual pinned entry identity."""
        link = (candidate or {}).get('dictionary_link') or {}
        if link.get('provider_status') != 'linked' or link.get('dictionary_sha256') != self.sha256:
            return None
        entry = self.at(link.get('offset'))
        if entry:
            proposed, proposed_number = _identity(link.get('provider_headword') or '')
            _, linked_number = _identity(entry['dictionary_key'])
            # A missing number asserts no competing homograph identity; the
            # supplied verified offset already identifies the particular entry.
            number_matches = not proposed_number or not linked_number or proposed_number == linked_number
            if number_matches and proposed == headword_key(entry['headword'], case_sensitive=True): return entry
        return None

    def assess(self, form, lemma, *, pos=None, candidate=None, manual=False):
        entries = {e['id']: e for e in self.lookup(lemma)}
        maps = self.mappings
        relevant = [m for m in maps if headword_key(m['from']) == headword_key(lemma)]
        for mapping in relevant:
            for e in self.lookup(mapping['to']): entries[e['id']] = e
        linked = self.linked_entry(candidate)
        if candidate and candidate.get('source') == 'diogenes' and not candidate.get('dictionary_link') and self.store is not None:
            # Legacy evidence lacked link metadata. Verify the preserved identity
            # and exact raw parse against today's pinned table, without altering
            # that old evidence or claiming it already contained the association.
            matches = {}
            for analysis in self.store.lookup(form):
                if analysis.lemma_id == candidate.get('lemma_id') and analysis.raw == candidate.get('raw', ''):
                    entry = self.linked_entry(asdict(analysis))
                    if entry: matches[entry['id']] = entry
            if len(matches) == 1:
                linked = dict(next(iter(matches.values())), association_basis='current table: same morphology identity and raw parse')
        if linked: entries[linked['id']] = linked
        form_entries = set()
        if any(m.get('requires_form_entry') for m in relevant) and self.store is not None:
            for analysis in self.store.lookup(form):
                entry = self.linked_entry(asdict(analysis))
                if entry: form_entries.add(entry['id'])
        return assess_headword(form, lemma, pos=pos, entries=entries.values(), mappings=maps,
            form_entry_ids=form_entries, selected_reference_id=linked['id'] if linked else None,
            provisional_link=bool(candidate and candidate.get('source') == 'diogenes' and not linked), manual=manual)

    def close(self):
        self.lookup.cache_clear(); self.at.cache_clear()
        self.connection.close()


def standardize_record(record, reference, *, manual=False):
    """Standardize one machine record in place, preserving its provider evidence."""
    stats = Counter()
    if manual or record.get('reviewed') or record.get('manual') or 'corrector' in (record.get('resp') or '').split():
        return stats
    readings = record.setdefault('readings', [])
    if not readings:
        readings.append({'kind': 'retained analysis', 'expansion': record.get('expansion') or [record['form']], 'components': []})
    reading = readings[0]
    traces = reading.setdefault('rule_contributions', [])
    if any(t.get('rule_id') == RULE_ID and t.get('rule_version') == RULE_VERSION for t in traces): return stats
    lemmas, tags = (record.get('lemma') or '').split('+'), (record.get('pos') or 'X').split('+')
    forms = record.get('expansion') or [record['form']]
    if not (len(forms) == len(lemmas) == len(tags)): return stats
    renamed = False
    for index, (form, lemma, pos) in enumerate(zip(forms, lemmas, tags)):
        deictic = assess_deictic(form, lemma)
        if deictic.status == 'applied' and lemma != deictic.headword:
            traces.append(dict(deictic.as_dict(), component=index))
            lemmas[index] = lemma = deictic.headword
            renamed = True
            stats['headwords renamed'] += 1
            stats['deictic forms'] += 1
            if len(forms) == 1: record['lemma_id'] = None
        component = (reading.get('components') or [])[index] if index < len(reading.get('components') or []) else {}
        original = next((c for c in component.get('candidates', []) if c.get('lemma_id') == component.get('selected_id')), None)
        assessment = reference.assess(form, lemma, pos=pos, candidate=original) if reference else assess_headword(
            form, lemma, pos=pos, entries=())
        trace = dict(assessment.as_dict(), component=index,
                     provider_lemma_id=record.get('lemma_id') if len(forms) == 1 else component.get('selected_id'))
        traces.append(trace)
        if component: component['headword_standard'] = trace
        if assessment.status == 'applied':
            if lemma != assessment.headword:
                renamed = True
                stats['headwords renamed'] += 1
            lemmas[index] = assessment.headword
            if len(forms) == 1: record['lemma_id'] = assessment.reference_id
            stats['headwords standardized'] += 1
        elif assessment.status == 'not_applicable' and assessment.reference_id and len(forms) == 1:
            record['lemma_id'] = assessment.reference_id
        elif assessment.status == 'abstained':
            record.setdefault('review_reasons', []).append(PROVISIONAL if assessment.reason == 'provisional_provider_link' else WARNING)
            stats['headwords provisional'] += 1
            if len(forms) == 1 and (record.get('lemma_id') or '').startswith('lsj:'): record['lemma_id'] = None
    record['lemma'] = '+'.join(lemmas)
    if renamed: record['resp'] = ' '.join(filter(None, (record.get('resp'), 'headword_policy')))
    record['rule_contributions'] = traces
    record['review_reasons'] = list(dict.fromkeys(record.get('review_reasons') or []))
    reading['headword_reference'] = reference.receipt if reference else {'standard': 'Diogenes/Morpheus + LSJ', 'available': False}
    return stats


def standardize_words(words, reference):
    """Post-selection naming, then the proper-noun class of the named lemma (grc.grammar.proper-noun).
    Preserve raw candidates, units and choices; POS changes only from NOUN to PROPN."""
    from .proper_nouns import apply_proper_noun_record
    stats = Counter()
    for word in words:
        if not word.evidence or '…' in word.form: continue
        record = {'form': word.form, 'expansion': word.expansion or [word.form], 'readings': word.evidence,
                  'lemma': '+'.join(p[1] or '' for p in word.crasis) if word.crasis else word.lemma,
                  'pos': '+'.join(p[2] or 'X' for p in word.crasis) if word.crasis else word.pos or 'X',
                  'resp': word.crasis[-1][3] if word.crasis else word.resp,
                  'lemma_id': word.lemma_id, 'reviewed': word.reviewed, 'review_reasons': word.review_reasons}
        stats.update(standardize_record(record, reference))
        before = record['pos']
        stats.update(apply_proper_noun_record(record))
        word.evidence, word.review_reasons = record['readings'], record['review_reasons']
        tags = record['pos'].split('+') if record['pos'] != before else None
        if word.crasis:
            word.crasis = [(surface, lemma, tags[i] if tags else pos, resp + ' headword_policy' if resp and lemma != old else resp)
                for i, ((surface, old, pos, resp), lemma) in enumerate(zip(word.crasis, record['lemma'].split('+')))]
        else:
            word.lemma, word.lemma_id, word.resp = record['lemma'] or None, record['lemma_id'], record['resp']
            if tags: word.pos = tags[0]
    return stats
