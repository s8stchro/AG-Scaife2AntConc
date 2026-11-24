"""The Morpheus analyses shipped with Diogenes (dependencies/data/greek-analyses.txt):
every known form with all its possible lemmas, without context.

Order of decision for each word:
  1. Diogenes knows one lemma for the form            -> that lemma          (resp: diogenes)
  2. several, and spaCy's lemma is one of them         -> spaCy's choice      (diogenes spacy)
  3. several, spaCy's is not among them                -> with Morpho (if asked for): the candidate that is
                                                          Morpho's only lemma (diogenes morpho; not when
                                                          Morpho has several: ὑμῶν ὑμός/ὑμεῖς, Diogenes σύ/ὑμός);
                                                          else the candidate found most often
                                                          unambiguously in the same text (diogenes frequency)
  4. form unknown to Diogenes, or no decision above    -> with Morpho: its lemma, if it has one (morpho);
                                                          spaCy's among its lemmas (morpho spacy); the one
                                                          most often unambiguous in the text (morpho frequency)
  5. no decision above                                 -> spaCy's lemma       (spacy),
                                                          with a final ς added or dropped if that
                                                          makes it a Diogenes headword (Εὐριπίδη -> Εὐριπίδης)
Morpho (morpho.py, Logeion) is asked only for the words that step 3 decides by frequency or that
reach step 5, so a run without it gives the same lemmas as before.
A form not found is looked up again without a second accent from an enclitic (ἐφύλαττόν τε).
An elided form that steps 1-3 cannot decide is decided again with the lemmas of the full forms
it may stand for (ἀγάθʼ: Morpheus has ἀγαστός, ἀγατός; ἀγαθά, ἀγαθέ, … give ἀγαθός). The second word of a crasis that the
crasis lexicon does not know is looked up with its own initial vowel (τοὔπος -> ἔπος).
On Lys. 1 against AGDT: spaCy alone 87.1%, this order 98.2%.
"""
import os
import pickle
import re
import unicodedata
import warnings
from collections import Counter

# beta-code 1.1 uses a non-raw regex string containing \s. Python 3.12 warns
# while compiling it, although its regex and conversion still work correctly.
# Limit the compatibility filter to this exact warning during this import.
with warnings.catch_warnings():
    warnings.filterwarnings('ignore', message=r"invalid escape sequence '\\s'", category=SyntaxWarning)
    from beta_code import beta_code_to_greek

from .normalize import APOS_LIKE

DATA = os.path.join('dependencies', 'data', 'greek-analyses.txt')
INSTALLS = [r'C:\Program Files (x86)\Diogenes', r'C:\Program Files\Diogenes',
            os.path.expanduser('~/Diogenes'), '/usr/local/diogenes', '/opt/diogenes']
CACHE_VERSION = 2


def key(form):
    """Lookup key: lower case, grave as acute, ASCII apostrophe."""
    d = unicodedata.normalize('NFD', form.lower()).replace('\u0300', '\u0301')
    s = unicodedata.normalize('NFC', d)
    return ''.join("'" if c in APOS_LIKE else c for c in s)


ACCENTS = '̀́͂'


def without_enclitic_accent(form):
    """ἐφύλαττόν (τε) -> ἐφύλαττον: drop the second accent that an enclitic puts on
    the last syllable; None if the form does not have two accents."""
    d = unicodedata.normalize('NFD', form)
    marks = [i for i, c in enumerate(d) if c in ACCENTS]
    if len(marks) < 2:
        return None
    return unicodedata.normalize('NFC', d[:marks[-1]] + d[marks[-1] + 1:])


def lookup(table, form):
    """The Diogenes candidates for a form, or () if it is unknown."""
    if '…' in form:
        return ()
    found = table.get(key(form))
    if found is None:
        plain = without_enclitic_accent(form)
        found = table.get(key(plain)) if plain else None
    return found or ()


def _union(table, forms):
    """The lemmas of all the forms that Diogenes knows, or None."""
    return tuple(sorted({x for f in forms for x in table.get(key(f), ())})) or None


ELIDED = ('α', 'ε', 'ι', 'ο', 'αι', 'οι')     # the vowels lost in elision (comedy: οἴομʼ, μʼ = μοι)
DEASPIRATE_CLUSTERS = (('χθ', 'κτ'), ('φθ', 'πτ'))
DEASPIRATE = {'θ': 'τ', 'φ': 'π', 'χ': 'κ'}


def unelided(form):
    """The full forms an elided form may stand for: ἐσθῆτʼ -> ἐσθῆτα, ἐσθῆτε, …; an acute on the
    word may also come from the lost vowel of an oxytone: ἀγάθʼ -> ἀγάθα, …, ἀγαθά, ἀγαθέ, …;
    trailing aspirates before rough breathing (Smyth §75) also try smooth stops: ἐγένεθʼ -> ἐγένετο."""
    base = form.rstrip(APOS_LIKE)
    if base == form or not base:
        return []
    bases = [base]
    matched_cluster = False
    for asp, plain in DEASPIRATE_CLUSTERS:
        if base.endswith(asp):
            bases.append(base[:-len(asp)] + plain)
            matched_cluster = True
            break
    if not matched_cluster and base[-1:] in DEASPIRATE:
        bases.append(base[:-1] + DEASPIRATE[base[-1]])
    out = []
    for b in bases:
        out.extend(b + v for v in ELIDED)
        d = unicodedata.normalize('NFD', b)
        marks = [i for i, c in enumerate(d) if c in ACCENTS]
        if marks and d[marks[-1]] != CIRCUMFLEX:
            plain = d[:marks[-1]] + d[marks[-1] + 1:]
            out.extend(unicodedata.normalize('NFC', plain + v + ACUTE) for v in ELIDED)
    return list(dict.fromkeys(out))


# the vowel at the start of the second word of a crasis -> the vowels it may stand for
# (κἀγώ: ε, τἀγαθά: α, τἠρυθράν: ε, τἠληθείᾳ: α, τὠφθαλμώ: ο, τὠνδρός: α, τοὔπος: ε, θοὔδωρ: υ, θοἰμάτιον: ἱ)
CONTRACTED = {
    'α': ('α', 'ε'),
    'η': ('η', 'ε', 'α'),
    'ω': ('ω', 'ο', 'α'),
    'ου': ('ου', 'ο', 'ε', 'υ'),
    'οι': ('οι', 'ι'),
}


def uncontracted(first, piece):
    """The second word of a crasis with its own initial vowel and breathing: rough if the first word
    ends in an aspirate (θοἰμάτιον, χαὖται). τ|οὔπος -> ἔπος, κ|ἀντεῦθεν -> ἐντεῦθεν, τ|ὠφθαλμώ ->
    ὀφθαλμώ, θ|οἰμάτιον -> ἱμάτιον, χ|αὖται -> αὗται, κ|ᾆπεν -> εἶπεν."""
    letters = []
    for ch in unicodedata.normalize('NFD', piece.lower()):
        if unicodedata.combining(ch) and letters:
            letters[-1] += ch
        else:
            letters.append(ch)
    if len(letters) < 2 or letters[0][0] not in VOWELS:
        return []
    n = 2 if letters[0][0] in 'αεηου' and letters[1][0] in 'ιυ' and DIAERESIS not in letters[1] else 1
    initial = ''.join(c[0] for c in letters[:n])
    has_iota_sub = '\u0345' in letters[0]
    breathing = ROUGH if (first and first[-1:].lower() in 'θχφ') or initial == 'υ' else SMOOTH
    tones = (ACUTE, CIRCUMFLEX) if any(c in ACCENTS for c in ''.join(letters[:n])) else ('',)
    rest = ''.join(letters[n:])
    vowels = list(CONTRACTED.get(initial, (initial,)))
    if has_iota_sub:
        for extra in (('οι', 'ει') if initial == 'ω' else ('ει', 'αι')):
            if extra not in vowels:
                vowels.append(extra)
    return list(dict.fromkeys(
        unicodedata.normalize('NFC', v + breathing + tone + rest)
        for v in vowels for tone in tones
    ))


def bare(s):
    """For comparing lemmas of different origin: no diacritics, no homograph number."""
    d = unicodedata.normalize('NFD', re.sub(r'\d+$', '', s or '').lower())
    return ''.join(c for c in d if not unicodedata.combining(c))


def greek(beta):
    g = unicodedata.normalize('NFC', beta_code_to_greek(beta))
    return re.sub('σ(?=$|\')', 'ς', g)


VOWELS = 'αεηιουω'
ROUGH, SMOOTH, ACUTE = '̔', '̓', '́'
CIRCUMFLEX, DIAERESIS = ACCENTS[2], chr(0x308)
ASPIRATE = {'τ': 'θ', 'π': 'φ', 'κ': 'χ'}


# Simplex roots whose initial letters happen to resemble a preverb (not already compounded)
_SIMPLEX_PREVERB_LOOKALIKES = frozenset((
    ('εν', 'εννυμι'), ('υπο', 'υφαινω'), ('κατα', 'καθαιρω'), ('κατα', 'καθαρος'),
    ('μετα', 'μεθυω'), ('μετα', 'μεθυ'), ('δια', 'διαινω'), ('ανα', 'ανωγα'),
))


def _assimilate(prep, first, second=''):
    """σύν, ἐν before a consonant: συλλέγω, συμβαίνω, ἐγκαλέω, συστῆναι, συσσιτέω, ἐνρίπτω."""
    if prep[-1] != 'ν':
        return prep
    bp = bare(prep)
    if first == 'ρ' and bp == 'εν':
        return prep
    if first in 'λρμ':
        return prep[:-1] + first
    if first in 'πβφψ':
        return prep[:-1] + 'μ'
    if first in 'κγχξ':
        return prep[:-1] + 'γ'
    if first == 'σ' and bp == 'συν':
        return prep[:-1] + ('σ' if second in VOWELS else '')
    if first == 'ζ' and bp == 'συν':
        return prep[:-1]
    return prep


def compound(lemma):
    """Morpheus writes compound verbs as preposition-verb (κατά-λέγω, περί-εἶδον);
    join them into one headword (καταλέγω, περιεῖδον, συλλέγω, ἔνειμι, καθεύδω)."""
    if '-' not in lemma:
        return lemma
    *preps, word = lemma.split('-')
    for prep in reversed(preps):
        word = _join(prep, word)
    return word


def _join(prep, word):
    p = ''.join(c for c in unicodedata.normalize('NFD', prep) if c not in ACCENTS)
    w = unicodedata.normalize('NFD', word)
    bp, bw = bare(p), bare(w)
    rough = ROUGH in w[:3]
    elided = bp[:-1] if bp[-1] in VOWELS else bp
    # the verb is already compounded (κατά-καθεύδω, διά-διαιτάω, ἀμφί-ἀμφισβητέω),
    # except documented simplex roots whose stems happen to share the preverb letters.
    if (bp, bw) not in _SIMPLEX_PREVERB_LOOKALIKES and (
            bw.startswith(bp) or (elided[-1:] in ASPIRATE
                                  and bw.startswith(elided[:-1] + ASPIRATE[elided[-1]]))):
        return word
    w = w.replace(ROUGH, '').replace(SMOOTH, '')
    if bw[0] in VOWELS:
        if bp[-1] in VOWELS and bp not in ('περι', 'προ'):
            p = p[:-1]  # elision of the final vowel: κατ-, ἀπ-, δι-
            if rough and p[-1] in ASPIRATE:
                p = p[:-1] + ASPIRATE[p[-1]]
        if bp == 'εκ':
            p = p[:-1] + 'ξ'
    elif bw[0] == 'ρ' and bp[-1] in VOWELS:  # διά-ῥώννυμι: διαρρώννυμι
        w = 'ρ' + w
    else:
        p = _assimilate(p, bw[0], bw[1:2])
    if bw in ('ειμι', 'κειμαι', 'ημαι', 'οιδα', 'φημι'):  # recessive accent: ἔνειμι, παράκειμαι, κάθημαι, σύνοιδα, σύμφημι
        w = ''.join(c for c in w if c not in ACCENTS)
        last = max(i for i, c in enumerate(p) if c in VOWELS)
        end = last + 1
        while end < len(p) and unicodedata.combining(p[end]):
            end += 1
        p = p[:end] + ACUTE + p[end:]
    return unicodedata.normalize('NFC', p + w)


def find(path=None):
    """greek-analyses.txt, from a given file or Diogenes folder, or a usual install location."""
    for p in ([path] if path else INSTALLS):
        if os.path.isfile(p):
            return p
        if os.path.isfile(os.path.join(p, DATA)):
            return os.path.join(p, DATA)
    return None


def version(analyses_path):
    """Diogenes' own version, from server/Diogenes/Base.pm (the file `version` is Electron's)."""
    base = os.path.join(os.path.dirname(analyses_path), '..', '..', 'server', 'Diogenes', 'Base.pm')
    try:
        with open(base, encoding='utf-8', errors='replace') as fh:
            m = re.search(r'\$Diogenes::Base::Version\s*=\s*"([^"]+)"', fh.read())
        return m.group(1) if m else ''
    except OSError:
        return ''


def parse(path):
    """{form key: (lemma, ...)} from Diogenes' file of Morpheus analyses."""
    table = {}
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            beta, _, analyses = line.rstrip('\n').partition('\t')
            if beta.startswith(('!', '*!')):        # a few hundred variant entries; skipped
                continue
            lemmas = {compound(greek(re.sub(r'\d+$', '', a.split(',')[-1])))
                      for a in re.findall(r'\{\d+ \d+ ([^\t]*)\t', analyses)}
            k = key(greek(beta))
            table[k] = tuple(sorted(lemmas | set(table.get(k, ()))))
    return table


def load(path, cache_dir=None):
    """Parse once (about a minute), then read the cached table (a few seconds)."""
    st = os.stat(path)
    cache_dir = cache_dir or os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/.cache'),
                                          'AG-Diogenes-SpaCy-txt2xml')
    cache = os.path.join(cache_dir, f'greek-lemmas-v{CACHE_VERSION}-{st.st_size}-{int(st.st_mtime)}.pickle')
    if os.path.exists(cache):
        with open(cache, 'rb') as fh:
            return pickle.load(fh)
    table = parse(path)
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache + '.tmp', 'wb') as fh:
        pickle.dump(table, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(cache + '.tmp', cache)
    return table


def by_case(form, candidates):
    """A word written in lower case is not a proper name: νόμον is νόμος, not Νόμος
    (Morpheus also has the personifications); keep all if none is in lower case. A word written
    with a capital is the name where the same headword exists in both cases: Φίλιππον is Φίλιππος,
    not φίλιππος 'fond of horses' (Στέφανος, Ἄφοβος, Πόντος likewise); in Perseus a capital marks
    a name, not the start of a sentence (a word all in capitals, a heading, is not a name)."""
    if form[:1].islower():
        return tuple(x for x in candidates if not x[:1].isupper()) or candidates
    if form[:1].isupper() and any(ch.islower() for ch in form[1:]):
        names = {bare(x) for x in candidates if x[:1].isupper()}
        return tuple(x for x in candidates if x[:1].isupper() or bare(x) not in names)
    return candidates


def headwords(table):
    """All the lemmas in the table."""
    return {x for lemmas in table.values() for x in lemmas}


def headword(lemma, heads):
    """spaCy's lemma, or the Diogenes headword it misses by a final ς: Εὐριπίδη -> Εὐριπίδης,
    Δικαιόπολι -> Δικαιόπολις, Διονύσιας -> Διονύσια."""
    if not lemma or lemma in heads:
        return lemma
    other = lemma[:-1] if lemma.endswith('ς') else lemma + 'ς'
    return other if other in heads else lemma


def decide(lemma, c, unambiguous, heads, m=()):
    """(lemma, resp) from spaCy's lemma, the Diogenes candidates c and Morpho's lemmas m (steps above)."""
    same = sorted((x for x in c if bare(x) == bare(lemma)), key=lambda x: (x != lemma, x[:1].isupper()))
    if len(c) == 1:
        return c[0], 'diogenes'
    if same:
        return same[0], 'diogenes spacy'
    picked = [x for x in c if len(m) == 1 and bare(x) == bare(m[0])]
    if picked:
        return picked[0], 'diogenes morpho'
    if c and max(unambiguous[x] for x in c):
        return max(c, key=lambda x: (unambiguous[x], not x[:1].isupper())), 'diogenes frequency'
    if len(m) == 1:
        return m[0], 'morpho'
    same = [x for x in m if bare(x) == bare(lemma)]
    if same:
        return same[0], 'morpho spacy'
    if m and max(unambiguous[x] for x in m):
        return max(m, key=lambda x: unambiguous[x]), 'morpho frequency'
    lemma = headword(lemma, heads)
    return lemma, 'spacy' if lemma else None


UNDECIDED = ('diogenes frequency', 'spacy', None)       # where Morpho is asked


def second_word(table, first, piece):
    """Candidates for the second word of a crasis: as written, else with its own initial vowel."""
    return by_case(piece, lookup(table, piece) or _union(table, uncontracted(first, piece)) or ())


def choose(words, table, heads=None, morpho=None):
    """Set lemma and resp of each word (spaCy's lemma, already in w.lemma, helps to choose), and of
    the second word of a crasis whose lemma is not from the crasis lexicon.
    `heads`: headwords(table), if already made. `morpho`: a morpho.Morpho, asked for the words
    that Diogenes cannot decide (UNDECIDED)."""
    heads = headwords(table) if heads is None else heads
    plain = [w for w in words if not w.crasis]
    crases = [w for w in words if w.crasis and w.crasis[-1][3] != 'lexicon']
    candidates = [by_case(w.form, lookup(table, w.form)) for w in plain]
    seconds = [second_word(table, w.crasis[0][0], w.crasis[-1][0]) for w in crases]
    unambiguous = Counter(c[0] for c in candidates + seconds if len(c) == 1)
    decided = []
    for w, c in zip(plain, candidates):
        lemma, resp = decide(w.lemma, c, unambiguous, heads)
        if resp in ('spacy', None) and w.form[-1:] in APOS_LIKE:
            c = by_case(w.form, _union(table, unelided(w.form)) or ())
            lemma, resp = decide(w.lemma, c, unambiguous, heads)
        decided.append((w, w.lemma, c, lemma, resp))
    for w, c in zip(crases, seconds):
        decided.append((w, w.crasis[-1][1], c) + decide(w.crasis[-1][1], c, unambiguous, heads))
    if morpho is not None:
        morpho.ask([w.form for w, *_, resp in decided if resp in UNDECIDED])
    stats = Counter()
    for w, spacy, c, lemma, resp in decided:
        if morpho is not None and resp in UNDECIDED:
            firsts = {bare(p[1]) for p in (w.crasis or [])[:-1]}        # Morpho gives a crasis the second lemma
            m = tuple(x for x in by_case(w.form, morpho.lemmas(w.form)) if bare(x) not in firsts)
            if m:
                lemma, resp = decide(spacy, c, unambiguous, heads, m)
        if w.crasis:
            piece, _, pos, _ = w.crasis[-1]
            w.crasis = w.crasis[:-1] + [(piece, lemma, pos, resp)]
            stats[f'crasis {resp or "no lemma"}'] += 1
            continue
        w.lemma, w.resp = lemma, resp
        stats[w.resp or 'no lemma'] += 1
        if w.resp == 'spacy' and w.lemma not in heads:
            stats['spacy unknown'] += 1
    return stats
