"""Optional accent normalisation, so that AntConc counts one word form where the edition
prints two: the grave written as acute (καὶ -> καί), and the second accent that an enclitic
puts on the last syllable left out (ὄρνιθός τινος -> ὄρνιθος, δῶρόν τι -> δῶρον).

An enclitic adds an acute on the last syllable only of a word accented on the antepenult
(acute) or on the penult (circumflex). Other words with two accents are crasis (ὦγαθοί,
ὦκβάτανα, μέντἂν) and keep both, as does an elided word (δήξομἄρʼ = δήξομαι ἄρα).
"""
import re
import unicodedata
from collections import Counter

from ag_diogenes_spacy_txt2xml.crasis import acute
from ag_diogenes_spacy_txt2xml.normalize import APOS_LIKE

ACUTE, GRAVE, CIRCUMFLEX, DIAERESIS = '\u0301', '\u0300', '\u0342', '\u0308'
VOWELS = set('αεηιουω')
DIPHTHONGS = {'αι', 'ει', 'οι', 'υι', 'αυ', 'ευ', 'ηυ', 'ου', 'ωυ'}
WORD = re.compile(r'[\u0370-\u0373\u0376\u0377\u037a-\u037d\u037f\u0386\u0388-\u038a\u038c\u038e-\u03a1\u03a3-\u03ff\u1f00-\u1fff\u0300-\u036f]+')


def letters(word):
    """[(letter, [marks])] of the decomposed word."""
    out = []
    for c in unicodedata.normalize('NFD', word):
        if unicodedata.combining(c) and out:
            out[-1][1].append(c)
        else:
            out.append((c, []))
    return out


def syllables(ls):
    """The syllable (vowel or diphthong, counted from 0) of each letter, None for a consonant;
    and the number of syllables. ἀι, αϊ are two syllables, αἰ one."""
    out, n = [], 0
    for i, (c, marks) in enumerate(ls):
        if c.lower() not in VOWELS:
            out.append(None)
            continue
        prev = ls[i - 1] if i else None
        if (prev and out[-1] is not None and not prev[1] and DIAERESIS not in marks
                and prev[0].lower() + c.lower() in DIPHTHONGS and (i < 2 or out[-2] != out[-1])):
            out.append(out[-1])
        else:
            out.append(n)
            n += 1
    return out, n


def without_enclitic_accent(word):
    """ὄρνιθός -> ὄρνιθος; any other word unchanged."""
    ls = letters(word)
    accents = [(i, m) for i, (_, marks) in enumerate(ls) for m in marks if m in (ACUTE, GRAVE, CIRCUMFLEX)]
    if len(accents) != 2:
        return word
    syl, n = syllables(ls)
    (i1, first), (i2, second) = accents
    if second != ACUTE or syl[i2] != n - 1:
        return word
    if not (first == ACUTE and syl[i1] == n - 3 or first == CIRCUMFLEX and syl[i1] == n - 2):
        return word
    ls[i2][1].remove(ACUTE)
    return unicodedata.normalize('NFC', ''.join(c + ''.join(marks) for c, marks in ls))


def normalize(word, elided=False):
    # Smyth §154: τὶς/τὶ explicitly distinguish the indefinite from interrogative
    # τίς/τί. Normalise them to the unaccented indefinite, never to the interrogative.
    if unicodedata.normalize('NFC', word.lower()) in ('τὶ', 'τὶς'):
        return unicodedata.normalize('NFC', unicodedata.normalize('NFD', word).replace(GRAVE, ''))
    return acute(word if elided else without_enclitic_accent(word))


def normalize_lines(lines):
    """Normalise the accents of every Greek word in the lines; {(printed, normalised): count}."""
    changed = Counter()

    def one(m):
        word, after = m.group(), m.string[m.end():m.end() + 1]
        new = normalize(word, elided=bool(after) and after in APOS_LIKE)
        if new != word:
            changed[word, new] += 1
        return new

    for line in lines:
        line.text = WORD.sub(one, line.text)
    return changed


def summary(changed, limit=8):
    """(words with a grave, words with an enclitic accent, examples) for the report and the window."""
    grave = sum(n for (old, _), n in changed.items() if GRAVE in unicodedata.normalize('NFD', old))
    enclitic = sum(n for (old, new), n in changed.items() if acute(old) != new)
    enc =[f'{o} → {n}' for (o, n), _ in changed.most_common() if acute(o) != n][:limit // 2]
    gr = [f'{o} → {n}' for (o, n), _ in changed.most_common() if acute(o) == n][:limit - len(enc)]
    return grave, enclitic, enc + gr
