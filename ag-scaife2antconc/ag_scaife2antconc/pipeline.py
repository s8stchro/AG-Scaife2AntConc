"""Scaife/Perseus TEI -> cleansed text -> full Morpheus + GreTa + contextual grammar -> AntConc.

Output folder:
  antconc-tagged/<work>.txt         the cleansed text, one citation unit per line, no punctuation, each word as
                                    form_POS_lemma for AntConc's simple_word_pos_headword_indexer (tagged())
  antconc-lemmata/<work>.txt        the same lines with each word replaced by its lemma (lemmatized()): a crasis
                                    gives its lemmata as separate words, a missing lemma NOLEMMA, as in antconc-tagged
  lemma-review.tsv                 one row per form and lemma, the most doubtful first, for correction (review.py)
  report.txt                        what was left out, how the lemmas were found, words without lemma
  analysis-review.jsonl             per-occurrence full analyses, inflected expansions and disagreements
  xml/<work>.xml                    only with Options.xml (command line): TEI with <w lemma pos> (+ msd, resp),
                                    which gold.py scores
<work> is the input file's own name without its extension (isaeus-1.xml -> isaeus-1.txt); a batch
with two inputs of the same name adds -2, an OGA import -OGA unless its name already says oga.
A work with several first-level sections (books of the Iliad) gives one file per section (<work>-<book>).
Corrections saved under the catalogue names of versions up to 0.7.1 still apply (legacy_names).
The review sheet and the report cover the works of one run, so a run refuses a folder
that holds obsolete files of works or books (Options.replace removes them), and never overwrites a review
sheet with corrections that is not its corrections file.
Results are generated in a temporary folder first; publishing keeps backups for rollback.
"""
import os
from .file_io import input_name, replace_file
import json
import shutil
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from ag_diogenes_spacy_txt2xml.review_priority import word_priority
from dataclasses import dataclass, replace

from ag_diogenes_spacy_txt2xml import diogenes
from ag_diogenes_spacy_txt2xml.antconc import NO_LEMMA
from ag_diogenes_spacy_txt2xml.morpho import SITE
from ag_diogenes_spacy_txt2xml.normalize import ANO_TELEIA, APOS_LIKE, APOSTROPHE, Normalizer
from ag_diogenes_spacy_txt2xml.tei import document
from ag_diogenes_spacy_txt2xml.tokenize import Token, Word, tokenize

from . import APP_NAME, __version__, accents, catalog, oga, review
from .reader import STEPS, read

MODEL = 'grc-joint-v3'
LEMMA_FOLDER = 'antconc-lemmata'
OUTPUT_FOLDERS = ('antconc-tagged', LEMMA_FOLDER, 'xml')     # one file per work
REVIEW = 'lemma-review.tsv'
DIOGENES_URL = 'https://d.iogen.es/d/'
# cleansing step -> (display label for reports and UI, prose phrase for TEI header)
STEP_NAMES = {
    'hyphenation': ('words divided by a hyphen', 'words divided by a hyphen'),
    'hypothesis': ('hypothesis', 'the hypothesis'),
    'notes': ('notes (apparatus)', 'notes'),
    'speaker names': ('speaker names', 'speaker names'),
    'headings': ('headings and labels', 'headings and labels'),
    'stage directions': ('stage directions', 'stage directions'),
    'deletions': ('editorial deletions [ ]', 'editorial deletions [ ]'),
    'rejected readings': ('rejected readings', 'rejected readings'),
}
NORMALIZATION = (f'Unicode NFC (combining marks reordered first); ano teleia U+{ord(ANO_TELEIA):04X}; '
                 f'elision mark U+{ord(APOSTROPHE):04X}; quotation marks « ».')
APOSTROPHES = str.maketrans(dict.fromkeys(APOS_LIKE, "'"))     # AntConc: one apostrophe, the ASCII one
GREEK = {'diogenes': '1 Diogenes, single lemma', 'diogenes spacy': '2 Diogenes, OdyCy choice',
         'diogenes morpho': '3 Diogenes, Morpho choice', 'diogenes frequency': '4 Diogenes, most frequent in text',
         'morpho spacy': '6 Morpho, OdyCy choice',
         'morpho frequency': '7 Morpho, most frequent in text',
         'spacy': '8 OdyCy only (unknown to Diogenes and Morpho)', 'no lemma': 'no lemma'}
GREEK.update({'diogenes greberta': 'Diogenes, GreBERTa choice', 'lexicon greberta': 'Attested expansion, GreBERTa choice',
              'greberta': 'GreBERTa, without full local morphology', 'diogenes greta': 'Morpheus and GreTa agree',
              'diogenes context': 'Morpheus, with contextual grammar',
              'greta': 'GreTa, without full local analysis',
              'lexicon greta': 'expansion lexicon, with GreTa',
              'oga': 'imported OGA lemma',
              'morpho': 'Morpho (unverified; needs review)'})


class SetupError(Exception):
    """Something the user has to install or choose; the message says what."""


@dataclass
class Options:
    xml: bool = False             # TEI with lemma + POS (command line; gold.py scores it)
    corrections: str | None = None  # a corrected lemma-review.tsv
    morphology: bool = False      # XML: also @msd (UD features)
    provenance: bool = False      # XML: also @resp (where the lemma comes from)
    diogenes: str | None = None   # Diogenes folder, if not in the usual place
    model: str = MODEL
    greta_model: str | None = None  # required trained GreTa directory; default greta.default_path()
    context_model: str | None = None  # pinned GreBERTa joint directory; default greberta.default_path()
    keep: frozenset = frozenset()  # cleansing steps (reader.STEPS) not to carry out
    accents: bool = False         # grave as acute, no second accent from an enclitic (accents.py)
    morpho: bool = False          # ignored since 0.7.2 (kept so saved options load): Morpho is reviewer lookup only
    replace: bool = False         # remove obsolete output files of works or books (old_results)
    session: bool = False         # GUI: retain a portable, data-only analysis snapshot for later review


def word_text(w):
    """The word as it goes to AntConc: letters only, ASCII apostrophe; None if letters are lost."""
    if any(k == 'gap' for k, _ in w.pieces):
        return None
    text = ''.join(v for k, v in w.pieces if k == 't')
    return unicodedata.normalize('NFC', text.translate(APOSTROPHES))   # also prodelision: ʼγὼ -> 'γὼ


HAIR_SPACE = chr(0x200A)


def tagged_tokens(form, w):
    """[form_POS_lemma], as AntConc's simple_word_pos_headword_indexer reads a token (split at _). A crasis
    is one token per word, each piece of the written form with its own POS and lemma (κ_CCONJ_καί,
    ἀγώ_PRON_ἐγώ), as UD, the BNC and Sketch Engine split contractions; if the pieces do not spell the
    form, one token under the last word, as in the lemma list."""
    def token(text, tag, lemma):
        return '_'.join(p.replace('_', '-') for p in (text, tag or 'X', lemma or NO_LEMMA))
    if not w.crasis:
        return [token(form, w.pos, w.lemma)]
    pieces = [unicodedata.normalize('NFC', p[0].translate(APOSTROPHES)) for p in w.crasis]
    if ''.join(pieces) != form or not all(pieces):
        return [token(form, w.crasis[-1][2], w.crasis[-1][1])]
    return [token(piece, tag, lemma) for piece, (_, lemma, tag, _) in zip(pieces, w.crasis)]


def tagged(form, w):
    """The word in antconc-tagged: the tokens of a crasis are joined by a hair space, the thinnest
    whitespace. AntConc has no glue between tokens (it keeps the whitespace that follows each token in
    the file and shows it), so the crasis reads as one word with a hair-thin gap: κ ἀγώ."""
    return HAIR_SPACE.join(tagged_tokens(form, w))


def lemmatized(form, w):
    """The word in antconc-lemmata: the lemma of each antconc-tagged token, so both files have the
    same lines and the same number of words; the lemmata of a crasis are separate words (καί ἐγώ)."""
    return ' '.join(token.rsplit('_', 1)[1] for token in tagged_tokens(form, w))


def lemmata_of_tagged(text):
    """An antconc-tagged text as antconc-lemmata: the same as writing each word with lemmatized(),
    since a tagged token ends in _lemma and the tokens of a crasis are joined by a hair space."""
    return '\n'.join(' '.join(token.rsplit('_', 1)[-1] for token in line.replace(HAIR_SPACE, ' ').split(' ') if token)
                     for line in text.split('\n'))


def lemma_path(tagged_path):
    """antconc-tagged/<stem>.txt -> antconc-lemmata/<stem>.txt"""
    return os.path.join(LEMMA_FOLDER, os.path.basename(tagged_path))


def units(tokens):
    """[(first-level section or None, [(citation, [(form, [lemmas], word)])])] in text order."""
    out, lines = [], []
    top, label = None, None
    for t in tokens:
        if isinstance(t, Token) and t.kind == 'ms':
            for m in t.value:
                if m.unit == 'segment' and '.' not in m.n:
                    if lines:
                        out.append((top, lines))
                    top, lines = m.n, []
                elif m.unit == 'line':
                    label = m.n
                    lines.append((label, []))
        elif isinstance(t, Word) and t.lang == 'grc':
            form = word_text(t)
            if form and lines:
                lemmas = [c[1] for c in t.crasis] if t.crasis else [t.lemma]
                lines[-1][1].append((form, lemmas, t))
    if lines:
        out.append((top, lines))
    return [(top, [(c, ws) for c, ws in lines if ws]) for top, lines in out]


def unique_name(name, taken):
    base, i = name, 2
    while os.path.normcase(name) in taken:
        name, i = f'{base}-{i}', i + 1
    taken.add(os.path.normcase(name))
    return name


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


class Lemmatizer:
    """Local morphology, contextual lemma selection and a GreTa comparator."""

    def __init__(self, opts, log):
        self.log = log
        analyses = diogenes.find(opts.diogenes)
        if not analyses:
            raise SetupError('Diogenes was not found. Install it from '
                             f'{DIOGENES_URL} or set its folder in Settings.')
        started = time.time()
        log('Loading the Diogenes morphological table (first time ~2 minutes)…')
        from ag_diogenes_spacy_txt2xml import analyses as full_analyses
        self.table = full_analyses.load(analyses, log=log)
        self.heads = self.table.headwords()
        self.dio = f'Diogenes {diogenes.version(analyses)}'.strip()
        log(f'{self.dio}: full analyses ({time.time() - started:.0f} s)')
        started = time.time()
        log('Loading GreBERTa joint for lemmata and grammar…')
        try:
            from ag_diogenes_spacy_txt2xml.contextual import Contextual
            from ag_diogenes_spacy_txt2xml.greberta import Joint, JointError
            from ag_diogenes_spacy_txt2xml.greta import Greta, GretaError
            if opts.model != MODEL:
                raise JointError(f'New analysis uses {MODEL}. Older snapshots are re-exported without changing the model.')
            self.context = Joint(opts.context_model, progress=lambda i: log(f'  GreBERTa: {i:,} passages'))
            self.spacy = self.context  # Compatibility metadata field for historical data-only snapshots.
        except (OSError, JointError, ValueError) as e:
            self.table.close()
            raise SetupError(str(e)) from e
        log(f'{self.spacy.name} ({time.time() - started:.0f} s)')
        log('Loading trained GreTa…')
        try:
            self.greta = Greta(opts.greta_model, progress=lambda phase, i, n:
                               log(f'  GreTa {"lemmata" if phase == "generation" else "candidates"}: {i:,}/{n:,}'))
        except GretaError as e:
            if hasattr(self.context, 'close'): self.context.close()
            self.table.close()
            raise SetupError(str(e)) from e
        self.morpho = None  # since 0.7.2 no network lookups during analysis; reviewers can still ask Morpho
        self.hybrid = Contextual(self.table, self.greta, self.context, None, log)
        from ag_diogenes_spacy_txt2xml.headwords import Reference
        try:
            self.headword_reference = Reference.from_store(self.table, log=log)
        except Exception:
            if hasattr(self.greta, 'close'): self.greta.close()
            if hasattr(self.context, 'close'): self.context.close()
            self.table.close()
            raise

    def __call__(self, tokens, words):
        stats = self.hybrid.run(tokens, words)
        from ag_diogenes_spacy_txt2xml.generated_lemmata import check_generated_lemmata
        stats.update(check_generated_lemmata(words))
        from .lemma_policy import apply_deictic_policy
        stats['deictic forms'] = apply_deictic_policy(words)
        from ag_diogenes_spacy_txt2xml.headwords import standardize_words
        stats.update(standardize_words(words, self.headword_reference))
        return stats

    def description(self):
        return (f'{self.context.name} selects contextual lemmata using pinned neural-first decoding; '
                'model-owned lookup fallbacks and raw neural predictions are identified separately. '
                f'{self.dio} supplies inspectable morphological candidates, raw dictionary association markers, '
                'headwords, byte offsets and source fingerprints; weak links are not proof of an invalid parse. '
                f'{self.greta.name} is an integrated form-based comparator and unresolved-context helper, '
                'with no candidate rescoring or voting bonus. Morpho is not consulted during analysis; '
                'it remains an on-demand, unverified reviewer lookup. OdyCy is not loaded. '
                'The existing inflected-restoration evidence requirement, restoration priors and context boundaries '
                'are retained. Overlong contexts split at complete word boundaries and carry a review warning. '
                'Edit-script log probabilities and restoration margins are not probabilities of correctness. '
                'The approved corroborated POS and separate deictic-headword policies act after selection; '
                'Selected headwords use the fingerprinted Diogenes/Morpheus + LSJ reference and documented correspondences. '
                'Provisional associations, ambiguous identities and uncovered proposals remain labelled for review; '
                'manual choices remain authoritative. Original evidence, provider receipts and decision traces are retained.')

    def sources(self):
        from ag_diogenes_spacy_txt2xml.greta import LICENSE, URL
        from ag_diogenes_spacy_txt2xml import greberta
        return [('diogenes', 'morphological candidates; raw dictionary-link quality and source fingerprints', self.dio),
                ('greta', 'form-based comparator and unresolved-context helper; not candidate scoring',
                 f'{self.greta.name}; {LICENSE}; {URL}'),
                ('context', 'primary contextual lemma, POS and morphology selection; neural-first with explicit lookup fallback',
                 f'{self.context.name}; {greberta.LICENSE}; {greberta.URL}; SHA256 {greberta.SHA256}'),
                ('lexicon', 'defeasible crasis/elision expansion evidence', 'AG-Diogenes-SpaCy-txt2xml'),
                ('selection_policy', 'contextual primary within the local inventory; canonical lookup, guarded top-k edit scripts, GreTa or attested-headword fallback',
                 'ag-greek-rules 0.8.1; MIT; grc.selection.contextual-primary 1.3.0'),
                ('lemma_policy', 'separate deictic-iota headword convention', 'ag-greek-rules 0.8.1; MIT; grc.lemma.deictic-iota-headword 1.1.0'),
                ('headword_policy', 'post-selection Diogenes/Morpheus + LSJ headword standard; not morphological verification',
                 'ag-greek-rules 0.8.1; MIT; grc.lemma.diogenes-headword 1.1.0; '+
                 (self.headword_reference.sha256 if self.headword_reference else 'reference unavailable')),
                ('grammar_policy', 'corroborated Morpheus headword/form POS consistency', 'ag-greek-rules 0.8.1; MIT; grc.grammar.corroborated-pos 1.0.0'),
                ('generated_lemma_policy', 'checks on lemmata generated for forms without a local analysis',
                 'ag-greek-rules 0.8.1; MIT; grc.selection.generated-lemma-check 1.0.0'),
                ('proper_noun_policy', 'NOUN with a capitalised lemma and written form is PROPN', 'ag-greek-rules 0.8.1; MIT; grc.grammar.proper-noun 1.0.0')]

    def close(self):
        if getattr(self, 'headword_reference', None): self.headword_reference.close()
        if getattr(self, 'greta', None) and hasattr(self.greta, 'close'): self.greta.close()
        if getattr(self, 'context', None) and hasattr(self.context, 'close'): self.context.close()
        self.table.close()


def input_work(path, keep=frozenset()):
    return oga.read(path) if oga.is_oga(path) else read(path, keep)


def oga_options(opts, folder):
    if (any(os.path.lexists(os.path.join(folder, name)) for name in
            (REVIEW, 'analysis-review.jsonl', 'analysis-session.json', 'report.txt')) or old_results(folder, ())):
        raise SetupError('Choose a new output folder for an OGA import. '
                         'For an already imported work, open the existing folder to continue reviewing.')
    return replace(opts, keep=frozenset(), accents=False, morpho=False, xml=False, replace=False)


def plan(paths, keep=frozenset()):
    """List (path, work, file name, label) in run order, retaining XML parse errors."""
    paths = tuple(paths)
    if len({oga.is_oga(path) for path in paths}) > 1:
        raise SetupError('Select either OGA files or XML files in a single run, not both.')
    taken, out = set(), []
    for path in paths:
        try:
            work = input_work(path, keep)
        except ET.ParseError as e:
            out.append((path, e, None, None))
            continue
        entry = catalog.lookup(work.ident)
        name = input_name(path)
        label = entry.display if entry else work.title or work.ident or name
        if isinstance(work, oga.OgaWork):
            label += ' · OGA'
            if 'oga' not in name.casefold():
                name += '-OGA'
        out.append((path, work, unique_name(name, taken), label))
    return out


def legacy_names(works):
    """{name: the catalogue name versions up to 0.7.1 gave the same work}, for the plan of one run.
    Review sheets and analyses saved by those versions still refer to works by these names."""
    taken, out = set(), {}
    for path, work, name, _ in works:
        if name is None:
            continue
        entry = catalog.lookup(work.ident)
        stem = os.path.splitext(os.path.basename(path))[0]
        old = entry.file_name if entry else catalog.slug(work.title) or work.ident or stem
        out[name] = unique_name(old + '-OGA' if isinstance(work, oga.OgaWork) else old, taken)
    return out


def tokens_for(work, opts):
    """Normalize and tokenize a copy of the source lines, for both planning and generation."""
    if isinstance(work, oga.OgaWork):
        return work.tokens, Counter()
    norm = Normalizer()
    lines = [replace(line, text=norm(line.text)) for line in work.lines]
    changed = accents.normalize_lines(lines) if opts.accents else Counter()
    return tokenize(lines, tlg_gap=False), changed


def tagged_paths(name, sections, taken):
    """Reserve exact tagged filenames, including book suffixes and batch collision suffixes."""
    files = []
    for top, _ in sections:
        stem = name if len(sections) == 1 else unique_name(f'{name}-{top}', taken)
        files.append(os.path.join('antconc-tagged', f'{stem}.txt'))
    return files


def expected_results(works, opts):
    """Exact per-work output paths, also used by the GUI before asking about replacement."""
    taken = {os.path.normcase(name) for _, _, name, _ in works if name}
    files = []
    for _, work, name, _ in works:
        if name is None:
            continue
        tokens, _ = tokens_for(work, opts)
        tagged_files = tagged_paths(name, units(tokens), taken)
        files.extend(tagged_files + [lemma_path(rel) for rel in tagged_files])
        if opts.xml:
            files.append(os.path.join('xml', f'{name}.xml'))
    if opts.session:
        files.append('analysis-session.json')
    return files


def old_results(outdir, files):
    """Existing output files absent from this run, including obsolete books of the same work."""
    expected = {os.path.normcase(os.path.normpath(rel)) for rel in files}
    found = []
    for folder in OUTPUT_FOLDERS:
        path = os.path.join(outdir, folder)
        for f in sorted(os.listdir(path)) if os.path.isdir(path) else ():
            rel = os.path.join(folder, f)
            if os.path.isfile(os.path.join(path, f)) and os.path.normcase(rel) not in expected:
                found.append(rel)
    if os.path.isfile(os.path.join(outdir, 'analysis-session.json')) and 'analysis-session.json' not in expected:
        found.append('analysis-session.json')
    return found


def unsaved_corrections(outdir, opts):
    """The review sheet in the output folder, if it has corrections that this run would overwrite
    (it is not the corrections file of the run); None otherwise."""
    path = os.path.join(outdir, REVIEW)
    if not os.path.isfile(path):
        return None
    if opts.corrections and os.path.isfile(opts.corrections) and os.path.samefile(path, opts.corrections):
        return None
    try:
        return path if review.read(path) else None
    except (OSError, ValueError):
        return path                             # not readable as a sheet: do not overwrite it either


def shown(files, n=5):
    return ', '.join(files[:n]) + (f' … (+{len(files) - n})' if len(files) > n else '')


def publish(staged, outdir, files, old):
    """Publish a completed run, keeping backups for rollback if a file cannot be replaced."""
    parent = os.path.dirname(os.path.abspath(outdir))
    backup = tempfile.mkdtemp(prefix='.ag-scaife2antconc-backup-', dir=parent)
    changed = []
    keep_backup = False
    try:
        for rel in dict.fromkeys(files + old):
            target = os.path.join(outdir, rel)
            if os.path.isfile(target):
                saved = os.path.join(backup, rel)
                os.makedirs(os.path.dirname(saved), exist_ok=True)
                shutil.copy2(target, saved)
        for rel in files:
            target = os.path.join(outdir, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            replace_file(os.path.join(staged, rel), target)
            changed.append(rel)
        for rel in old:
            os.remove(os.path.join(outdir, rel))
            changed.append(rel)
    except BaseException as error:
        failed = []
        for rel in reversed(changed):
            target, saved = os.path.join(outdir, rel), os.path.join(backup, rel)
            try:
                if os.path.isfile(saved):
                    replace_file(saved, target)
                else:
                    os.remove(target)
            except OSError:
                failed.append(rel)
        if failed:
            keep_backup = True
            raise SetupError(f'Saving failed and restoring previous files also failed ({shown(failed)}). '
                             f'Previous files are kept in {backup}.') from error
        if not isinstance(error, OSError):
            raise
        raise SetupError('Saving did not complete. Previous results were preserved. '
                         f'Details: {error}') from error
    finally:
        if not keep_backup:
            shutil.rmtree(backup, ignore_errors=True)


def run(paths, outdir, opts, log=print, lemmatizer=None, before_publish=None):
    """Process TEI or OGA files and return the report path. `lemmatizer`: a loaded Lemmatizer (or a
    stand-in in the tests); by default one is loaded here."""
    paths = tuple(paths)
    if not paths:
        raise SetupError('No text was selected.')
    outdir = os.path.normpath(outdir)
    from .working_text import WorkingText
    if WorkingText.exists(outdir):
        raise SetupError('This folder contains an editable working text. Open it in the browser workbench '
                         'to analyze or export the current revision, or choose another output folder.')
    importing = oga.is_oga(paths[0]) and lemmatizer is None
    if importing:
        opts = oga_options(opts, outdir)
    fixes = {}
    if opts.corrections:
        if not os.path.isfile(opts.corrections):
            raise SetupError(f'Corrections file not found: {opts.corrections}.')
        try:
            fixes = review.read(opts.corrections)
        except (OSError, ValueError) as e:
            raise SetupError(f'Corrections file {os.path.basename(opts.corrections)}: {e}') from e
    works = plan(paths, opts.keep)
    if not any(name for _, _, name, _ in works):
        raise SetupError('No valid XML file was found. Previous results were preserved.')
    old = old_results(outdir, expected_results(works, opts))
    if old and not opts.replace:
        raise SetupError(f'Folder {outdir} contains previous outputs not written by this run '
                         f'({shown(old)}). The review sheet and report would cover only the current texts. '
                         'Choose another folder, or delete the old outputs (CLI: --replace).')
    pending = unsaved_corrections(outdir, opts)
    if pending:
        raise SetupError(f'{pending} contains manual corrections and would be overwritten by the new review sheet. '
                         'Pass it as the corrections file, move it, or choose another folder.')
    lem = lemmatizer or (oga.Imported() if importing else Lemmatizer(opts, log))
    if opts.session and not hasattr(lem, 'save_session'):
        from .session import Capture
        lem = Capture(lem, paths, opts)
    parent = os.path.dirname(os.path.abspath(outdir))
    os.makedirs(parent, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.ag-scaife2antconc-stage-', dir=parent) as staged:
            files, totals = generate(works, staged, opts, log, lem, fixes)
            if importing:
                oga_options(opts, outdir)
            if before_publish:
                before_publish()  # final cancellation checkpoint; publishing itself is transactional
            publish(staged, outdir, files, old)
    finally:
        if lemmatizer is None and hasattr(lem, 'close'):
            lem.close()
    if old:
        log(f'Removed previous outputs not written by this run: {shown(old)}.')
    report_path = os.path.join(outdir, 'report.txt')
    log(f'Done: {totals["files"]} files, {totals["words"]:,} words. Report: {report_path}')
    return report_path


def generate(works, outdir, opts, log, lem, fixes):
    """Write all results in a staging folder, without touching the previous run."""
    sheet = review.Sheet(lem.heads)
    report, written, evidence_rows = [], [], []
    totals = Counter()
    # Reserve every work's unsplit filename before assigning any book filenames. A book of
    # Work must not take Work-2.txt when a second input already has the work name Work-2.
    tagged_names = {os.path.normcase(name) for _, _, name, _ in works if name}
    legacy = legacy_names(works)

    for path, work, name, label in works:
        log(f'— {os.path.basename(path)}')
        if name is None:
            log(f'  Invalid XML: {work}')
            report.append(f'{os.path.basename(path)}: invalid XML ({work}); skipped.\n')
            continue
        tokens, changed = tokens_for(work, opts)
        words = [t for t in tokens if isinstance(t, Word) and t.lang == 'grc']
        log(f'  {label}: {len(work.lines)} passages, {len(words):,} words')
        log('  Importing OGA lemmata…' if isinstance(work, oga.OgaWork) else '  lemmatizing…')
        stats = lem(tokens, words)
        stats['crasis'] = sum(1 for w in words if w.crasis)
        sections = units(tokens)
        # Recalculate priorities even for old snapshots, excluding damaged words
        # that do not enter the review workspace or the AntConc export.
        exported = [w for _, lines in sections for _, ws in lines for _, _, w in ws]
        priorities = [word_priority(w) for w in exported]
        stats['review needed'] = sum(bool(p) for p in priorities)
        stats['analysis warnings'] = sum(bool(w.review_reasons) and not p for w, p in zip(exported, priorities))
        manual_details = {}
        before = review.apply([(f, w, cit, i) for _, lines in sections for cit, ws in lines
                               for i, (f, _, w) in enumerate(ws, 1)], fixes, work=name,
                              manual_details=manual_details, aliases=(legacy[name],)) if fixes else {}
        if before:
            stats['corrected'] = len(before)
            stats['crasis'] = sum(1 for w in words if w.crasis)
        sheet.add(name, sections, before)
        for _, lines in sections:
            for cit, ws in lines:
                for i, (form, _, w) in enumerate(ws, 1):
                    evidence_rows.append(json.dumps({'work': name, 'citation': cit, 'token': i, 'form': form,
                        'expansion': w.expansion or [form], 'lemma': review.lemma_of(w),
                        'pos': review.pos_of(w), 'msd': w.crasis_msd if w.crasis else w.msd,
                        'resp': review.resp_of(w), 'lemma_id': w.lemma_id,
                        'reviewed': w.reviewed,
                        'machine_before_correction': before.get(id(w)),
                        'rule_contributions': (w.evidence[0].get('rule_contributions', []) if w.evidence else []),
                        'review_reasons': list(dict.fromkeys(w.review_reasons)), 'readings': w.evidence,
                        **manual_details.get(id(w), {})},
                        ensure_ascii=False, allow_nan=False))
        for rel, (_, lines) in zip(tagged_paths(name, sections, tagged_names), sections):
            written.append(os.path.join(outdir, rel))
            write(written[-1], ''.join(' '.join(tagged(f, w) for f, _, w in ws) + '\n' for _, ws in lines))
            written.append(os.path.join(outdir, lemma_path(rel)))
            write(written[-1], ''.join(' '.join(lemmatized(f, w) for f, _, w in ws) + '\n' for _, ws in lines))
        if opts.xml:
            attrs = {'pos'} | ({'msd'} if opts.morphology else set()) | ({'resp'} if opts.provenance else set())
            source = (f'{os.path.basename(path)} ({work.urn}), Perseus Digital Library via Scaife Viewer, '
                      'CC BY-SA 4.0. ' + cleansing_en(opts.keep, opts.accents))
            sources = lem.sources() + ([('corrector', 'lemma, POS or crasis corrected by hand in the review sheet',
                                         os.path.basename(opts.corrections))] if fixes else [])
            xml = document(tokens, label, source, lem.description(), NORMALIZATION, attrs, sources)
            ET.fromstring(xml.encode('utf-8'))      # refuse to write XML that is not well-formed
            written.append(os.path.join(outdir, 'xml', f'{name}.xml'))
            write(written[-1], xml)

        stats['tagged words'] = sum(len(tagged_tokens(f, w)) for _, lines in sections for _, ws in lines
                                    for f, _, w in ws)
        report.append(work_report(path, name, label, work, words, sections, stats, changed))
        totals['words'] += sum(len(ws) for _, lines in sections for _, ws in lines)
        totals['files'] += 1
        totals['reviewed'] += sum(bool(w.reviewed) for _, lines in sections for _, ws in lines for _, _, w in ws)

    if not totals['words']:
        raise SetupError('No Greek words were found. Previous results were preserved.')
    if sheet.rows or fixes:
        written.append(os.path.join(outdir, REVIEW))
        write(written[-1], sheet.text(fixes))
    written.append(os.path.join(outdir, 'analysis-review.jsonl'))
    write(written[-1], '\n'.join(evidence_rows) + '\n')
    if opts.session and hasattr(lem, 'save_session'):
        written.append(os.path.join(outdir, 'analysis-session.json'))
        lem.save_session(written[-1], os.path.join(outdir, 'analysis-review.jsonl'))
    report_path = os.path.join(outdir, 'report.txt')
    write(report_path, header(opts, lem, totals, fixes) + '\n'.join(report) + '\nFiles written:\n'
          + ''.join(f'  {os.path.relpath(p, outdir)}\n' for p in written) + ANTCONC_HELP)
    return [os.path.relpath(p, outdir) for p in written] + ['report.txt'], totals


def header(opts, lem, totals, fixes):
    when = time.strftime('%Y-%m-%d %H:%M')
    lines = [f'{APP_NAME} {__version__}, {when}', '']
    checked = totals.get('reviewed', 0)
    lines.append(f'Manual occurrence review: {checked:,} / {totals["words"]:,} words.'
                 if checked else 'No manual occurrence review yet. Automatic proposals require review.')
    imported = getattr(lem, 'provider', None) == 'oga'
    if imported:
        lines.extend([f'Source: Opera Graeca Adnotata ({oga.RELEASE}), via oga_src ({oga.EDITION}).',
                      'OGA lemmata and word boundaries were preserved. '
                      'Punctuation, non-Greek tokens and syntactically implied words were omitted.',
                      'Original OGA records are retained in analysis-review.jsonl. '
                      'X marks an original tag without a direct export equivalent.'])
    elif getattr(lem, 'replaying', False):
        lines.append('Re-export of saved analyses; neural lemmatization was not re-run.')
    if lem and not imported:
        lines.append(f'Lemmatization: {lem.dio} (Morpheus) and {lem.spacy.name}.')
        if getattr(lem, 'greta', None):
            lines.append(f'GreTa: {lem.greta.name}. '
                         + ('GreBERTa: primary lemma choice; GreTa: comparator / unknown-word helper. ' if getattr(lem, 'context', None) else
                            'Saved pipeline: see analysis provenance. ')
                         +
                         'Ranking scores are not probabilities of correctness. '
                         'Analyses and disagreements: analysis-review.jsonl.')
        if lem.morpho:
            m = lem.morpho
            lines.append(f'Morpho (Logeion, {SITE}) for words unresolved by Diogenes: {m.asked:,} queries '
                         'in this run, remaining answers from cache'
                         + (f'; stopped responding ({m.failed})' if m.failed else '') + '.')
    if fixes:
        used = [c for c in fixes.values() if c.used]
        text = (f'Corrections from {os.path.basename(opts.corrections)}: {len(fixes):,} rows; '
                f'applied {len(used):,} to {sum(c.used for c in used):,} words')
        unused = sum(1 for c in fixes.values() if not (c.used or c.refused))
        if unused:
            text += f'; {unused:,} not found in these texts'
        refused = [c.row[0] for c in fixes.values() if c.refused]
        if refused:
            text += (f'; not applied to crases because the number of lemmata (separated by +) '
                     f'does not match: {", ".join(refused)}')
        lines.append(text + '.')
    if not imported:
        lines.append(cleansing_en(opts.keep, opts.accents))
    return '\n'.join(lines) + '\n\n'


def names(steps, lang):
    words = [STEP_NAMES[s][lang] for s in steps]
    joiner = ' and '
    return ', '.join(words[:-1]) + joiner + words[-1] if len(words) > 1 else ''.join(words)


def cleansing_gr(keep, normalized=False):
    return cleansing_en(keep, normalized)


def cleansing_en(keep, normalized=False):
    removed = [s for s in STEPS if s not in keep and s != 'hyphenation']
    kept = [s for s in STEPS if s in keep and s != 'hyphenation']
    text = 'Cleansing: left out ' + (f'{names(removed, 1)} and gaps. ' if removed else 'gaps only. ')
    if kept:
        text += f"Kept at the user's request: {names(kept, 1)}. "
    text += ('Words divided by a hyphen joined. ' if 'hyphenation' not in keep
             else 'Words divided by a hyphen left divided. ')
    text += 'Supplements 〈 〉 kept without the brackets.'
    return text + (' Accents normalised: grave written as acute, the second accent that an enclitic puts '
                   'on the last syllable left out.' if normalized else ' Accents kept as in the edition.')


def times(n):
    return f'{n} {"time" if n == 1 else "times"}'


def work_report(path, name, label, work, words, sections, stats, changed):
    kept = sum(len(ws) for _, lines in sections for _, ws in lines)
    kind = 'OGA' if isinstance(work, oga.OgaWork) else 'XML'
    out = [f'== {label}', f'{kind} file: {os.path.basename(path)} → {name}', f'URN: {work.urn or "—"}',
           f'Passages (paragraphs or lines): {len(work.lines)}', f'Words: {kept:,}']
    split = stats['tagged words'] - kept if stats['tagged words'] else 0
    if split:
        out[-1] += (f' orthographic; in antconc-tagged {stats["tagged words"]:,} (+{split:,}), because each crasis '
                    'counts as two words, each with its own lemma and POS (κ ἀγώ: καί + ἐγώ)')
    for w in work.warnings:
        out.append(f'WARNING: {w}')
    removed = [(s, work.elements[s], work.dropped[s]) for s in STEPS if s != 'hyphenation' and work.elements[s]]
    if removed:
        out.append('Removed:')
        out += [f'  {STEP_NAMES[s][0]}: {times(n)}, {g:,} Greek words' for s, n, g in removed]
    if work.speakers:
        out.append('  speakers: ' + ', '.join(f'{s} ({n})' for s, n in work.speakers.most_common()))
    if work.elements['hyphenation']:
        out.append(f'Hyphenated words joined: {work.elements["hyphenation"]} ('
                   + '; '.join(work.examples['hyphenation']) + ')')
    if changed:
        grave, enclitic, examples = accents.summary(changed)
        out.append(f'Accents normalised: {grave:,} words with grave, {enclitic:,} with second accent '
                   f'from an enclitic ({"; ".join(examples)})')
    kept_steps = [(s, work.kept[s]) for s in STEPS if work.kept[s]]
    if kept_steps:
        out.append('Kept as requested:')
        out += [f'  {STEP_NAMES[s][0]}: {times(n)}' for s, n in kept_steps]
    if stats:
        out.append('Lemma sources:')
        for k in GREEK:
            if stats[k]:
                out.append(f'  {GREEK[k]}: {stats[k]:,}')
            if k == 'spacy' and stats['spacy unknown']:
                out.append(f'    of these, with a lemma not in Diogenes: {stats["spacy unknown"]:,} '
                           '(listed first in the review sheet)')
        if stats['crasis']:
            out.append(f'  crases: {stats["crasis"]:,}; the sources above count the expanded words. '
                       'Inflected expansions and alternatives: analysis-review.jsonl.')
        if stats['review needed']:
            out.append(f'Words with substantive lemma or expansion uncertainty: {stats["review needed"]:,}. '
                       'Listed first in lemma-review.tsv; details are in analysis-review.jsonl.')
        if stats['analysis warnings']:
            out.append(f'Other grammatical or secondary notes: {stats["analysis warnings"]:,}. '
                       'Available for inspection, but do not require lemma review on their own.')
        if stats['corrected']:
            out.append(f'  corrected from the corrections file: {stats["corrected"]:,}')
        if stats['pos corrected']:
            out.append(f'POS adjusted to match an indeclinable lemma: {stats["pos corrected"]:,}')
        if stats['generated lemmata replaced']:
            out.append(f'Generated lemmata replaced by the lemma of another form of the same word (review): '
                       f'{stats["generated lemmata replaced"]:,}')
        if stats['generated lemmata removed']:
            out.append(f'Generated lemmata with an impossible spelling, left without lemma (review): '
                       f'{stats["generated lemmata removed"]:,}')
        if stats['proper nouns']:
            out.append(f'Proper nouns (PROPN: capitalised lemma and written form): {stats["proper nouns"]:,}')
        if stats['capitalised lemma, lower-case form']:
            out.append(f'Capitalised lemma on a lower-case form, kept NOUN and listed for review: '
                       f'{stats["capitalised lemma, lower-case form"]:,}')
        if stats['corroborated pos']:
            out.append(f'POS corroborated by dictionary lemma and inflected form: {stats["corroborated pos"]:,}. '
                       'Evidence and initial proposals in analysis-review.jsonl.')
        missing = Counter(w.form for w in words if not w.crasis and not w.lemma)
        if missing:
            out.append('Words without a lemma (please review): ' + ', '.join(
                f'{f} ({n})' if n > 1 else f for f, n in missing.most_common(40))
                + (' …' if len(missing) > 40 else ''))
    return '\n'.join(out) + '\n'


ANTCONC_HELP = '''
In AntConc 4 (tested on 4.4.2):
  1. File → Corpus Manager → Raw Files. Add File(s): the files in antconc-tagged.
     Encoding: UTF-8.
  2. Options → Indexer Options: simple_word_pos_headword_indexer. Create Database.
  3. Word List: display headword+[type] (each lemma with its forms), or headword, pos, etc.
     Concordance: search a lemma in braces, e.g. {καί}.
     A crasis is two tokens separated by a hair space (κ ἀγώ: καί + ἐγώ).
'''
