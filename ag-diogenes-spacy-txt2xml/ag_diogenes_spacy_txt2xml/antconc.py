"""ag-tei2antconc: the lemmatized TEI of AG-Diogenes-SpaCy-txt2xml as plain text files for AntConc.

For each first-level section (milestone unit="segment" without a dot in @n, e.g. Or1):
  <prefix>-forms/<prefix>-NN.txt   the words as written, one lowest-level section per line
  <prefix>-lemmas/<prefix>-NN.txt  the same, each word replaced by its lemma
  <prefix>-tagged/<prefix>-NN.txt  the same, each word as form_POS_lemma, for AntConc's
                                   simple_word_pos_headword_indexer (POS X where the TEI has no @pos)
and one <prefix>-lemma-list-antconc4.txt (lemma<TAB>form<TAB>form ...) for the whole text.
A text without labels gives one file, one line per line of the source.
Left out: titles and editorial headings (seg type="title"), punctuation, numbers, editorial
signs, words with lost letters (<gap>). A crasis gives two lemmas (κἀγὼ -> καί ἐγώ); in the tagged
text it is two tokens, one per piece of the written form, joined by a hair space (U+200A), the
thinnest whitespace: AntConc keeps the whitespace between tokens, and has no way to join them.
"""
import argparse
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

TEI = '{http://www.tei-c.org/ns/1.0}'
NO_LEMMA = 'NOLEMMA'  # Latin letters, so that AntConc counts it as a token
HAIR_SPACE = chr(0x200A)


def _tag(el):
    return el.tag.replace(TEI, '')


def word_text(w):
    """The letters of a <w>, without editorial signs, with ASCII apostrophe; None if letters are lost."""
    if w.find(f'{TEI}gap') is not None:
        return None
    parts = [w.text or ''] + [child.tail or '' for child in w]   # <c type="editorial"> itself is dropped
    return unicodedata.normalize('NFC', ''.join(parts).replace('’', "'"))


def sections(tei_path, levels=None):
    """Yield (first-level section or None, [line: [(form, [(piece, lemma, pos)])]]) in document order;
    a crasis has one piece per word.
    levels: keep only lines whose label has that many levels (Or1.1.1: 3)."""
    ab = ET.parse(tei_path).getroot().find(f'.//{TEI}ab')
    by_segment = any(_tag(m) == 'milestone' and m.get('unit') == 'segment' for m in ab.iter())
    state = {'top': None, 'lines': [], 'words': [], 'skip': False}
    out = []

    def close_line():
        if state['words']:
            state['lines'].append(state['words'])
        state['words'] = []

    def close_top():
        close_line()
        if state['lines']:
            out.append((state['top'], state['lines']))
        state['lines'] = []

    def walk(el):
        for ch in el:
            t = _tag(ch)
            if t == 'milestone':
                if ch.get('unit') == 'segment':
                    n = ch.get('n') or ''
                    if '.' not in n:
                        close_top()
                        state['top'] = n
                    else:
                        close_line()
                elif ch.get('unit') == 'line':
                    state['skip'] = bool(levels) and len((ch.get('n') or '').split('.')) != levels
                    if not by_segment:
                        close_line()
            elif state['skip']:
                continue
            elif t == 'seg' and ch.get('type') == 'title':
                continue
            elif t == 'seg' and ch.get('type') == 'crasis':
                ws = ch.findall(f'{TEI}w')
                parts = [(word_text(w) or '', w.get('lemma'), w.get('pos')) for w in ws]
                state['words'].append((''.join(p[0] for p in parts), parts))
            elif t == 'seg':
                walk(ch)
            elif t == 'w' and ch.get('{http://www.w3.org/XML/1998/namespace}lang') in (None, 'grc'):
                form = word_text(ch)
                if form:
                    state['words'].append((form, [(form, ch.get('lemma'), ch.get('pos'))]))
            # pc, gap, c, num: not words

    walk(ab)
    close_top()
    return out


def tagged(parts):
    """form_POS_lemma; the pieces of a crasis joined by a hair space."""
    return HAIR_SPACE.join('_'.join(x.replace('_', '-') for x in (piece, pos or 'X', lemma or NO_LEMMA))
                           for piece, lemma, pos in parts if piece)


def export(tei_path, outdir, prefix, keep=None, levels=None):
    """Write the AntConc files; return [(file name, section, words)], forms, lemmas."""
    kept = [(n, lines) for n, lines in sections(tei_path, levels) if not keep or (n and re.search(keep, n))]
    width = max(2, len(str(len(kept))))
    forms_dir = os.path.join(outdir, f'{prefix}-forms')
    lemmas_dir = os.path.join(outdir, f'{prefix}-lemmas')
    tagged_dir = os.path.join(outdir, f'{prefix}-tagged')
    for d in (forms_dir, lemmas_dir, tagged_dir):
        os.makedirs(d, exist_ok=True)
    form_lemmas = defaultdict(Counter)
    report = []
    for i, (n, lines) in enumerate(kept, 1):
        name = f'{prefix}-{i:0{width}d}.txt' if len(kept) > 1 or n else f'{prefix}.txt'
        forms, lemmas, tags = [], [], []
        for words in lines:
            forms.append(' '.join(f for f, _ in words))
            lemmas.append(' '.join(p[1] or NO_LEMMA for _, parts in words for p in parts))
            tags.append(' '.join(tagged(parts) for _, parts in words))
            for f, parts in words:
                if parts[-1][1]:  # crasis: the lemma of the last word (κἀγώ -> ἐγώ)
                    form_lemmas[f][parts[-1][1]] += 1
        for d, text in ((forms_dir, forms), (lemmas_dir, lemmas), (tagged_dir, tags)):
            with open(os.path.join(d, name), 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(text) + '\n')
        report.append((name, n, sum(len(w) for w in lines)))
    groups = defaultdict(list)
    for form, counts in form_lemmas.items():
        groups[counts.most_common(1)[0][0]].append(form)
    with open(os.path.join(outdir, f'{prefix}-lemma-list-antconc4.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join('\t'.join([lemma] + sorted(forms)) for lemma, forms in sorted(groups.items())) + '\n')
    return report, len(form_lemmas), len(groups)


def main(argv=None):
    p = argparse.ArgumentParser(prog='ag-tei2antconc', description=__doc__.split('\n')[0])
    p.add_argument('input', help='TEI file written by ag-diogenes-spacy-txt2xml')
    p.add_argument('outdir', nargs='?', help='output folder (default: the folder of the TEI file)')
    p.add_argument('--prefix', help='name of the files and folders (default: the TEI file name)')
    p.add_argument('--keep', help='regular expression: keep only first-level sections whose number matches '
                                  '(e.g. "^Or" for the 34 speeches of Lysias in the TLG)')
    p.add_argument('--levels', type=int, help='keep only lines whose label has N levels (e.g. 3 for '
                                              'Or1.1.1; leaves out the papyri labelled Or34.1.1.1)')
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding='utf-8')
    outdir = args.outdir or os.path.dirname(os.path.abspath(args.input))
    prefix = args.prefix or os.path.basename(args.input).removesuffix('.xml').removesuffix('.tei')
    report, n_forms, n_lemmas = export(args.input, outdir, prefix, args.keep, args.levels)
    if not report:
        p.error('no section left: check --keep')
    for name, n, words in report:
        print(f'{name}\t{n or "-"}\t{words} words')
    print(f'{len(report)} files, {sum(r[2] for r in report)} words; lemma list: {n_lemmas} lemmas, {n_forms} forms')
    print(f'in {outdir}: {prefix}-forms/, {prefix}-lemmas/, {prefix}-tagged/, {prefix}-lemma-list-antconc4.txt')


if __name__ == '__main__':
    main()
