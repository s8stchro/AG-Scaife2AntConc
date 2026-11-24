"""The review sheet (lemma-review.tsv) and the corrections read back from it.

The sheet has one row per form and lemma that the machine gave it, the most doubtful first, with
the places and a line of context. The corrector fills in the last two columns (in LibreOffice
Calc) and gives the file back to the app: on the next run a correction replaces the lemma wherever
the machine gives that form that lemma, in any text. The sheet of that run keeps every correction,
also those that found nothing, so one file can grow from text to text.
"""
import csv
import io
import json
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from ag_diogenes_spacy_txt2xml.crasis import with_accents
from ag_diogenes_spacy_txt2xml.review_priority import word_priority

COLUMNS = ['form', 'lemma', 'POS', 'count', 'source', 'other lemmas of form', 'places',
           'context', 'corrected lemma', 'corrected POS']
REQUIRED = ['form', 'lemma', 'corrected lemma', 'corrected POS']
UNKNOWN = 'spacy unknown'
# where the lemma comes from -> (order in the sheet: most doubtful first, label)
SOURCES = {None: (0, 'no lemma'), UNKNOWN: (1, 'OdyCy only, lemma not in Diogenes'),
           'spacy': (2, 'OdyCy only'), 'diogenes spacy': (3, 'Diogenes, chosen by OdyCy'),
           'diogenes frequency': (3, 'Diogenes, most frequent in text'),
           'diogenes morpho': (3, 'Diogenes, chosen by Morpho'), 'morpho spacy': (3, 'Morpho, chosen by OdyCy'),
           'morpho frequency': (3, 'Morpho, most frequent in text'), 'morpho': (4, 'Morpho, single lemma'),
           'lexicon': (4, 'crasis lexicon'), 'diogenes': (5, 'Diogenes, single lemma')}
NOT_FOUND = 'correction from another text (not found here)'
MAX_PLACES, WINDOW = 20, 5
BOM = chr(0xFEFF)
SCOPE_COLUMNS = ['work', 'citation', 'token']  # optional: override one orthographic token only
CRASIS_COLUMN = 'manual crasis'

# Backward compatibility: accept legacy Greek review-sheet column headers transparently.
LEGACY_COLUMNS = {
    'μορφή': 'form',
    'λήμμα': 'lemma',
    'μέρος λόγου': 'POS',
    'φορές': 'count',
    'προέλευση': 'source',
    'άλλα λήμματα της μορφής': 'other lemmas of form',
    'θέσεις': 'places',
    'συμφραζόμενα': 'context',
    'διορθωμένο λήμμα': 'corrected lemma',
    'διορθωμένο μέρος λόγου': 'corrected POS',
    'έργο': 'work',
    'θέση': 'citation',
    'λέξη': 'token',
    'χειροκίνητη κράση': 'manual crasis',
}


def normalize_header(row):
    return [LEGACY_COLUMNS.get(h.strip(), h.strip()) for h in row]


SOURCES.update({'diogenes greberta': (3, 'Diogenes, chosen by GreBERTa'),
                'lexicon greberta': (3, 'Supported expansion, chosen by GreBERTa'),
                'greberta': (2, 'GreBERTa, without full local morphology'),
                'diogenes greta': (4, 'Morpheus and GreTa agree'),
                'diogenes context': (3, 'Morpheus, with contextual grammar'),
                'greta': (2, 'GreTa, without full local analysis'),
                'lexicon greta': (3, 'expansion lexicon, with GreTa'),
                'needs review': (0, 'lemma/expansion doubt; see analysis-review.jsonl'),
                'analysis warning': (4, 'other observation; lemma not flagged as uncertain')})
SOURCES.update({name + ' lemma_policy': (rank, label + ' · separate deictic headword')
                for name, (rank, label) in list(SOURCES.items()) if name})
SOURCES['oga'] = (3, 'Opera Graeca Adnotata, original annotation')
SOURCES.update({name + ' headword_policy': (rank, label + ' · Diogenes / LSJ headword')
                for name, (rank, label) in list(SOURCES.items()) if name})


def key(form):
    """Corrections match regardless of case and of grave/acute (καὶ = καί)."""
    return with_accents(form)


def lemma_of(w):
    return '+'.join(c[1] or '' for c in w.crasis) if w.crasis else (w.lemma or '')


def pos_of(w):
    return '+'.join(c[2] or '' for c in w.crasis) if w.crasis else (w.pos or '')


def resp_of(w):
    return w.crasis[-1][3] if w.crasis else w.resp


@dataclass
class ManualCrasis:
    cut: int                # boundary counted in base characters, with accents attached
    forms: list[str]        # two restored inflected forms

    def pieces(self, form):
        boundaries = [i for i, char in enumerate(form) if i and not unicodedata.category(char).startswith('M')]
        if type(self.cut) is not int or not 1 <= self.cut <= len(boundaries):
            raise ValueError('Choose a split point inside the written form.')
        cut = boundaries[self.cut - 1]
        parts = (form[:cut], form[cut:])
        if not all(any(char.isalpha() for char in part) for part in parts):
            raise ValueError('Each written part must contain at least one letter.')
        return parts

    def validate(self, form, lemma, pos):
        self.pieces(form)
        if not isinstance(self.forms, list) or len(self.forms) != 2:
            raise ValueError('Provide two inflected forms for manual crasis.')
        for values in (self.forms, lemma.split('+'), pos.split('+')):
            if len(values) != 2 or any(not isinstance(value, str) or not value or
                                       any(char.isspace() or char in '+?' for char in value) for value in values):
                raise ValueError('Provide one inflected form, one lemma, and one POS for each component.')


@dataclass
class Correction:
    lemma: str             # '' = lemma unchanged
    pos: str               # '' = POS unchanged
    row: list              # as read, in the order of COLUMNS
    used: int = 0          # words corrected in this run
    refused: int = 0       # crasis words whose number of lemmas does not match
    scope: tuple | None = None  # (work, citation, token number); None applies globally
    crasis: ManualCrasis | None = None

    def crasis_cell(self):
        return json.dumps(asdict(self.crasis), ensure_ascii=False) if self.crasis else ''


def rows_of(path):
    """The rows of a sheet as saved by a spreadsheet: UTF-8 or UTF-16, tab, ; or , between cells.
    ValueError with a message for the user if it is not Unicode."""
    with open(path, 'rb') as fh:
        data = fh.read()
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text = data.decode('utf-16')                    # "Unicode text" from a spreadsheet
    else:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('file is not saved as UTF-8. Save as UTF-8 (tab-delimited TSV or CSV).') from None
    first = text.split('\n', 1)[0]
    delimiter = '\t' if '\t' in first else ';' if ';' in first else ','
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def read(path):
    """{(key(form), lemma): Correction} for the rows with something in the last two columns.
    Accepts both English and legacy Greek column headers.
    ValueError with a message for the user if the file cannot be read as a review sheet."""
    rows = rows_of(path)
    header = normalize_header(rows[0]) if rows else []
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ValueError(f'missing columns: {", ".join(missing)}. Use the lemma-review.tsv '
                         'written by the application without altering the header row.')
    index = {c: header.index(c) for c in COLUMNS if c in header}
    crasis_index = header.index(CRASIS_COLUMN) if CRASIS_COLUMN in header else None
    out = {}
    for row in rows[1:]:
        cells = [row[index[c]].strip() if c in index and index[c] < len(row) else '' for c in COLUMNS]
        form, lemma, new_lemma, new_pos = cells[0], cells[1], cells[8], cells[9]
        crasis_cell = row[crasis_index].strip() if crasis_index is not None and crasis_index < len(row) else ''
        if not form or not (new_lemma or new_pos or crasis_cell):
            continue
        if '?' in form + lemma + new_lemma:
            raise ValueError(f'row "{form}" contains ? instead of Greek letters: the file was saved in '
                             'a non-Unicode encoding. Save it as UTF-8.')
        scope_cells = [row[header.index(c)].strip() if c in header and header.index(c) < len(row) else ''
                       for c in SCOPE_COLUMNS]
        scope = None
        if any(scope_cells):
            try:
                if not all(scope_cells) or int(scope_cells[2]) < 1:
                    raise ValueError()
                scope = (*scope_cells[:2], int(scope_cells[2]))
            except ValueError:
                raise ValueError('For an occurrence-specific correction, provide work, citation, and a positive token number.') from None
        match = (key(form), lemma) + (scope or ())
        manual = None
        if crasis_cell:
            try:
                manual = ManualCrasis(**json.loads(crasis_cell))
            except (TypeError, ValueError):
                raise ValueError(f'Invalid manual crasis entry for "{form}".') from None
            manual.validate(form, new_lemma, new_pos)
        if match in out and (out[match].lemma, out[match].pos, out[match].crasis) != (new_lemma, new_pos, manual):
            raise ValueError(f'Conflicting corrections for "{form}" at the same location.')
        out[match] = Correction(new_lemma, new_pos, cells, scope=scope, crasis=manual)
    return out


def apply(pairs, corrections, work=None, manual_details=None, aliases=()):
    """Correct the words [(form, word)]; {id(word): (lemma, pos, resp)} as the machine had them.
    A position-specific row may name the work by `work` or by one of its older `aliases`."""
    before = {}
    names = (work, *(a for a in aliases if a and a != work))
    for pair in pairs:
        form, w = pair[:2]
        match = (key(form), lemma_of(w))
        c = next((corrections[s] for s in (match + (n, pair[2], pair[3]) for n in names)
                  if s in corrections), None) if len(pair) == 4 else None
        c = c or corrections.get(match)
        if not c:
            continue
        old = (lemma_of(w), pos_of(w), resp_of(w))
        if c.crasis:
            pieces = c.crasis.pieces(w.form)
            if manual_details is not None:
                manual_details[id(w)] = {'manual_crasis': asdict(c.crasis), 'machine_expansion': w.expansion or [form]}
            w.expansion = list(c.crasis.forms)
            w.crasis = [(piece, lemma, tag, 'corrector')
                        for piece, lemma, tag in zip(pieces, c.lemma.split('+'), c.pos.split('+'))]
            w.lemma = w.pos = w.resp = w.msd = w.lemma_id = w.crasis_msd = None
            w.reviewed = bool(c.scope)
        elif w.crasis:
            lemmas = c.lemma.split('+') if c.lemma else [p[1] for p in w.crasis]
            tags = c.pos.split('+') if c.pos else [p[2] for p in w.crasis]
            if len(lemmas) != len(w.crasis) or len(tags) != len(w.crasis):
                c.refused += 1
                continue
            w.reviewed = bool(c.scope)
            if lemmas == [p[1] for p in w.crasis] and tags == [p[2] for p in w.crasis]:
                before[id(w)] = old
                c.used += 1
                continue  # confirmation keeps the original grammar and lexical identities
            w.crasis = [(p[0], lem or None, tag or None, 'corrector' if lem != p[1] or tag != p[2] else p[3])
                        for p, lem, tag in zip(w.crasis, lemmas, tags)]
            w.crasis_msd = None  # old grammatical features may no longer describe the corrected reading
        else:
            if '+' in c.lemma:
                c.refused += 1
                continue
            w.reviewed = bool(c.scope)
            if (not c.lemma or c.lemma == w.lemma) and (not c.pos or c.pos == w.pos):
                before[id(w)] = old
                c.used += 1
                continue
            if c.lemma:
                w.lemma, w.resp = c.lemma, 'corrector'
            else:
                w.resp = f'{w.resp or ""} corrector'.strip()
            w.pos = c.pos or w.pos
            w.msd = None            # the features were for the machine's analysis
            w.lemma_id = None       # a manual replacement has not been linked to a lexical identity
        before[id(w)] = old
        c.used += 1
    return before


class Sheet:
    """The rows of lemma-review.tsv, collected work by work."""

    def __init__(self, known=None):
        """known: Diogenes headwords, also used to prioritise legacy OdyCy-only results."""
        self.rows = {}
        self.known = known

    def add(self, work, sections, before):
        for _, lines in sections:
            for cit, ws in lines:
                for i, (form, _, w) in enumerate(ws):
                    lemma, pos, resp = before.get(id(w)) or (lemma_of(w), pos_of(w), resp_of(w))
                    if resp == 'spacy' and self.known is not None and lemma.split('+')[-1] not in self.known:
                        resp = UNKNOWN
                    if word_priority(w):
                        resp = 'needs review'
                    elif w.review_reasons:
                        resp = 'analysis warning'
                    r = self.rows.setdefault((key(form), lemma), {
                        'forms': Counter(), 'pos': Counter(), 'resp': resp, 'places': [], 'context': None})
                    r['forms'][form] += 1
                    r['pos'][pos] += 1
                    if order(resp) < order(r['resp']):
                        r['resp'] = resp
                    r['places'].append((work, cit))
                    if r['context'] is None:
                        r['context'] = context([f for f, _, _ in ws], i)

    def text(self, corrections):
        works = {wk for r in self.rows.values() for wk, _ in r['places']}
        lemmas = defaultdict(set)
        for k, lemma in self.rows:
            lemmas[k].add(lemma)
        out = []
        has_crasis = any(c.crasis for c in corrections.values())
        for (k, lemma), r in self.rows.items():
            c = corrections.get((k, lemma))
            n = sum(r['forms'].values())
            row = [r['forms'].most_common(1)[0][0], lemma, r['pos'].most_common(1)[0][0], str(n),
                   SOURCES.get(r['resp'], (0, r['resp']))[1], ', '.join(sorted(lemmas[k] - {lemma})),
                   places(r['places'], len(works) > 1), r['context'], c.lemma if c else '', c.pos if c else '']
            out.append(((bool(c), order(r['resp']), -n, k), row, c))
        has_scope = any(c.scope for c in corrections.values())
        padding = ['', '', ''] if has_scope else []
        rows = [row + padding + ([c.crasis_cell() if c else ''] if has_crasis else [])
                for _, row, c in sorted(out, key=lambda item: item[:2])]
        for c in corrections.values():
            manual = [c.crasis_cell()] if has_crasis else []
            if c.scope:
                rows.append(c.row[:4] + ['occurrence-specific correction'] + c.row[5:]
                            + [str(v) for v in c.scope] + manual)
            elif not (c.used or c.refused):
                rows.append(c.row[:4] + [NOT_FOUND] + c.row[5:] + padding + manual)
        buf = io.StringIO()
        header = COLUMNS + (SCOPE_COLUMNS if has_scope else []) + ([CRASIS_COLUMN] if has_crasis else [])
        csv.writer(buf, delimiter='\t', lineterminator='\n').writerows([header] + rows)
        return BOM + buf.getvalue()


def order(resp):
    return SOURCES.get(resp, (0,))[0]


def context(forms, i):
    a, b = max(0, i - WINDOW), min(len(forms), i + WINDOW + 1)
    words = forms[a:i] + [f'«{forms[i]}»'] + forms[i + 1:b]
    return ('… ' if a else '') + ' '.join(words) + (' …' if b < len(forms) else '')


def places(found, with_work):
    by_work = defaultdict(list)
    for work, cit in found:
        if cit not in by_work[work]:
            by_work[work].append(cit)
    parts = []
    for work, cits in by_work.items():
        shown = ', '.join(cits[:MAX_PLACES]) + (f' … (+{len(cits) - MAX_PLACES})' if len(cits) > MAX_PLACES else '')
        parts.append(f'{work}: {shown}' if with_work else shown)
    return '; '.join(parts)
