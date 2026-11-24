"""Candidate inflected expansions, kept separate from original spelling and dictionary lemmas."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import unicodedata

from .analyses import Analysis, features
from . import diogenes
from .crasis import coronis_cuts, with_accents

CRASIS_RULE_ID = 'grc.expansion.crasis'
CRASIS_RULE_VERSION = '1.2.0'


@dataclass(frozen=True)
class Atom:
    form: str
    analyses: tuple
    hint: str | None = None


@dataclass(frozen=True)
class Reading:
    pieces: tuple
    atoms: tuple
    kind: str = 'word'
    prior: float = 0.0
    notes: tuple = ()
    shared_context: bool = False


# These are inflected words, not headwords. Conditional and modal κἄν remain alternatives.
EXACT = {
    'κἀγώ': [('καί', 'ἐγώ')], 'κἀμοί': [('καί', 'ἐμοί')],
    'κἀμέ': [('καί', 'ἐμέ')], 'κἀμοῦ': [('καί', 'ἐμοῦ')],
    'κἄν': [('καί', 'ἄν'), ('καί', 'ἐάν')],
    'μοὐστί': [('μοι', 'ἐστί')], 'μοὔστι': [('μοι', 'ἐστί')],
    'σοὐστί': [('σοι', 'ἐστί')], 'σοὐστίν': [('σοι', 'ἐστίν')],
    'ἐγᾦδα': [('ἐγώ', 'οἶδα')],
    'χὠ': [('καί', 'ὁ')], 'χἠ': [('καί', 'ἡ')],
    'χοἰ': [('καί', 'οἱ')], 'χαἰ': [('καί', 'αἱ')],
}
ARTICLE = [
    ('ὁ', 'Case=Nom|Gender=Masc|Number=Sing'), ('ἡ', 'Case=Nom|Gender=Fem|Number=Sing'),
    ('τό', 'Case=Acc,Nom|Gender=Neut|Number=Sing'), ('οἱ', 'Case=Nom|Gender=Masc|Number=Plur'),
    ('αἱ', 'Case=Nom|Gender=Fem|Number=Plur'), ('τά', 'Case=Acc,Nom|Gender=Neut|Number=Plur'),
    ('τοῦ', 'Case=Gen|Gender=Masc,Neut|Number=Sing'), ('τῷ', 'Case=Dat|Gender=Masc,Neut|Number=Sing'),
    ('τῇ', 'Case=Dat|Gender=Fem|Number=Sing'), ('τώ', 'Case=Acc,Nom|Gender=Masc,Neut|Number=Dual'),
]

# LSJ s.v. ταώς records Attic aspiration within this lexical noun (ταὧς).
# Its inflections, e.g. ταὧσι at Ach. 63, must not become article + verb.
LEXICAL_MEDIAL_LEMMAS = frozenset(('ταὧς', 'ταώς', 'ταῶς'))
LEXICAL_MEDIAL_FORMS = frozenset(('ταὧς', 'ταὧσι', 'ταὧσιν'))


def elision_penalty(form, analysis):
    """Constrain restorations by phonology, rather than treating every ending as elidable.

    Smyth §§70–74: -αι occurs in verbal endings; -οι is exceptional (οἴμοι,
    epic μοι/σοι/τοι), not a general noun/adjective/optative ending. Dative -ι
    is retained as a rare poetic possibility, with a visible cost and review note.
    """
    plain = diogenes.bare(form)
    grammar = features(analysis.msd)
    if plain.endswith('οι'):
        if plain == 'οιμοι':
            return 0.0
        return 2.0 if plain in ('μοι', 'σοι', 'τοι') else None
    if plain.endswith('αι'):
        return 0.0 if ('Inf' in grammar.get('VerbForm', ()) or
                       ('Fin' in grammar.get('VerbForm', ()) and
                        grammar.get('Voice', set()) & {'Mid', 'Pass'})) else None
    if plain.endswith('ι') and 'Dat' in grammar.get('Case', ()):
        return 1.5
    return 0.0


def load_elisions():
    table = {}
    for line in Path(__file__).with_name('elision.tsv').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            form, expanded = line.split('\t')[:2]
            table[with_accents(form.strip().rstrip(diogenes.APOS_LIKE))] = expanded.strip()
    return table


def consonants(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s.lower())
                   if c.isalpha() and c not in diogenes.VOWELS)


def compatible(left, right):
    a, b = features(left), features(right)
    return all(a[k] & b[k] for k in a.keys() & b.keys())


def initial_accent(form):
    """Accent of the initial vowel nucleus; grave and acute are equivalent."""
    marks = set()
    for ch in unicodedata.normalize('NFD', form.lower()):
        if ch.isalpha() and ch not in diogenes.VOWELS:
            break
        if ch in diogenes.ACCENTS:
            marks.add(diogenes.ACUTE if ch == '\u0300' else ch)
    return marks


class Expander:
    def __init__(self, store, crasis, elisions):
        self.store, self.crasis, self.elisions = store, crasis, elisions

    def atom(self, form, hint=None):
        analyses = tuple(self.store.lookup(form))
        # A lexicon hint is defeasible evidence when no full analysis is available.
        if hint and not analyses:
            analyses = (Analysis(form, hint, 'expansion:' + hint, source='lexicon'),)
        return Atom(form, analyses, hint)

    def elided_atoms(self, form):
        """Full inputs first; retain the attested elided reading as a defeasible fallback."""
        preferred = self.elisions.get(with_accents(form.rstrip(diogenes.APOS_LIKE)))
        out = []
        if preferred:
            entries = tuple(self.store.lookup(preferred))
            hint = preferred if any(with_accents(a.lemma) == with_accents(preferred) for a in entries) else None
            out.append((self.atom(preferred, hint), 1.0, ()))
        for full in diogenes.unelided(form):
            entries = [(a, elision_penalty(full, a)) for a in self.store.lookup(full)]
            entries = [(a, cost) for a, cost in entries if cost is not None]
            if entries:
                cost = min(cost for _, cost in entries)
                accepted = tuple(a for a, penalty in entries if penalty == cost)
                notes = ('rare poetic elision restoration; verify dialect and context',) if cost else ()
                out.append((Atom(full, accepted), -cost, notes))
        original = self.atom(form.translate(str.maketrans(dict.fromkeys(diogenes.APOS_LIKE, "'"))))
        if original.analyses or not out:
            out.append((original, -0.5 if out else 0.0, ('unexpanded elision; restoration uncertain',)))
        unique = {}
        for atom, prior, notes in out:
            unique.setdefault(atom.form, (atom, prior, notes))
        return list(unique.values())

    def prodelided_readings(self, form):
        stem, out = form[1:], []
        double_elided = bool(stem) and stem[-1:] in diogenes.APOS_LIKE
        # Restore an initial vowel only when the complete form has a local analysis.
        # This covers 'γώ, 'στιν, 'κεχήνη, 'νταῦθα, 'μαυτόν, plus double elisions ('στ', 'φ', 'π', 'μ').
        seen = set()
        for initial in ('ἐ', 'ἔ', 'ἀ', 'ἄ'):
            if initial in ('ἔ', 'ἄ') and any(c in diogenes.ACCENTS for c in unicodedata.normalize('NFD', stem)):
                continue  # do not manufacture a second accent and then use enclitic-accent fallback
            cand = initial + stem
            rare = initial in ('ἀ', 'ἄ')
            if double_elided:
                for atom, prior, notes in self.elided_atoms(cand):
                    if atom.analyses and not any('unexpanded' in n for n in notes) and atom.form not in seen:
                        seen.add(atom.form)
                        extra_notes = (('rare initial-alpha elision; verify dialect and context',) if rare else ()) + notes
                        out.append(Reading((form,), (atom,), 'prodelision',
                                           prior + (-1.5 if rare else 0.0), extra_notes))
            elif self.store.lookup(cand) and cand not in seen:
                seen.add(cand)
                out.append(Reading((form,), (self.atom(cand),), 'prodelision', -1.5 if rare else 0.0,
                                   ('rare initial-alpha elision; verify dialect and context',) if rare else ()))
        if not out:
            atom = self.atom(form.translate(str.maketrans(dict.fromkeys(diogenes.APOS_LIKE, "'"))))
            out.append(Reading((form,), (atom,), 'prodelision', notes=('unresolved initial elision',)))
        return tuple(out[:8])

    def medial_readings(self, form, cuts):
        """Restore independently attested words on both sides of a medial coronis."""
        out, compound = [], False
        whole = self.store.lookup(form)
        for cut in cuts:
            first, second = form[:cut], form[cut:]
            stems = [first]
            if first[-1:] in diogenes.ASPIRATE.values():
                stems += [first[:-1] + plain for plain, aspirate in diogenes.ASPIRATE.items()
                          if aspirate == first[-1:]]
            lefts = {}
            medial_vowels = diogenes.ELIDED + ('ω', 'η', 'ου')
            for stem in stems:
                # These endings are contracted, rather than elided: -αι/-οι and long -ω/-η/-ου
                # are legitimate here (δήξομαι, μέντοι, σοι, ἐγᾦμαι, ποὔστιν, μἠγώ).
                forms = [stem] + diogenes.unelided(stem + '’') + [stem + v for v in ('ω', 'η', 'ου')]
                if not any(ch in diogenes.ACCENTS for ch in unicodedata.normalize('NFD', stem)):
                    forms += [unicodedata.normalize('NFC', stem + vowel + diogenes.ACUTE)
                              for vowel in medial_vowels]
                    forms += [unicodedata.normalize('NFC', stem + vowel + diogenes.CIRCUMFLEX)
                              for vowel in ('ω', 'η', 'ου')]
                for full in forms:
                    if entries := tuple(self.store.lookup(full)):
                        lefts.setdefault(full, Atom(full, entries))
            rights = {}
            for restored in [second] + diogenes.uncontracted(first, second):
                # With an accent already retained in the first word, do not
                # invent an alternative second-word accent (ἄρα -> ἆρα).
                if (any(ch in diogenes.ACCENTS for ch in unicodedata.normalize('NFD', first)) and
                        initial_accent(second) and initial_accent(second) != initial_accent(restored)):
                    continue
                if restored[-1:] in diogenes.APOS_LIKE:
                    for atom, prior, notes in self.elided_atoms(restored):
                        if atom.analyses:
                            rights.setdefault(atom.form, (atom, prior + 0.5 if restored == second else min(0.0, prior), notes))
                elif entries := tuple(self.store.lookup(restored)):
                    rights.setdefault(restored, (Atom(restored, entries), 0.5 if restored == second else 0.0, ()))
            for left in lefts.values():
                for right, prior, notes in rights.values():
                    # Preserve a recognized prefixed verb as one lexical token.
                    # E.g. προὔδωκεν is not independent πρό + ἔδωκεν.
                    lexical_compound = (any(a.pos == 'ADP' for a in left.analyses) and
                        any(a.pos == 'VERB' for a in right.analyses) and
                        any(w.pos == 'VERB' and any(diogenes.bare(w.lemma).startswith(diogenes.bare(a.lemma))
                            for a in left.analyses if a.pos == 'ADP') for w in whole))
                    if lexical_compound:
                        compound = True
                        continue
                    supported_left = any(a.lemma == w.lemma for a in left.analyses for w in whole)
                    out.append(Reading((first, second), (left, right), 'crasis',
                                       prior + (1.0 if supported_left else 0.0), notes, shared_context=True))
        # A directly attested elision preference of the second word supplies
        # useful evidence before applying the bounded candidate limit.
        out.sort(key=lambda reading: (-reading.prior,
                     not any(a.pos in ('PART', 'CCONJ', 'SCONJ', 'ADP') for a in reading.atoms[0].analyses)))
        return (() if compound else tuple(out[:8])), compound

    @lru_cache(maxsize=32768)
    def readings(self, form):
        if form and form[:1] in diogenes.APOS_LIKE:
            return self.prodelided_readings(form)
        hit = self.crasis.match(form)
        if not hit and (vocative := self.vocative_readings(form)):
            return vocative
        if not hit:
            cuts = coronis_cuts(form)
            if cuts:
                if (with_accents(form) in LEXICAL_MEDIAL_FORMS or
                        any(with_accents(a.lemma) in LEXICAL_MEDIAL_LEMMAS for a in self.store.lookup(form))):
                    return (Reading((form,), (self.atom(form),), 'lexical medial breathing'),)
                readings, compound = self.medial_readings(form, cuts)
                if readings:
                    return readings
                if compound:
                    return (Reading((form,), (self.atom(form),), 'lexical compound'),)
            if form[-1:] in diogenes.APOS_LIKE:
                out = []
                for atom, prior, notes in self.elided_atoms(form):
                    if (self.crasis.match(atom.form) or
                            (atom.form[-1:] not in diogenes.APOS_LIKE and coronis_cuts(atom.form))):
                        for reading in self.readings(atom.form):
                            cut = len(reading.pieces[0])
                            out.append(Reading((form[:cut], form[cut:]), reading.atoms, reading.kind,
                                               prior + reading.prior, notes + reading.notes, reading.shared_context))
                    else:
                        kind = 'unresolved crasis' if cuts else 'elision'
                        extra = ('internal coronis detected; no supported independent-word expansion',) if cuts else ()
                        out.append(Reading((form,), (atom,), kind, prior, notes + extra))
                return tuple(out[:8])
            if cuts:
                return (Reading((form,), (self.atom(form),), 'unresolved crasis',
                                notes=('internal coronis detected; no supported independent-word expansion',)),)
            return (Reading((form,), (self.atom(form),)),)
        cut, hints = hit
        return self.crasis_readings(form, cut, hints)

    def vocative_readings(self, form):
        """ὦ fused with a following vocative (ὦναίσχυντε = ὦ ἀναίσχυντε, ὦταῖρε = ὦ ἑταῖρε), for forms
        the crasis table does not list. Only for a written form without an analysis of its own, and
        only restorations that Diogenes analyses as vocative or nominative are kept."""
        if len(form) < 4 or form[:1] not in ('ὦ', 'ὤ') or self.store.lookup(form):
            return ()
        kept = []
        for reading in self.crasis_readings(form, 1, ('ὦ', None)):
            if reading.kind != 'crasis':
                continue
            analyses = [a for a in reading.atoms[-1].analyses if a.source == 'diogenes']
            if any({'Voc', 'Nom'} & set(features(a.msd).get('Case', ())) for a in analyses):
                kept.append(Reading(reading.pieces, reading.atoms, reading.kind, reading.prior,
                                    reading.notes + ('ὦ + vocative restored without a crasis-table entry',),
                                    reading.shared_context))
        return tuple(kept)

    def crasis_readings(self, form, cut, hints):
        pieces = (form[:cut], form[cut:])
        exact = EXACT.get(with_accents(form))
        if exact:
            return tuple(Reading(pieces, tuple(self.atom(f, h) for f, h in zip(pair, hints)), 'crasis')
                         for pair in exact)
        seconds = [pieces[1]] + diogenes.uncontracted(pieces[0], pieces[1])
        if hints[0] == 'ὁ' and not consonants(pieces[0]):
            # In ἁνήρ/ἅνδρες the fused vowel was assigned to the article's
            # written piece. Reconstruct the noun's own vowel and breathing.
            seconds += diogenes.uncontracted('', form)
        if hints[0] == 'ὦ':
            seconds += [v + pieces[1] for v in ('ἀ', 'ἁ', 'ἄ', 'ἅ', 'ἐ', 'ἑ', 'ἔ', 'ὀ', 'ὁ')]
        if hints[1] and any(a.pos in ('PART', 'CCONJ', 'SCONJ', 'ADP', 'ADV', 'INTJ')
                            for a in self.store.lookup(hints[1])):
            seconds.insert(0, hints[1])  # an indeclinable headword is also its inflected form
        # Lookup may find a capitalized/accented restoration; retain its actual inflected surface.
        restored = {}
        for f in dict.fromkeys(seconds):
            if f[-1:] in diogenes.APOS_LIKE:
                for atom, prior, notes in self.elided_atoms(f):
                    if atom.analyses:
                        if f != pieces[1]:
                            # A guessed restoration of the contracted vowel must not
                            # inherit the preference of a different, unattested elision.
                            # κᾆτ’ is not direct evidence for the separate word ἔτ’.
                            prior = min(prior, 0.0)
                        restored.setdefault(atom.form, (atom, prior, notes))
            else:
                for a in self.store.lookup(f):
                    restored.setdefault(a.surface, (self.atom(a.surface, hints[1]), 0.0, ()))
        # Unattested variants are lookup probes. An empty result leaves the
        # written token available for whole-word analysis and review below.
        out = []
        for second, (atom, prior, notes) in restored.items():
            if pieces[1][-1:] in diogenes.APOS_LIKE and second == hints[1]:
                prior += 1.0  # lexical evidence for an elided indeclinable, still defeasible
            if hints[0] == 'ὁ':
                for article, msd in ARTICLE:
                    if consonants(article) != consonants(pieces[0]).replace('θ', 'τ'):
                        continue
                    if atom.analyses and not any(compatible(msd, a.msd) for a in atom.analyses):
                        continue
                    a = Analysis(article, 'ὁ', 'article:' + article, 'DET', msd, source='lexicon')
                    out.append(Reading(pieces, (Atom(article, (a,), 'ὁ'), atom), 'crasis', prior, notes))
            else:
                out.append(Reading(pieces, (self.atom(hints[0], hints[0]), atom), 'crasis', prior, notes))
        # Favor restorations supported by the lemma hint without ruling alternatives out.
        out.sort(key=lambda r: (not any(a.lemma == hints[1] for a in r.atoms[-1].analyses),
                                not any(a.source == 'diogenes' for a in r.atoms[-1].analyses)))
        return tuple(out[:8]) or (Reading((form,), (self.atom(form),), 'unresolved crasis'),)
