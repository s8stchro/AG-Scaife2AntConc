"""Command line: normalize -> tokenize -> Morpheus/GreTa/contextual grammar -> TEI."""
import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET

from . import APP_NAME, __version__, diogenes
from .morpho import NAME as MORPHO_NAME, Morpho
from .normalize import ANO_TELEIA, APOSTROPHE, Normalizer
from .source import dehyphenate, format_line, read_lines
from .tei import document
from .tokenize import Word, tokenize

MODEL = 'grc-joint-v3'


def codepoint(s):
    """Accept a character or U+XXXX / XXXX."""
    if len(s) == 1:
        return s
    return chr(int(s.upper().removeprefix('U+'), 16))


def main(argv=None):
    p = argparse.ArgumentParser(prog='ag-diogenes-spacy-txt2xml', description=f'{APP_NAME} {__version__}: '
                                'normalize an Ancient Greek text, lemmatize it with the Morpheus analyses of '
                                'Diogenes, required GreTa and contextual grammar, and write TEI XML.')
    p.add_argument('input', help='UTF-8 text file, one line per row (`number  label : text` or plain text)')
    p.add_argument('-o', '--output', help='TEI file (default: <input>.tei.xml)')
    p.add_argument('--title', help='title for the TEI header (default: file name)')
    p.add_argument('--morphology', action='store_true',
                   help='also write @pos and @msd from the selected analysis and contextual grammar; default: @lemma only')
    p.add_argument('--provenance', action='store_true',
                   help='also write @resp: the evidence used to select each lemma (Diogenes, GreTa, context, ...)')
    p.add_argument('--diogenes', help='Diogenes folder or its greek-analyses.txt '
                   '(default: looked for in the usual install locations)')
    p.add_argument('--morpho', action='store_true', help='ask Morpho (Logeion, needs the internet) for the '
                   'words that Diogenes cannot decide; answers are cached')
    p.add_argument('--model', default=MODEL, choices=[MODEL], help='pinned contextual model identity')
    p.add_argument('--context-model', help='verified GreBERTa joint ONNX folder (default: application cache)')
    p.add_argument('--greta-model', help='required trained GreTa ONNX folder (default: application cache)')
    p.add_argument('--batch-size', type=int, default=64, help='retained compatibility option; joint inference uses single-sentence CPU execution')
    p.add_argument('--crasis', help='crasis lexicon (TSV) instead of the built-in crasis.tsv')
    p.add_argument('--apostrophe', type=codepoint, default=APOSTROPHE, help='elision mark (default: U+2019)')
    p.add_argument('--ano-teleia', type=codepoint, default=ANO_TELEIA, help='ano teleia (default: U+0387)')
    p.add_argument('--lines', type=int, help='process only the first N lines (for trials)')
    p.add_argument('--no-lemmas', action='store_true', help='normalize and write TEI without lemmatizing')
    args = p.parse_args(argv)
    sys.stderr.reconfigure(encoding='utf-8')

    analyses = None
    if not args.no_lemmas:
        analyses = diogenes.find(args.diogenes)
        if not analyses:
            sys.exit('Diogenes not found' + (f' at {args.diogenes}' if args.diogenes else ' in the usual places')
                     + '. Give its folder with --diogenes "C:\\...\\Diogenes".')

    stem = os.path.splitext(args.input)[0]
    out = args.output or stem + '.tei.xml'
    lines = read_lines(args.input)[:args.lines]

    # 1. normalize
    norm = Normalizer(args.apostrophe, args.ano_teleia)
    for line in lines:
        line.text = norm(line.text)
    dehyphenate(lines)
    norm_path = os.path.splitext(out)[0].removesuffix('.tei') + '.norm.txt'
    with open(norm_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.writelines(format_line(line, line.text) + '\n' for line in lines)
    print(f'normalized text: {norm_path}', file=sys.stderr)

    # 2. tokenize and lemmatize
    tokens = tokenize(lines, args.apostrophe)
    words = [t for t in tokens if isinstance(t, Word) and t.lang == 'grc']
    print(f'{len(lines)} lines, {len(words)} Greek words', file=sys.stderr)
    description, sources = 'No lemmatization (dry run).', []
    if not args.no_lemmas:
        started = time.time()
        from .analyses import load as load_analyses
        from .greta import Greta
        from .contextual import Contextual
        from .greberta import Joint
        table = load_analyses(analyses)
        dio = f'Diogenes {diogenes.version(analyses)}'.strip()
        print(f'{dio}: complete morphological analyses ({time.time() - started:.0f} s)', file=sys.stderr)

        started = time.time()
        tagger = Joint(args.context_model)
        greta = Greta(args.greta_model)
        print(f'{greta.name}; {tagger.name}: {time.time() - started:.0f} s', file=sys.stderr)
        morpho = None
        if args.morpho:
            morpho = Morpho(progress=lambda i, n: print(f'Morpho: {i}/{n} forms', file=sys.stderr))
        lem = Contextual(table, greta, tagger, morpho, log=lambda m: print(m, file=sys.stderr))
        from .headwords import Reference, standardize_words
        reference = Reference.from_store(table, log=lambda m: print(m, file=sys.stderr))
        if args.crasis:
            from .crasis import Crasis
            lem.expander.crasis = Crasis.load(args.crasis)
        try:
            stats = lem.run(tokens, words)
            from .generated_lemmata import check_generated_lemmata
            stats.update(check_generated_lemmata(words))
            stats.update(standardize_words(words, reference))
        finally:
            if reference: reference.close()
            table.close()
        if morpho and morpho.failed:
            print(f'Morpho did not answer ({morpho.failed}); its cached answers only', file=sys.stderr)
        print('lemma from: ' + ', '.join(f'{k} {v}' for k, v in stats.most_common()), file=sys.stderr)
        description = (f'Full Morpheus candidates in {dio}; ambiguous/unseen lemma selection by {tagger.name}. '
                       f'{greta.name} is a comparator and unresolved-form helper. Crasis is expanded to inflected '
                       'forms; original spelling is preserved. Scores are heuristics, not probabilities. '
                       'Corroborated form/headword POS consistency is recorded separately from provider grammar. '
                       'Headword naming uses the pinned Diogenes/Morpheus + LSJ reference; unresolved mappings remain provisional.')
        if morpho:
            description += f' {MORPHO_NAME}: weak headword evidence; manual verification not established.'
        if args.morphology:
            description += ' @pos and @msd describe the selected lexical analysis, narrowed by contextual grammar.'
        sources = [
            ('diogenes', 'full lexical and grammatical analysis', dio),
            ('greta', 'form-based comparator and unresolved-context helper', greta.name),
            ('context', 'primary contextual lemma, POS and morphology selection', tagger.name),
            ('selection_policy', 'unique local lemma, contextual disambiguation, GreTa helper', 'grc.selection.contextual-primary 1.3.0; MIT'),
            ('lexicon', 'crasis and elision tables', APP_NAME),
            ('grammar_policy', 'corroborated form/headword POS consistency', 'grc.grammar.corroborated-pos 1.0.0; ag-greek-rules 0.8.1'),
        ] + ([('morpho', 'lemma of the form in Morpho, for words Diogenes cannot decide', MORPHO_NAME)]
             if morpho else [])

    # 3. TEI
    normalization = ('Unicode NFC (combining marks reordered first); ano teleia '
                     f'U+{ord(args.ano_teleia):04X}; elision mark U+{ord(args.apostrophe):04X}; '
                     'quotation marks « »; a word broken at a line end is completed on its first line '
                     '(the rest, with attached punctuation, moved up; hyphen removed).')
    attrs = ({'pos', 'msd'} if args.morphology else set()) | ({'resp'} if args.provenance else set())
    xml = document(tokens, args.title or os.path.basename(stem), os.path.basename(args.input), description,
                   normalization, attrs, sources)
    ET.fromstring(xml.encode('utf-8'))      # refuse to write XML that is not well-formed
    with open(out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(xml)

    lemmatized = sum(1 for w in words if w.lemma or w.crasis)
    crasis = sum(1 for w in words if w.crasis)
    print(f'TEI: {out}  ({lemmatized}/{len(words)} words lemmatized, {crasis} crasis)', file=sys.stderr)


if __name__ == '__main__':
    main()
