"""Step 1: unify the Unicode of Greek text, line by line.

- precomposed characters (NFC), with combining marks put in canonical Greek order first
- spacing breathings/accents written before a capital are moved onto the letter
- one ano teleia, one elision apostrophe, Greek quotation marks « »
"""
import re
import unicodedata

APOSTROPHE = '’'          # RIGHT SINGLE QUOTATION MARK, Unicode's recommended apostrophe
ANO_TELEIA = '·'          # GREEK ANO TELEIA (NFC would turn it into U+00B7)

# characters used for elision in the wild (not U+2032 / U+02B9: those mark numerals)
APOS_LIKE = "'´‘’ʼ᾽᾿"
# characters that may stand for the ano teleia (after NFC U+0387 is U+00B7)
TELEIA_LIKE = '··‧∙⋅'
# Latin and typographic look-alikes inside Greek text
LOOKALIKES = str.maketrans({
    'µ': 'μ', 'ϐ': 'β', 'ϲ': 'σ', 'Ϲ': 'Σ',
    'ϑ': 'θ', 'ϕ': 'φ', 'ϖ': 'π', 'ϰ': 'κ', 'ϱ': 'ρ', 'ϵ': 'ε', 'ϒ': 'Υ',
})
_FINAL_LUNATE_SIGMA = re.compile(r'ϲ(?![Ͱ-Ͽἀ-῿\u0300-\u036f' + re.escape(APOS_LIKE) + '])')   # \u03f2\u2019 is elided \u03c3\u03ad, not \u03c2
_CIRCUMFLEX_LOOKALIKES = {'\u0303', '\u0302', '\u0311'}
INVISIBLE = dict.fromkeys(map(ord, '﻿​‌‍⁠'))
SPACES = re.compile('[  -   　\t]+')

# spacing diacritics placed before a capital (old fonts): mark -> combining sequence
SPACING_MARKS = {
    '᾿': '̓', '῾': '̔', '΄': '́', '´': '́',
    '`': '̀', '῀': '͂', '῎': '̓́', '῍': '̓̀',
    '῏': '̓͂', '῞': '̔́', '῝': '̔̀',
    '῟': '̔͂',
}
_SPACING_BEFORE_CAPITAL = re.compile(
    '(?:^|(?<=[\\s(\\[〈«„“"]))(['+ ''.join(SPACING_MARKS) + ']+)([ΑΕΗΙΟΥΩΡ])')

# order of Greek marks above the letter: length, diaeresis, breathing, accent
_MARK_ORDER = {c: i for i, c in enumerate('̄̆̈̓̔́̀͂')}


def is_greek_letter(ch):
    return ('Ͱ' <= ch <= 'Ͽ' or 'ἀ' <= ch <= '῿') and unicodedata.category(ch).startswith('L')


def _reorder_marks(text):
    """NFD, then sort each run of above-marks into the order Greek precomposed letters expect."""
    out, run = [], []
    in_greek = False
    for ch in unicodedata.normalize('NFD', text):
        if in_greek and ch in _CIRCUMFLEX_LOOKALIKES:
            ch = '\u0342'
        if ch in _MARK_ORDER:
            run.append(ch)
            continue
        out.extend(sorted(run, key=_MARK_ORDER.get))
        run = []
        if not unicodedata.combining(ch):
            in_greek = is_greek_letter(ch)
        out.append(ch)
    out.extend(sorted(run, key=_MARK_ORDER.get))
    return ''.join(out)


def _is_word_end_context(text, i):
    """True if the apostrophe-like char at i closes an elided Greek word."""
    j = i - 1
    while j >= 0 and text[j] in ']〉⟩':   # πα[ρ]´
        j -= 1
    return j >= 0 and (is_greek_letter(text[j]) or unicodedata.combining(text[j]) > 0)


def _fix_apostrophes(text, apostrophe):
    out = []
    for i, ch in enumerate(text):
        # A combining smooth breathing on a consonant is also used as an
        # elision sign. On a vowel/rho it remains a real breathing/coronis.
        previous_index = i - 1
        while previous_index >= 0 and unicodedata.combining(text[previous_index]):
            previous_index -= 1
        previous = text[previous_index].lower() if previous_index >= 0 else ''
        before_prev = previous_index - 1
        while before_prev >= 0 and unicodedata.combining(text[before_prev]):
            before_prev -= 1
        combining_elision = (ch == '\u0313' and previous_index >= 0 and is_greek_letter(text[previous_index])
                             and (previous not in 'αεηιουωρ' or
                                  (previous == 'ρ' and before_prev >= 0 and is_greek_letter(text[before_prev]) and
                                   (i + 1 == len(text) or not is_greek_letter(text[i + 1]) or
                                    text[i + 1].lower() in 'αεηιουω'))))
        after_word = _is_word_end_context(text, i)
        before_word = i + 1 < len(text) and is_greek_letter(text[i + 1])
        if (ch in APOS_LIKE or combining_elision) and after_word:
            out.append(apostrophe)
            # an elided word glued to the next one: δ’αὐτόν -> δ’ αὐτόν
            if before_word:
                out.append(' ')
        elif before_word and (ch in APOS_LIKE or (ch == '\u0313' and not after_word
                                                  and text[i + 1].lower() not in 'αεηιουωρ')):
            out.append(apostrophe)  # prodelision: ʼγώ, 'στιν, ᾽μέ
        else:
            out.append(ch)
    return ''.join(out)


class Normalizer:
    """Stateful because a quotation may open on one line and close on another."""

    def __init__(self, apostrophe=APOSTROPHE, ano_teleia=ANO_TELEIA):
        self.apostrophe = apostrophe
        self.ano_teleia = ano_teleia
        self.quote_depth = 0

    def _quotes(self, text):
        out = []
        for i, ch in enumerate(text):
            if ch in '«„‟':
                opening = True
            elif ch in '»”':
                opening = False
            elif ch == '“':
                opening = self.quote_depth == 0
            elif ch == '"':
                opening = i == 0 or text[i - 1].isspace() or text[i - 1] in '([〈'
            else:
                out.append(ch)
                continue
            if opening:
                self.quote_depth += 1
                out.append('«')
            else:
                self.quote_depth = max(0, self.quote_depth - 1)
                out.append('»')
        return ''.join(out)

    def __call__(self, text):
        text = text.translate(INVISIBLE)
        text = SPACES.sub(' ', text)
        # An explicitly paired typographic quotation is not two elision signs.
        # Do not treat a Greek-word-final ‘ (elision) as an opening quotation mark.
        text = re.sub(r'(?<![Ͱ-Ͽἀ-῿])‘([^‘’\n]+)’(?=$|[\s.,;:!?··;)\]»])', r'«\1»', text)
        text = _SPACING_BEFORE_CAPITAL.sub(lambda m: m.group(2) + ''.join(SPACING_MARKS[c] for c in m.group(1)), text)
        text = _FINAL_LUNATE_SIGMA.sub('ς', text).translate(LOOKALIKES)
        # Interpret consonantal elision marks before NFC can compose ρ + psili as ῤ.
        text = unicodedata.normalize('NFC', _fix_apostrophes(_reorder_marks(text), self.apostrophe))
        text = re.sub('[' + TELEIA_LIKE + ']', self.ano_teleia, text)
        text = self._quotes(text)
        return re.sub(' {2,}', ' ', text).strip()
