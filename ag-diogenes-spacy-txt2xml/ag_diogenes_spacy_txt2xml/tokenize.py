"""Split the normalized lines into words, punctuation, editorial signs and numbers.

All lines are joined into one stream in which MARK stands for the milestones of the
next line. Words broken at a line end have already been completed (source.dehyphenate).
"""
import re
import unicodedata
from dataclasses import dataclass, field

from .normalize import APOSTROPHE, is_greek_letter

MARK, TITLE_OPEN, TITLE_CLOSE, GAP_MARK = '', '', '', '\ue003'
EDITORIAL = '[]〈〉⟨⟩<>{}†|?*⟦⟧⸤⸥⌊⌋'
KERAIA = 'ʹʹ͵'
OPENERS = '«([〈“‘'
DOT_BELOW = '̣'


@dataclass
class Word:
    pieces: list                  # ('t', text) | ('c', sign) | ('gap', n) | ('ms', [Milestone])
    lang: str = 'grc'
    lemma: str | None = None
    pos: str | None = None        # Universal Dependencies part of speech
    msd: str | None = None        # UD morphological features, e.g. Case=Gen|Number=Sing
    resp: str | None = None       # where the lemma comes from: 'diogenes', 'diogenes spacy', 'spacy', ...
    crasis: list | None = None    # [(surface, lemma, pos, resp), ...] once lemmatized
    space: bool = True
    crasis_msd: list | None = None  # grammatical features of each expanded crasis component
    expansion: list | None = None   # inflected model input; original pieces are never rewritten
    lemma_id: str | None = None
    evidence: list = field(default_factory=list)
    review_reasons: list = field(default_factory=list)
    reviewed: bool = False        # explicitly checked at this occurrence, not inferred from model agreement

    @property
    def form(self):
        """The word as sent to the lemmatizer: no editorial signs, lost letters as …"""
        out = []
        for kind, val in self.pieces:
            if kind == 't':
                out.append(val.replace(DOT_BELOW, ''))
            elif kind == 'gap':
                out.append('…')
        return ''.join(out)


@dataclass
class Token:
    kind: str                     # 'pc' | 'ed' | 'gap' | 'num' | 'ms' | 'title-open' | 'title-close'
    value: object = None
    space: bool = True
    pieces: list = field(default_factory=list)


def char_class(ch, apostrophe=APOSTROPHE, tlg_gap=True):
    if ch == MARK:
        return 'M'
    if ch in (TITLE_OPEN, TITLE_CLOSE):
        return 'T'
    if ch in KERAIA:
        return 'K'
    if ch == apostrophe:
        return 'A'
    if is_greek_letter(ch) or unicodedata.combining(ch):
        return 'G'
    if ch.isdigit():
        return 'D'
    if ch.isalpha():
        return 'L'
    if ch == GAP_MARK or (tlg_gap and ch == '!'):  # lost letter: PUA sentinel or TLG '!'
        return 'X'
    if ch in EDITORIAL:
        return 'E'
    return 'P'


CORE_START = set('GLDKX')
CORE_END = CORE_START | {'A'}


def build_stream(lines):
    """Join lines into one string; return it and the milestone groups in order."""
    parts, groups = [], []
    for line in lines:
        text = re.sub(r'\{\d+', TITLE_OPEN, line.text)
        text = re.sub(r'\}\d+', TITLE_CLOSE, text)
        parts.append(MARK + text)
        groups.append(line.milestones)
    return ' '.join(parts), groups


class Tokenizer:
    def __init__(self, groups, apostrophe=APOSTROPHE, tlg_gap=True):
        self.groups = iter(groups)
        self.apostrophe = apostrophe
        self.tlg_gap = tlg_gap
        self.tokens = []

    def run(self, stream):
        for chunk in stream.split(' '):
            if chunk:
                self._chunk(chunk, space=True)
        return self.tokens

    def _emit(self, tok, space):
        """Append a token; adjacent lost-letter marks become one gap, dots one ellipsis."""
        last = self.tokens[-1] if self.tokens else None
        if not space and isinstance(last, Token) and isinstance(tok, Token):
            if tok.kind == last.kind == 'gap':
                last.value += tok.value
                return
            if tok.kind == last.kind == 'pc' and tok.value == '.' and set(last.value) == {'.'}:
                last.value += '.'
                return
        tok.space = space
        self.tokens.append(tok)

    def _signs(self, s, space):
        for i, ch in enumerate(s):
            self._sign(ch, char_class(ch, self.apostrophe, self.tlg_gap), space and i == 0)

    def _chunk(self, s, space):
        """Peel signs off both edges, split the core at inner punctuation, emit."""
        classes = [char_class(c, self.apostrophe, self.tlg_gap) for c in s]
        starts = [i for i, c in enumerate(classes) if c in CORE_START]
        if not starts:
            self._signs(s, space)
            return
        a = starts[0]
        if a and classes[a - 1] == 'A' and classes[a] == 'G':
            a -= 1  # retain the leading prodelision mark in the Greek word
        b = max(i for i, c in enumerate(classes) if c in CORE_END)
        self._signs(s[:a], space)
        first = a == 0 and space
        seg_start = a
        for i in range(a, b + 1):
            if classes[i] in 'PT':
                if seg_start < i:
                    self._core(s[seg_start:i], first)
                    first = False
                self._sign(s[i], classes[i], first)
                first = False
                seg_start = i + 1
        if seg_start <= b:
            self._core(s[seg_start:b + 1], first)
        self._signs(s[b + 1:], False)

    def _sign(self, ch, cls, space):
        if cls == 'M':
            self._emit(Token('ms', next(self.groups)), space)
        elif cls == 'T':
            self._emit(Token('title-open' if ch == TITLE_OPEN else 'title-close'), space)
        elif cls == 'E':
            self._emit(Token('ed', ch), space)
        elif cls == 'X':
            self._emit(Token('gap', 1), space)
        else:
            self._emit(Token('pc', ch), space)

    def _core(self, s, space):
        classes = [char_class(c, self.apostrophe, self.tlg_gap) for c in s]
        if not any(c in 'GLDK' for c in classes):      # only lost letters and signs
            self._signs(s, space)
            return
        pieces = []
        for ch, cls in zip(s, classes):
            if cls == 'M':
                pieces.append(('ms', next(self.groups)))
            elif cls == 'E':
                pieces.append(('c', ch))
            elif cls == 'X':
                if pieces and pieces[-1][0] == 'gap':
                    pieces[-1] = ('gap', pieces[-1][1] + 1)
                else:
                    pieces.append(('gap', 1))
            elif pieces and pieces[-1][0] == 't':
                pieces[-1] = ('t', pieces[-1][1] + ch)
            else:
                pieces.append(('t', ch))
        has = set(classes)
        letters = ''.join(v for k, v in pieces if k == 't')
        if 'K' in has or ('D' in has and 'G' not in has) or ('G' not in has and re.fullmatch('[IVXLCDM]+', letters)):
            self._emit(Token('num', letters, pieces=pieces), space)
        elif 'G' in has:
            self._emit(Word(pieces), space)
        else:
            self._emit(Word(pieces, lang='la'), space)


def tokenize(lines, apostrophe=APOSTROPHE, tlg_gap=True):
    stream, groups = build_stream(lines)
    return Tokenizer(groups, apostrophe, tlg_gap).run(stream)
