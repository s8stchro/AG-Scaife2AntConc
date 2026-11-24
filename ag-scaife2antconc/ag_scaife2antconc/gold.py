"""A hand-checked sample of words, for measuring how often the lemmas are right.

    python -m ag_scaife2antconc.gold sample xml\\Work.xml ... -o gold-sample.tsv [-n 800]
    python -m ag_scaife2antconc.gold score gold-sample.tsv xml\\Work.xml ...

The XML is the app's xml/ output, written with --xml --provenance. The sample is stratified by
where the lemma comes from, so that the small, doubtful groups (OdyCy only, crasis) get enough
rows; each row carries its weight, the number of words of its group it stands for, so that the
accuracy of the whole text can be estimated. The corrector fills «σωστό λήμμα»: + if the lemma is
right, the right lemma if it is not, ? if unsure; empty means not checked yet. `score` compares
any run with the checked rows, so that a change to the lemmatizer can be measured on the same words.
"""
import argparse
import csv
import io
import math
import os
import random
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

from ag_diogenes_spacy_txt2xml.crasis import acute

from . import review

T = '{http://www.tei-c.org/ns/1.0}'
LANG = '{http://www.w3.org/XML/1998/namespace}lang'
COLUMNS = ['αρχείο', 'θέση', 'λέξη', 'συμφραζόμενα', 'μορφή', 'λήμμα', 'σωστό λήμμα', 'ομάδα', 'βάρος']
RIGHT, UNSURE = '+', '?'
GROUPS = {key: name for key, (_, name) in review.SOURCES.items()} | {'crasis': 'κράση', 'corrector': 'διορθωτής'}


class Token:
    def __init__(self, work, cit, n, form, lemma, resp, crasis):
        self.work, self.cit, self.n, self.form, self.lemma = work, cit, n, form, lemma
        self.resp, self.crasis = resp, crasis

    @property
    def place(self):
        return self.work, self.cit, self.n


def tokens(path):
    """The Greek words of a TEI file of the app, in order; a crasis is one word."""
    work = os.path.splitext(os.path.basename(path))[0]
    root = ET.parse(path).getroot()
    parts = {id(w) for seg in root.iter(T + 'seg') if seg.get('type') == 'crasis' for w in seg.iter(T + 'w')}
    out, cit, n = [], '', 0
    for el in root.iter():
        if el.tag == T + 'milestone' and el.get('unit') == 'line':
            cit, n = el.get('n'), 0
        elif el.tag == T + 'seg' and el.get('type') == 'crasis':
            ws = list(el.iter(T + 'w'))
            if not ws:
                continue
            n += 1
            out.append(Token(work, cit, n, ''.join(el.itertext()), '+'.join(w.get('lemma', '') for w in ws),
                             ws[-1].get('resp', ''), True))
        elif el.tag == T + 'w' and id(el) not in parts and el.get(LANG) in (None, 'grc'):
            n += 1
            out.append(Token(work, cit, n, ''.join(el.itertext()), el.get('lemma', ''), el.get('resp', ''), False))
    return out


def group(t, heads=None):
    """Where the lemma comes from: a key of GROUPS."""
    if t.crasis:
        return 'crasis'
    if not t.lemma:
        return None
    resp = t.resp.replace('#', '')
    if resp == 'spacy' and heads is not None and t.lemma not in heads:
        return review.UNKNOWN
    return resp or None


def allocate(sizes, n):
    """{group: rows}: as equal as the group sizes allow (small groups whole), n rows in all."""
    out, left = {}, n
    for i, (g, size) in enumerate(sorted(sizes.items(), key=lambda kv: kv[1])):
        out[g] = min(size, round(left / (len(sizes) - i)))
        left -= out[g]
    return out


def sample(paths, n=800, seed=1, heads=None):
    """The rows of the sample sheet, in the order of the text."""
    words = [t for p in paths for t in tokens(p)]
    by_group = defaultdict(list)
    for i, t in enumerate(words):
        by_group[group(t, heads)].append(i)
    rng = random.Random(seed)
    chosen = {}
    for g, k in allocate({g: len(ix) for g, ix in by_group.items()}, n).items():
        for i in rng.sample(by_group[g], k):
            chosen[i] = (g, len(by_group[g]) / k)
    rows = []
    for i in sorted(chosen):
        t, (g, weight) = words[i], chosen[i]
        same_work = [j for j in range(max(0, i - review.WINDOW), min(len(words), i + review.WINDOW + 1))
                     if words[j].work == t.work]
        context = review.context([words[j].form for j in same_work], same_work.index(i))
        rows.append([t.work, t.cit, str(t.n), context, t.form, t.lemma, '', GROUPS.get(g, g), f'{weight:.3f}'])
    return rows


def sheet_text(rows):
    buf = io.StringIO()
    csv.writer(buf, delimiter='\t', lineterminator='\n').writerows([COLUMNS] + rows)
    return review.BOM + buf.getvalue()


def norm(lemma):
    """Lemmas compared without homograph numbers, grave as acute, case kept (Νόμος ≠ νόμος)."""
    return '+'.join(re.sub(r'\d+$', '', acute(unicodedata.normalize('NFC', p.strip())))
                    for p in (lemma or '').split('+'))


def read_sheet(path):
    rows = review.rows_of(path)
    header = [h.strip() for h in rows[0]] if rows else []
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise ValueError(f'λείπουν οι στήλες {", ".join(missing)}: δεν είναι φύλλο δείγματος.')
    index = {c: header.index(c) for c in COLUMNS}
    return [{c: (row[index[c]].strip() if index[c] < len(row) else '') for c in COLUMNS}
            for row in rows[1:] if any(cell.strip() for cell in row)]


def wilson(k, n, z=1.96):
    """95% interval for a proportion k/n."""
    if not n:
        return 0.0, 0.0
    p, d = k / n, 1 + z * z / n
    mid, half = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return mid - half, mid + half


def score(sheet, paths):
    """Text report: the run in `paths` against the checked rows of the sheet."""
    rows = read_sheet(sheet)
    run = {t.place: t for p in paths for t in tokens(p)}
    # Results are named after their input file since 0.7.2, older sheets after the catalogue:
    # one work on each side is the same work whatever its two names.
    sheet_works, run_works = {r['αρχείο'] for r in rows}, {place[0] for place in run}
    if len(sheet_works) == len(run_works) == 1 and sheet_works != run_works:
        (sheet_work,), (run_work,) = sheet_works, run_works
        run = {(sheet_work, *place[1:]): t for place, t in run.items()}
    size, checked = Counter(), defaultdict(list)          # per group of the sample
    unsure, missing, errors = 0, [], Counter()
    for r in rows:
        g, weight, mark = r['ομάδα'], float(r['βάρος'].replace(',', '.')), r['σωστό λήμμα']
        size[g] += weight
        if not mark:
            continue
        if mark == UNSURE:
            unsure += 1
            continue
        if '?' in mark:
            raise ValueError(f'η γραμμή «{r["μορφή"]}» έχει ? αντί για γράμματα: αποθηκεύστε το φύλλο ως UTF-8.')
        gold = r['λήμμα'] if mark == RIGHT else mark
        t = run.get((r['αρχείο'], r['θέση'], int(r['λέξη'])))
        if t is None or t.form != r['μορφή']:
            missing.append(r['μορφή'])
            continue
        ok = norm(t.lemma) == norm(gold) and bool(gold)
        checked[g].append(ok)
        if not ok:
            errors[t.form, t.lemma or '—', gold] += 1
    total = sum(size.values())
    out = ['Ομάδα (κατά το δείγμα)                           ελέγχθηκαν  σωστά   ακρίβεια   95% διάστημα',
           '-' * 95]
    estimate, variance, covered = 0.0, 0.0, 0.0
    for g in sorted(size, key=lambda g: -size[g]):
        oks = checked.get(g, [])
        if not oks:
            out.append(f'{g:<48} {0:>10}')
            continue
        k, n = sum(oks), len(oks)
        lo, hi = wilson(k, n)
        out.append(f'{g:<48} {n:>10} {k:>6} {100 * k / n:>9.1f}%   {100 * lo:.1f}–{100 * hi:.1f}%')
        w, p = size[g] / total, k / n
        estimate += w * p
        variance += w * w * p * (1 - p) / n
        covered += w
    out.append('-' * 95)
    if covered:
        est, half = estimate / covered, 1.96 * math.sqrt(variance) / covered
        note = '' if covered > 0.999 else f' (μόνο για τις ομάδες που ελέγχθηκαν: {100 * covered:.0f}% του κειμένου)'
        out.append(f'Εκτίμηση για όλο το κείμενο: {100 * est:.1f}% ± {100 * half:.1f}{note}')
    done = sum(len(v) for v in checked.values())
    out.append(f'Γραμμές: {len(rows)}· ελέγχθηκαν {done}, αβέβαιες (?) {unsure}, '
               f'δεν ελέγχθηκαν ακόμη {len(rows) - done - unsure - len(missing)}.')
    if missing:
        out.append(f'Δεν βρέθηκαν στο XML ({len(missing)}): ' + ', '.join(missing[:20]))
    if errors:
        out.append('\nΛάθη (μορφή: λήμμα του προγράμματος → σωστό):')
        out += [f'  {f}: {l} → {g}' + (f' ({n})' if n > 1 else '') for (f, l, g), n in errors.most_common()]
    return '\n'.join(out) + '\n'


def headwords():
    """The Diogenes headwords, to set apart OdyCy's lemmas unknown to it; None without Diogenes."""
    from ag_diogenes_spacy_txt2xml import analyses, diogenes
    path = diogenes.find()
    if not path:
        return None
    store = analyses.load(path)
    try:
        return store.headwords()
    finally:
        store.close()


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(prog='python -m ag_scaife2antconc.gold', description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('sample', help='write the sample sheet from the xml/ files of a run')
    s.add_argument('xml', nargs='+')
    s.add_argument('-o', '--output', default='gold-sample.tsv')
    s.add_argument('-n', type=int, default=800, help='rows (default 800)')
    s.add_argument('--seed', type=int, default=1)
    c = sub.add_parser('score', help='measure a run against the checked rows of a sample sheet')
    c.add_argument('sheet')
    c.add_argument('xml', nargs='+')
    args = p.parse_args(argv)
    if args.command == 'sample':
        rows = sample(args.xml, args.n, args.seed, headwords())
        with open(args.output, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(sheet_text(rows))
        sizes = Counter(r[7] for r in rows)
        print(f'{args.output}: {len(rows)} γραμμές (' + ', '.join(f'{g} {k}' for g, k in sizes.most_common()) + ')')
    else:
        print(score(args.sheet, args.xml), end='')


if __name__ == '__main__':
    main()
