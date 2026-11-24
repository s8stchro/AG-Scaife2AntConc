"""Write the token stream as TEI P5 (3.3.0): <w lemma pos msd resp>, <pc>, <c type="editorial">,
<gap>, <num>, <milestone unit="segment|line">, <seg type="crasis">, <seg type="title">."""
import datetime
import unicodedata
from xml.sax.saxutils import escape, quoteattr

from . import APP_NAME, __version__
from .tokenize import DOT_BELOW, OPENERS, Token, Word

TEI_NS = 'http://www.tei-c.org/ns/1.0'
VOWELS = set('αεηιουω')


def _milestones(group, inside_word=False):
    out = []
    for m in group:
        attrs = f'unit="{m.unit}" n={quoteattr(m.n)}'
        if m.xml_id:
            attrs += f' xml:id="{m.xml_id}"'
        if inside_word:
            attrs += ' break="no"'
        out.append(f'<milestone {attrs}/>')
    return ''.join(out)


def _pieces(pieces):
    out = []
    for kind, val in pieces:
        if kind == 't':
            out.append(escape(val))
        elif kind == 'c':
            out.append(f'<c type="editorial">{escape(val)}</c>')
        elif kind == 'gap':
            out.append(f'<gap quantity="{val}" unit="chars"/>')
        elif kind == 'ms':
            out.append(_milestones(val, inside_word=True))
    return ''.join(out)


def _counted(ch):
    """Characters that make up Word.form (dot below is dropped there)."""
    return ch != DOT_BELOW


def split_pieces(pieces, cut):
    """Split word pieces after `cut` characters of the word's form."""
    head, tail, pos = [], [], 0
    for kind, val in pieces:
        if pos >= cut:
            tail.append((kind, val))
        elif kind == 't':
            i = 0
            while i < len(val) and pos < cut:
                if _counted(val[i]):
                    pos += 1
                i += 1
            while i < len(val) and unicodedata.combining(val[i]):   # keep marks with their letter
                i += 1
            head.append(('t', val[:i]))
            if val[i:]:
                tail.append(('t', val[i:]))
        else:
            head.append((kind, val))
            pos += kind == 'gap'          # a gap is one … in Word.form
    return head, tail


def crasis_cuts(form, surfaces):
    """Where to cut `form` for the crasis parts Claude proposed.

    Accept the proposal if it spells the form (or matches it letter count for letter
    count, e.g. a grave accent restored as acute); otherwise cut after the opening
    consonants (κ|ἀγώ, τ|ἀληθῆ), which is right for most two-word crasis.
    """
    surfaces = [s.replace(DOT_BELOW, '') for s in surfaces]
    if sum(len(s) for s in surfaces) == len(form) and all(surfaces):
        cuts, pos = [], 0
        for s in surfaces[:-1]:
            pos += len(s)
            cuts.append(pos)
        return cuts
    if len(surfaces) != 2:
        return None
    base = unicodedata.normalize('NFD', form.lower())
    letters = [c for c in base if not unicodedata.combining(c)]
    k = next((i for i, c in enumerate(letters) if c in VOWELS), 0)
    return [k] if 0 < k < len(form) else None


def _ling(attrs, lemma=None, pos=None, msd=None, resp=None):
    """Linguistic attributes; @lemma always, @pos @msd @resp only if asked for in `attrs`."""
    values = (('lemma', lemma), ('pos', pos), ('msd', msd),
              ('resp', ' '.join('#' + r for r in resp.split()) if resp else None))
    return ''.join(f' {k}={quoteattr(v)}' for k, v in values if v and (k == 'lemma' or k in attrs))


def _word(w, attrs):
    if w.lang != 'grc':
        return f'<w xml:lang="{w.lang}">{_pieces(w.pieces)}</w>'
    if w.crasis:
        cuts = crasis_cuts(w.form, [s for s, *_ in w.crasis])
        if cuts is not None:
            parts, rest, done = [], w.pieces, 0
            for cut in cuts:
                head, rest = split_pieces(rest, cut - done)
                parts.append(head)
                done = cut
            parts.append(rest)
            features = w.crasis_msd or [None] * len(w.crasis)
            inner = ''.join(f'<w{_ling(attrs, lem, pos, msd, resp)}>{_pieces(p)}</w>'
                            for p, (_, lem, pos, resp), msd in zip(parts, w.crasis, features))
            return f'<seg type="crasis">{inner}</seg>'
        w = Word(w.pieces, lemma='+'.join(lem or '?' for _, lem, *_ in w.crasis))
    return f'<w{_ling(attrs, w.lemma, w.pos, w.msd, w.resp)}>{_pieces(w.pieces)}</w>'


def _token(t, attrs):
    if isinstance(t, Word):
        return _word(t, attrs)
    if t.kind == 'pc':
        pre = ' pre="true"' if t.value in OPENERS else ''
        return f'<pc{pre}>{escape(t.value)}</pc>'
    if t.kind == 'ed':
        return f'<c type="editorial">{escape(t.value)}</c>'
    if t.kind == 'gap':
        return f'<gap quantity="{t.value}" unit="chars"/>'
    if t.kind == 'num':
        return f'<num>{_pieces(t.pieces)}</num>'
    if t.kind == 'title-open':
        return '<seg type="title">'
    if t.kind == 'title-close':
        return '</seg>'
    raise ValueError(t.kind)


def body(tokens, attrs=frozenset()):
    out, open_titles, after_ms = [], 0, False
    for t in tokens:
        if isinstance(t, Token) and t.kind == 'ms':
            out.append('\n' + _milestones(t.value))
            after_ms = True
            continue
        if isinstance(t, Token) and t.kind == 'title-close':
            if not open_titles:          # unbalanced: drop the close
                continue
            open_titles -= 1
        if isinstance(t, Token) and t.kind == 'title-open':
            open_titles += 1
        if t.space and not after_ms and out:
            out.append(' ')
        out.append(_token(t, attrs))
        after_ms = False
    out.append('</seg>' * open_titles)
    return ''.join(out)


def document(tokens, title, source_name, description, normalization, attrs=frozenset(), sources=()):
    """`attrs`: which of pos, msd, resp to write besides lemma.
    `sources`: [(xml:id, what it is responsible for, name)], the targets of @resp."""
    today = datetime.date.today().isoformat()
    desc = escape(description)
    resp_stmts = ''.join(f'\n<respStmt xml:id="{i}"><resp>{escape(r)}</resp><name>{escape(n)}</name></respStmt>'
                         for i, r, n in sources) if 'resp' in attrs else ''
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{TEI_NS}" xml:lang="grc">
<teiHeader>
<fileDesc>
<titleStmt><title>{escape(title)}</title>{resp_stmts}</titleStmt>
<publicationStmt><p>Generated by {APP_NAME} {__version__} on {today}.</p></publicationStmt>
<sourceDesc><p>Converted from {escape(source_name)}.</p></sourceDesc>
</fileDesc>
<encodingDesc>
<appInfo><application ident="{APP_NAME}" version="{__version__}" when="{today}"><label>{APP_NAME}</label><p>{desc}</p></application></appInfo>
<editorialDecl><normalization><p>{escape(normalization)}</p></normalization></editorialDecl>
</encodingDesc>
</teiHeader>
<text><body><ab>{body(tokens, attrs)}
</ab></body></text>
</TEI>
'''
