"""Read a TEI file from Scaife Viewer / Perseus and keep only the text in the strict sense.

Cleansing steps (STEPS), each counted in the log and each one can be switched off with `keep`:
  hyphenation       a word divided by a hyphen (ἀπο- | μορξάμενον) is joined (ἀπομορξάμενον)
  hypothesis        the hypothesis/argument (Ὑπόθεσις) is left out
  notes             notes (apparatus, Loeb and Perseus notes)
  speaker names     <speaker>; when kept, the name is put before the first line of the speech
  headings          headings and labels (Μάρτυρες)
  stage directions  <stage>
  deletions         editorial deletions ([...], <surplus>, <del>)
  rejected readings <sic>, <rdg> (when kept: written after the reading of the text)
Gaps stay marked internally so damaged words can be excluded from AntConc. Page and card
milestones are left out. Always kept: supplements 〈...〉, <add>,
<supplied>, quotations, cruces (the signs themselves are removed when the word is written out).

Each citation unit becomes one source.Line: a verse line (<l n>) or the lowest numbered
textpart (a section of a speech). Its label is the citation: 12, 1.12 (book.line), ...
"""
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ag_diogenes_spacy_txt2xml.source import Line, add_milestones
from ag_diogenes_spacy_txt2xml.tokenize import GAP_MARK

TEI = '{http://www.tei-c.org/ns/1.0}'
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'

STEPS = ['hyphenation', 'hypothesis', 'notes', 'speaker names', 'headings', 'stage directions', 'deletions',
         'rejected readings']
# element -> cleansing step; None: always left out
DROP = {
    'note': 'notes', 'speaker': 'speaker names', 'label': 'headings', 'head': 'headings',
    'stage': 'stage directions', 'argument': 'hypothesis', 'castList': 'headings', 'trailer': 'headings',
    'del': 'deletions', 'surplus': 'deletions', 'sic': 'rejected readings', 'rdg': 'rejected readings',
    'bibl': 'notes', 'figure': 'notes', 'fw': 'notes',
    'space': None, 'milestone': None, 'pb': None, 'cb': None,
}
# in <choice> / <app>: the reading of the text
PREFERRED = ('corr', 'reg', 'expan', 'lem')
# textpart subtypes that are not part of the citation (Ar. Ach. is cited by line only)
NOT_CITED = {'strophe', 'antistrophe', 'epode', 'mesode', 'ephymnion', 'stanza', 'choral', 'episode',
             'prologue', 'parodos', 'parabasis', 'exodus', 'lyric-scene', 'stasimon', 'kommos',
             'epirrhema', 'antepirrhema', 'pnigos', 'antipnigos', 'ode', 'antode', 'monody'}
HYPOTHESIS = re.compile(r'^\W*(ὑπόθεσις|ὑποθέσεις|hypothesis|argumentum|argument)\b', re.I)
BLOCKS = {'p', 'ab', 'lg', 'sp', 'quote', 'cit', 'q', 'said', 'list', 'item', 'floatingText', 'body', 'div'}
DELETION = {'[': ']', '⟦': '⟧'}
GREEK_CHAR = r'[\u0370-\u0373\u0376\u0377\u037a-\u037d\u037f\u0386\u0388-\u038a\u038c\u038e-\u03a1\u03a3-\u03ff\u1f00-\u1fff]'
GREEK_WORD = re.compile(rf'{GREEK_CHAR}+')
# hyphen, Unicode hyphen, non-breaking hyphen, soft hyphen; not – or —, which mark an interruption
HYPHENS = '‐‑­-'          # ASCII hyphen last: literal inside [...]
LETTER = rf'{GREEK_CHAR}[̀-ͯ]*'
# ἀπο- μορξάμενον: group 1 the first part, group 2 (not consumed) the word that continues it
HYPHEN_INSIDE = re.compile(rf'([^\s{HYPHENS}]*{LETTER})[{HYPHENS}]\s*(?=({GREEK_CHAR}\S*))')
HYPHEN_AT_END = re.compile(rf'{LETTER}[{HYPHENS}]$')
MAX_EXAMPLES = 5


def local(el):
    return el.tag.rsplit('}', 1)[-1] if isinstance(el.tag, str) else ''


def greek_words(text):
    return len(GREEK_WORD.findall(text or ''))


def squeeze(text):
    return ' '.join((text or '').split())


@dataclass
class Work:
    path: str
    urn: str = ''
    title: str = ''
    lang: str = ''
    lines: list = field(default_factory=list)       # [source.Line]
    dropped: Counter = field(default_factory=Counter)   # step -> Greek words left out
    elements: Counter = field(default_factory=Counter)  # step -> elements left out (hyphenation: words joined)
    kept: Counter = field(default_factory=Counter)      # step -> elements kept because the user said so
    examples: dict = field(default_factory=lambda: defaultdict(list))   # step -> a few examples
    speakers: Counter = field(default_factory=Counter)  # speaker name -> times
    warnings: list = field(default_factory=list)

    @property
    def ident(self):
        """tlg0017.tlg001 from urn:cts:greekLit:tlg0017.tlg001.perseus-grc2."""
        m = re.search(r'(tlg\d+\.tlg\d+)', self.urn)
        return m.group(1) if m else ''

    def example(self, step, text):
        text = squeeze(text)
        if text and len(self.examples[step]) < MAX_EXAMPLES and text not in self.examples[step]:
            self.examples[step].append(text if len(text) <= 80 else text[:77] + '…')


class Reader:
    def __init__(self, work, keep):
        self.work = work
        self.keep = keep
        self.units = []          # [(label, text)]
        self.buffer = []
        self.speaker = ''        # a kept speaker name, waiting for the first line of the speech

    def leaves_out(self, el):
        """True if the element is left out (and counted); False if it stays in the text."""
        tag = local(el)
        if tag not in DROP:
            return False
        step = DROP[tag]
        if step in self.keep:
            self.work.kept[step] += 1
            return False
        if step:
            text = ''.join(el.itertext())
            self.work.dropped[step] += greek_words(text)
            self.work.elements[step] += 1
            self.work.example(step, text)
        if tag == 'speaker':
            self.work.speakers[squeeze(''.join(el.itertext()))] += 1
        return True

    # -- text collection ------------------------------------------------------------
    def collect(self, el):
        """The text of an inline element (or a <p>) without what is left out."""
        out = [el.text or '']
        for child in el:
            out.append(self.inline(child))
            out.append(child.tail or '')
        return ''.join(out)

    def inline(self, child):
        tag = local(child)
        if tag == 'gap':
            unit = child.get('unit', 'character').lower()
            if unit in ('word', 'words', 'line', 'lines', 'paragraph', 'paragraphs', 'page', 'pages'):
                return ' '
            # The shared tokenizer uses ! for a lost letter. Keep it attached to surviving
            # letters: a space would turn the fragments into apparently complete words.
            try:
                count = max(1, int(child.get('quantity', '1')))
            except ValueError:
                count = 1
            if unit not in ('char', 'chars', 'character', 'characters', 'letter', 'letters'):
                count = 1
            return GAP_MARK * count
        if self.leaves_out(child):
            if tag in ('pb', 'cb', 'milestone'):
                return '' if child.get('break') == 'no' else ' '
            return ' ' if tag in ('note', 'label', 'head', 'stage', 'speaker') else ''
        if tag == 'lb':
            return '' if child.get('break') == 'no' else ' '
        if tag in ('choice', 'app'):
            kept = next((c for name in PREFERRED for c in child if local(c) == name), None)
            if kept is None and len(child):
                kept = child[0]
            others = [c for c in child if c is not kept]
            if 'rejected readings' in self.keep:
                self.work.kept['rejected readings'] += len(others)
                return ' '.join(self.collect(c) for c in [kept] + others if c is not None)
            for c in others:
                self.work.dropped['rejected readings'] += greek_words(''.join(c.itertext()))
                self.work.elements['rejected readings'] += 1
                self.work.example('rejected readings', ''.join(c.itertext()))
            return self.collect(kept) if kept is not None else ''
        text = self.collect(child)
        # a kept note: <foreign>αὑτοὺς</foreign>Schoemann must not become one word
        return f' {text} ' if tag in BLOCKS or tag in DROP or tag == 'foreign' else text

    # -- citation units -------------------------------------------------------------
    def flush(self, chain):
        text = squeeze(''.join(self.buffer))
        self.buffer = []
        if text:
            label = '.'.join(chain) or None
            if label is not None and self.units and self.units[-1][0] == label:
                # A split verse (antilabe: <l part="M|F"> without @n) continues the line it shares a
                # number with: one cited line, so word identifiers (citation, token) stay unique.
                self.units[-1] = (label, self.units[-1][1] + ' ' + text)
            else:
                self.units.append((label, text))

    def is_hypothesis(self, div):
        if (div.get('subtype') or '').lower() in ('hypothesis', 'argument', 'argumentum'):
            return True
        if any(local(d) == 'div' for d in div.iter() if d is not div):
            return False        # only a lowest-level textpart is recognised by its heading
        first = next((e for e in div.iter() if local(e) in ('label', 'head')), None)
        return first is not None and bool(HYPOTHESIS.match(''.join(first.itertext()).strip('〈〉[] ')))

    def take_speaker(self):
        name, self.speaker = self.speaker, ''
        return name + ' ' if name else ''

    def walk(self, el, chain):
        self.buffer.append(el.text or '')
        for child in el:
            tag = local(child)
            if tag == 'speaker' and 'speaker names' in self.keep:
                self.work.kept['speaker names'] += 1
                self.speaker = squeeze(self.collect(child))
            elif tag in DROP:
                if not self.leaves_out(child):
                    self.buffer.append(f' {self.take_speaker()}{self.collect(child)} ')
            elif tag == 'div':
                if 'hypothesis' not in self.keep and self.is_hypothesis(child):
                    text = ''.join(child.itertext())
                    self.work.dropped['hypothesis'] += greek_words(text)
                    self.work.elements['hypothesis'] += 1
                    self.work.example('hypothesis', text)
                    continue
                if 'hypothesis' in self.keep and self.is_hypothesis(child):
                    self.work.kept['hypothesis'] += 1
                n = child.get('n')
                cited = n and (child.get('subtype') or '').lower() not in NOT_CITED
                self.flush(chain)
                prev_line_n = getattr(self, 'last_line_n', None)
                self.last_line_n = None
                inner = chain + [n] if cited else chain
                self.walk(child, inner)
                self.flush(inner)
                self.last_line_n = prev_line_n
            elif tag == 'l':
                self.flush(chain)
                text = self.take_speaker() + self.collect(child)
                if child.get('n') and child.get('style') != 'hidden':
                    self.last_line_n = child.get('n')
                    self.buffer.append(text)
                    self.flush(chain + [child.get('n')])
                elif (child.get('part') or '').upper() in ('M', 'F') and getattr(self, 'last_line_n', None):
                    self.buffer.append(text)
                    self.flush(chain + [self.last_line_n])
                else:
                    self.buffer.append(text)
                    self.flush(chain)
            elif any(local(d) == 'l' for d in child.iter()):
                self.walk(child, chain)        # <sp>, <lg>, a quotation of verse in prose
                self.buffer.append(' ')
            else:
                self.buffer.append(self.take_speaker() + self.inline(child))
                if tag in BLOCKS:
                    self.buffer.append(' ')
            self.buffer.append(child.tail or '')


def join_hyphenation(units, work):
    """ἀπο- μορξάμενον -> ἀπομορξάμενον, inside a unit and across units: the rest of the word,
    with any punctuation attached to it, moves up to the unit where the word begins."""
    def inside(m):
        if not m.group(2)[0].islower():        # a capital after the hyphen: not one word
            return m.group(0)
        work.elements['hyphenation'] += 1
        work.example('hyphenation', f'{m.group(0).strip()} {m.group(2)} → {m.group(1)}{m.group(2)}')
        return m.group(1)

    out = []
    for label, text in units:
        text = HYPHEN_INSIDE.sub(inside, text)
        if out and HYPHEN_AT_END.search(out[-1][1]) and text[:1].islower():
            head, _, rest = text.partition(' ')
            prev_label, prev = out[-1]
            first = prev.split()[-1]
            work.example('hyphenation', f'{first} | {head} → {first[:-1]}{head}')
            out[-1] = (prev_label, prev[:-1] + head)
            work.elements['hyphenation'] += 1
            text = rest
        if text.strip():
            out.append((label, text.strip()))
    soft = [(label, squeeze(text.replace('­', ''))) for label, text in out]   # soft hyphens left over
    return [(label, text) for label, text in soft if text]


def strip_deletions(units, work):
    """[deleted words] and ⟦...⟧ out, also when the brackets span several units."""
    out, closing = [], None
    for label, text in units:
        kept, removed = [], []
        for ch in text:
            if closing:
                if ch == closing:
                    closing = None
                    work.example('deletions', '[' + ''.join(removed[-40:]).split('[')[-1] + ']')
                else:
                    removed.append(ch)
            elif ch in DELETION:
                closing = DELETION[ch]
                removed.append('[')
                work.elements['deletions'] += 1
            elif ch in DELETION.values():
                continue                       # a lone closing bracket
            else:
                kept.append(ch)
        work.dropped['deletions'] += greek_words(''.join(removed))
        text = squeeze(''.join(kept))
        if text:
            out.append((label, text))
    return out


def read(path, keep=frozenset()):
    """Parse a Scaife/Perseus TEI file into a Work with source.Line objects.
    keep: cleansing steps (from STEPS) not to carry out."""
    root = ET.parse(path).getroot()
    work = Work(path)
    edition = next((d for d in root.iter() if local(d) == 'div' and d.get('type') == 'edition'), None)
    if edition is None:
        edition = next((d for d in root.iter() if local(d) == 'body'), root)
        work.warnings.append('no <div type="edition">: the whole <body> was read')
    work.urn = edition.get('n') or ''
    work.lang = edition.get(XML_LANG) or next((e.get(XML_LANG) for e in root.iter() if e.get(XML_LANG)), '')
    if work.lang and work.lang != 'grc':
        work.warnings.append(f'the text is marked xml:lang="{work.lang}", not Ancient Greek (grc)')
    title = next((e for e in root.iter() if local(e) == 'title' and ''.join(e.itertext()).strip()), None)
    work.title = squeeze(''.join(title.itertext())) if title is not None else ''

    reader = Reader(work, keep)
    reader.walk(edition, [])
    reader.flush([])
    units = reader.units
    if 'hyphenation' in keep:
        work.kept['hyphenation'] = sum(len(HYPHEN_INSIDE.findall(t)) for _, t in units) + sum(
            bool(HYPHEN_AT_END.search(a)) and b[:1].islower() for (_, a), (_, b) in zip(units, units[1:]))
    else:
        units = join_hyphenation(units, work)
    if 'deletions' in keep:
        work.kept['deletions'] += sum(t.count('[') + t.count('⟦') for _, t in units)
    else:
        units = strip_deletions(units, work)
    work.lines = [Line(None, label, text) for label, text in units]
    add_milestones(work.lines)
    if not work.lines:
        work.warnings.append('no text found')
    return work
