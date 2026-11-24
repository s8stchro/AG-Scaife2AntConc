"""Command line: ag-scaife2antconc FILE.xml ... -o FOLDER [--corrections FILE --xml --provenance]."""
import argparse
import os
import sys

from . import APP_NAME, __version__
from .pipeline import MODEL, Lemmatizer, Options, SetupError, run

# --keep names -> reader.STEPS
KEEP = {'hyphenation': 'hyphenation', 'hypothesis': 'hypothesis', 'notes': 'notes', 'speakers': 'speaker names',
        'headings': 'headings', 'stage': 'stage directions', 'deletions': 'deletions',
        'readings': 'rejected readings'}


def main(argv=None):
    p = argparse.ArgumentParser(prog='ag-scaife2antconc', description=f'{APP_NAME} {__version__}: Ancient Greek '
                                'TEI from Scaife Viewer / Perseus to a cleansed text with lemma and POS of each '
                                'word for AntConc (antconc-tagged/), a review sheet for corrections '
                                '(lemma-review.tsv) and a report.')
    p.add_argument('--version', action='version', version=f'{APP_NAME} {__version__}')
    p.add_argument('input', nargs='*', help='Scaife or Perseus TEI XML files, or OGA .conllu files')
    p.add_argument('-o', '--output', help='output folder (default: antconc-output next to the first file)')
    p.add_argument('--xml', action='store_true', help='also TEI with @lemma and @pos (xml/), e.g. for the '
                   'accuracy measurement of gold.py, which needs --provenance')
    p.add_argument('--morphology', action='store_true', help='XML: also @msd (UD features)')
    p.add_argument('--provenance', action='store_true', help='XML: also @resp (where each lemma comes from)')
    p.add_argument('--corrections', metavar='FILE', help='a corrected lemma-review.tsv: its corrections replace '
                   'the lemmas the machine gives')
    p.add_argument('--keep', default='', help='cleansing steps to skip, comma-separated: '
                   + ', '.join(KEEP) + ' (e.g. --keep speakers,headings keeps the character names)')
    p.add_argument('--accents', action='store_true', help='normalise the accents: grave as acute, no second '
                   'accent from an enclitic (καὶ -> καί, ὄρνιθός τινος -> ὄρνιθος); default: as in the edition')
    p.add_argument('--morpho', action='store_true', help=argparse.SUPPRESS)  # accepted, ignored since 0.7.2
    p.add_argument('--replace', action='store_true', help='remove obsolete output files, including omitted books '
                   'and XML files not requested in this run (by default a run refuses such a folder)')
    p.add_argument('--diogenes', help='Diogenes folder (default: the usual install locations)')
    p.add_argument('--model', default=MODEL, help=f'contextual model identity (required: {MODEL})')
    p.add_argument('--context-model', metavar='FOLDER', help='verified GreBERTa joint ONNX folder')
    p.add_argument('--prepare-context', action='store_true', help='download and verify GreBERTa joint')
    p.add_argument('--context-archive', metavar='FILE', help='pinned GreBERTa archive for offline preparation')
    p.add_argument('--greta-model', metavar='FOLDER', help='trained GreTa ONNX folder (required; default: application cache)')
    p.add_argument('--prepare-greta', action='store_true', help='download and verify the required trained GreTa model')
    p.add_argument('--greta-archive', metavar='FILE', help='verified GreTa release archive for offline preparation')
    p.add_argument('--prepare', action='store_true',
                   help='build the Diogenes index and verify GreBERTa joint and GreTa (used by the installer)')
    for stream in (sys.stdout, sys.stderr):        # before parse_args: --help has Greek examples
        stream.reconfigure(encoding='utf-8')
    args = p.parse_args(argv)
    unknown = [k for k in args.keep.split(',') if k and k not in KEEP]
    if unknown:
        p.error(f'--keep: unknown step {", ".join(unknown)}; choose from {", ".join(KEEP)}')
    opts = Options(xml=args.xml, corrections=args.corrections, morphology=args.morphology,
                   provenance=args.provenance, diogenes=args.diogenes, model=args.model,
                   greta_model=args.greta_model, context_model=args.context_model,
                   keep=frozenset(KEEP[k] for k in args.keep.split(',') if k), accents=args.accents,
                   replace=args.replace)
    try:
        if args.prepare_context:
            from ag_diogenes_spacy_txt2xml.greberta import prepare, JointError
            try:
                prepare(args.context_model, archive_path=args.context_archive)
            except (OSError, JointError) as error:
                raise SetupError(str(error)) from error
            return
        if args.prepare_greta:
            from ag_diogenes_spacy_txt2xml.greta import prepare, GretaError
            try:
                prepare(args.greta_model, archive_path=args.greta_archive)
            except (OSError, GretaError) as error:
                raise SetupError(str(error)) from error
            return
        if args.prepare:
            lem = Lemmatizer(opts, print)
            lem.close()
            return
        if not args.input:
            p.error('give at least one XML or OGA .conllu file')
        outdir = args.output or os.path.join(os.path.dirname(os.path.abspath(args.input[0])), 'antconc-output')
        run(args.input, outdir, opts)
    except SetupError as e:
        sys.exit(str(e))


if __name__ == '__main__':
    main()
