"""Occurrence review model and interoperable correction-sheet persistence."""
import csv
from collections import defaultdict
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import tempfile

from . import review
from .file_io import replace_file
from .pipeline import SetupError
from ag_diogenes_spacy_txt2xml.review_priority import priority_reasons

POS_NAMES = {'ADJ': 'Adjective', 'ADP': 'Preposition', 'ADV': 'Adverb', 'AUX': 'Auxiliary verb',
             'CCONJ': 'Coordinating conjunction', 'DET': 'Determiner / Article', 'INTJ': 'Interjection',
             'NOUN': 'Noun', 'NUM': 'Numeral', 'PART': 'Particle', 'PRON': 'Pronoun',
             'PROPN': 'Proper noun', 'SCONJ': 'Subordinating conjunction', 'VERB': 'Verb', 'X': 'Other / Unknown'}


def identity(record):
    return record['work'], record['citation'], record['token']


def original(record):
    before = record.get('machine_before_correction')
    return (before[0], before[1]) if before else (record['lemma'], record['pos'])


class Workspace:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.sheet = self.folder / 'lemma-review.tsv'
        try:
            with (self.folder / 'analysis-review.jsonl').open(encoding='utf-8') as fh:
                self.records = [json.loads(line) for line in fh if line.strip()]
            keys = [identity(r) for r in self.records]
            if not keys or len(set(keys)) != len(keys):
                raise ValueError('non-unique occurrence identifiers')
            for record in self.records:
                if not isinstance(record['token'], int) or not isinstance(record['readings'], list):
                    raise ValueError('invalid evidence')
                if (not all(isinstance(record[k], str) for k in ('work', 'citation', 'form', 'lemma', 'pos')) or
                        not isinstance(record['expansion'], list) or not record['expansion'] or
                        not all(isinstance(s, str) for s in record['expansion']) or
                        not isinstance(record['review_reasons'], list) or
                        not all(isinstance(s, str) for s in record['review_reasons'])):
                    raise ValueError('invalid occurrence')
            self.corrections = review.read(self.sheet)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SetupError('Folder does not contain valid per-occurrence analysis results.') from error
        self.by_id = {identity(r): r for r in self.records}
        self.priorities = {identity(r): priority_reasons(r) for r in self.records}
        lines = defaultdict(list)
        for record in self.records:
            lines[record['work'], record['citation']].append(record)
        self.contexts = {}
        for rows in lines.values():
            forms = [r['form'] for r in rows]
            for i, record in enumerate(rows):
                self.contexts[identity(record)] = review.context(forms, i)

    def matching_correction(self, record):
        old, _ = original(record)
        match = (review.key(record['form']), old)
        return self.corrections.get(match + identity(record)) or self.corrections.get(match)

    def values(self, record):
        c = self.matching_correction(record)
        return ((c.lemma or original(record)[0], c.pos or original(record)[1]) if c else
                (record['lemma'], record['pos']))

    def manual_crasis(self, record):
        correction = self.matching_correction(record)
        if correction:
            return asdict(correction.crasis) if correction.crasis else None
        return record.get('manual_crasis')

    def expansion(self, record):
        manual = self.manual_crasis(record)
        return manual['forms'] if manual else record.get('machine_expansion', record['expansion'])

    def checked(self, record):
        c = self.matching_correction(record)
        return bool(record.get('reviewed') or (c and c.scope))

    def visible(self, query='', filter_name='uncertain'):
        query = review.key(query.strip())
        out = []
        for r in self.records:
            checked = self.checked(r)
            if filter_name == 'uncertain' and (not self.priorities[identity(r)] or checked):
                continue
            if filter_name == 'warnings' and (self.priorities[identity(r)] or not r['review_reasons'] or checked):
                continue
            if filter_name == 'checked' and not checked:
                continue
            applied = {t.get('rule_id') for t in r.get('rule_contributions', []) if t.get('status') == 'applied'}
            annotation = ' deictic δεικτικό -ι' if 'grc.lemma.deictic-iota-headword' in applied else ''
            if 'grc.grammar.corroborated-pos' in applied:
                annotation += ' corroborated γραμματική συμφωνία'
            if query and query not in review.key(' '.join((r['form'], self.values(r)[0], r['work'], r['citation'])) + annotation):
                continue
            out.append(r)
        return out

    def choose(self, record, lemmas, tags, all_matches=False, crasis=None):
        count = 2 if crasis is not None else len(record.get('machine_expansion', record['expansion']))
        if len(lemmas) != count or len(tags) != count or any('+' in l or '?' in l for l in lemmas):
            raise ValueError('Provide one lemma per word in the expansion, without + or ?.')
        if any(tag not in POS_NAMES for tag in tags):
            raise ValueError('Choose a valid part of speech for each word.')
        old_lemma, old_pos = original(record)
        for new, old in zip(lemmas, old_lemma.split('+')):
            if (not new.strip() and old) or any(c.isspace() for c in new.strip()):
                raise ValueError('Each lemma must be a single non-empty word. An unknown lemma may remain empty.')
        new_lemma, new_pos = '+'.join(l.strip() for l in lemmas), '+'.join(tags)
        manual = None
        if crasis is not None:
            manual = review.ManualCrasis(crasis['cut'], [form.strip() for form in crasis['forms']])
            manual.validate(record['form'], new_lemma, new_pos)
        row = [record['form'], old_lemma, old_pos, '1', 'manual review', '', record['citation'],
               self.contexts[identity(record)], new_lemma, new_pos]
        match = (review.key(record['form']), old_lemma)
        pending = {}
        if all_matches:
            pending[match] = review.Correction(new_lemma, new_pos, row, crasis=manual)
        scoped = match + identity(record)
        pending[scoped] = review.Correction(new_lemma, new_pos, row, scope=identity(record), crasis=manual)
        self._write(pending)

    def _write(self, choices):
        # Re-read before saving: preserve unrelated changes made in a spreadsheet meanwhile.
        existing_rows = review.rows_of(self.sheet)
        fresh = review.read(self.sheet)
        fresh.update(choices)
        header = review.normalize_header(existing_rows[0])
        index = {name: header.index(name) for name in review.COLUMNS if name in header}
        has_crasis = any(c.crasis for c in fresh.values())
        rows = []
        for raw in existing_rows[1:]:
            cells = [raw[index[c]] if c in index and index[c] < len(raw) else '' for c in review.COLUMNS]
            if not cells[8].strip() and not cells[9].strip():
                rows.append(cells + ['', '', ''] + ([''] if has_crasis else []))
        for c in fresh.values():
            scope = [str(v) for v in c.scope] if c.scope else ['', '', '']
            rows.append(c.row[:8] + [c.lemma, c.pos] + scope + ([c.crasis_cell()] if has_crasis else []))
        buf = io.StringIO()
        columns = review.COLUMNS + review.SCOPE_COLUMNS + ([review.CRASIS_COLUMN] if has_crasis else [])
        csv.writer(buf, delimiter='\t', lineterminator='\n').writerows([columns] + rows)
        fd, pending = tempfile.mkstemp(prefix='.corrections-', suffix='.tsv', dir=self.folder)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as fh:
                fh.write(review.BOM + buf.getvalue())
                fh.flush()
                os.fsync(fh.fileno())
            replace_file(pending, self.sheet)
            self.corrections = fresh
        finally:
            if os.path.exists(pending):
                os.remove(pending)
