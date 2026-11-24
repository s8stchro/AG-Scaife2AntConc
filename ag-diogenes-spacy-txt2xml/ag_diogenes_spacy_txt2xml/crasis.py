"""Crasis: spaCy pipelines treat κἀγώ as one word (or cut it as κἀ + γώ), so we find it
ourselves, from a lexicon (crasis.tsv) and from internal vowel breathings.
The legacy match() fallback covers κ/χ/τ/θ; coronis_cuts() supplies productive
candidate boundaries to the full morphological expander for other first words."""
import os
import unicodedata

from .normalize import APOS_LIKE, APOSTROPHE

LEXICON = os.path.join(os.path.dirname(__file__), 'crasis.tsv')
ACCENTS = {'̀', '́', '͂'}
BREATHINGS = {'̓', '̔'}
VOWELS = set('αεηιουω')
FIRST_WORD = {'κ': 'καί', 'χ': 'καί', 'τ': 'ὁ', 'θ': 'ὁ'}


def coronis_cuts(form):
    """Candidate cuts before an internal vowel nucleus bearing either breathing.

    Return offsets into the original string, including NFC/NFD and attached marks.
    An initial diphthong (αὐτός, οἶδα) and consonantal rho breathings are excluded.
    Lexical compounds may also contain an internal breathing; the expander checks
    the proposed independent words against full analyses before splitting.
    """
    letters = []
    for offset, ch in enumerate(form):
        for decomposed in unicodedata.normalize('NFD', ch):
            if unicodedata.combining(decomposed) and letters:
                letters[-1][2].add(decomposed)
            else:
                letters.append([offset, decomposed.lower(), set()])
    cuts = []
    for i, (offset, base, marks) in enumerate(letters):
        if base not in VOWELS or not marks & BREATHINGS:
            continue
        start = i
        if (i and base in 'ιυ' and letters[i - 1][1] in 'αεηου' and '\u0308' not in marks):
            start -= 1
        if start > 0 and letters[start][0] not in cuts:
            cuts.append(letters[start][0])
    return tuple(cuts)


def _strip(s, marks):
    d = unicodedata.normalize('NFD', s.lower())
    return unicodedata.normalize('NFC', ''.join(c for c in d if c not in marks))


def acute(s):
    """Grave written as acute, as in a dictionary headword."""
    return unicodedata.normalize('NFC', unicodedata.normalize('NFD', s).replace('̀', '́'))


def with_accents(s):
    return acute(s.lower().translate(str.maketrans(dict.fromkeys(APOS_LIKE, APOSTROPHE))))


def folded(s):
    return _strip(with_accents(s), ACCENTS)


class Crasis:
    def __init__(self, entries):
        """entries: [(pieces, lemmas)] as in crasis.tsv, e.g. ('κ|ἀκειν*', 'καί+ἐκεῖνος')."""
        self.exact, self.exact_folded, self.prefixes = {}, {}, []
        for pieces, lemmas in entries:
            first, rest = pieces.split('|')
            value = (len(first), lemmas.split('+'))
            if rest.endswith('*'):
                self.prefixes.append((folded(first + rest[:-1]), value))
            else:
                self.exact[with_accents(first + rest)] = value
                self.exact_folded.setdefault(folded(first + rest), value)
        self.prefixes.sort(key=lambda p: -len(p[0]))

    @classmethod
    def load(cls, path=LEXICON):
        entries = []
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if line.strip() and not line.startswith('#'):
                    pieces, lemmas = line.rstrip('\n').split('\t')[:2]
                    entries.append((pieces.strip(), lemmas.strip()))
        return cls(entries)

    def match(self, form):
        """(cut, [lemma, lemma or None]) if `form` is a crasis, else None.

        A lemma is None when the crasis was found by the coronis rule only.
        """
        if form in ('', '…') or '…' in form:
            return None
        hit = self.exact.get(with_accents(form)) or self.exact_folded.get(folded(form))
        if hit:
            return hit
        f = folded(form)
        for prefix, value in self.prefixes:
            if f.startswith(prefix):
                return value
        return self._by_coronis(form)

    @staticmethod
    def _by_coronis(form):
        letters = []
        for ch in unicodedata.normalize('NFD', form.lower()):
            if unicodedata.combining(ch) and letters:
                letters[-1][1].add(ch)
            else:
                letters.append((ch, set()))
        if len(letters) < 3 or letters[0][0] not in FIRST_WORD or letters[1][0] not in VOWELS:
            return None
        # breathing on the vowel after the consonant, or on the second vowel of a diphthong (τοὔνομα)
        marked = letters[1][1] & BREATHINGS or (letters[2][0] in 'ιυ' and letters[2][1] & BREATHINGS)
        return (1, [FIRST_WORD[letters[0][0]], None]) if marked else None
