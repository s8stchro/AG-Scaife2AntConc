"""Step 2: lemma, part of speech and morphology from a spaCy pipeline (default: OdyCy).

spaCy receives our own tokens, sentence by sentence (Doc(words=...)), so its tokenizer
cannot cut words differently from the TEI. Crasis and frequent elisions are resolved
before spaCy sees them: κἀγὼ goes in as the two words καί ἐγώ, δ’ as δέ.
"""
import os
from collections import Counter

import spacy
from spacy.tokens import Doc

from .crasis import Crasis, acute, with_accents
from .normalize import APOS_LIKE
from .tokenize import APOSTROPHE, Token, Word

MODEL = 'grc_odycy_joint_sm'
SENTENCE_END = set('.;··')
ELISIONS = os.path.join(os.path.dirname(__file__), 'elision.tsv')


def elision_key(form):
    return with_accents(form.rstrip(APOS_LIKE))


def load_elisions(path=ELISIONS):
    table = {}
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if line.strip() and not line.startswith('#'):
                elided, full = line.rstrip('\n').split('\t')[:2]
                table[elision_key(elided.strip())] = full.strip()
    return table


def sentences(tokens, max_words=150):
    """Yield (start, end) slices ending after . ; · (or before the word past max_words)."""
    start, count = 0, 0
    for i, t in enumerate(tokens):
        if isinstance(t, Word) and t.lang == 'grc':
            if count >= max_words:
                yield start, i
                start, count = i, 0
            count += 1
        elif count and isinstance(t, Token) and t.kind == 'pc' and t.value in SENTENCE_END:
            yield start, i + 1
            start, count = i + 1, 0
    if start < len(tokens):
        yield start, len(tokens)


def clean_lemma(lemma, form):
    """None for what is not a lemma: an elided form returned as is, a title in capitals."""
    if not lemma or any(c in APOS_LIKE for c in lemma):
        return None
    if form.isupper() and lemma == form:
        return None
    return acute(lemma)


class SpacyLemmatizer:
    def __init__(self, model=MODEL, batch_size=64, crasis_path=None, apostrophe=APOSTROPHE):
        self.nlp = spacy.load(model)
        self.batch_size = batch_size
        self.apostrophe = apostrophe
        self.crasis = Crasis.load(crasis_path) if crasis_path else Crasis.load()
        self.elisions = load_elisions()
        meta = self.nlp.meta
        self.name = f'{meta.get("lang", "")}_{meta.get("name", model)} {meta.get("version", "")}'.strip()
        self.crasis_by_rule = Counter()     # crasis found by the coronis only: check, add to crasis.tsv

    def spacy_form(self, form):
        """The word as spaCy should see it: elisions from the table in full, otherwise
        with the ASCII apostrophe, the only one OdyCy lemmatizes."""
        if form.endswith(self.apostrophe):
            full = self.elisions.get(elision_key(form))
            if full:
                return full
        return form.replace(self.apostrophe, "'")

    def _sentence(self, tokens):
        """Words for spaCy and, for each, what to do with its analysis."""
        words, plan = [], []
        for t in tokens:
            if isinstance(t, Word) and t.lang == 'grc':
                form = t.form
                hit = self.crasis.match(form)
                if hit:
                    cut, lemmas = hit
                    pieces = [form[:cut], form[cut:]]
                    texts = [lemmas[0], lemmas[1] or self.spacy_form(pieces[1])]
                    plan.append(('crasis', t, pieces, lemmas))
                    words += texts
                else:
                    plan.append(('word', t))
                    words.append(self.spacy_form(form))
            elif isinstance(t, Token) and t.kind in ('pc', 'num'):
                plan.append(('skip',))
                words.append(t.value if t.kind == 'pc' else ''.join(v for k, v in t.pieces if k == 't'))
        return words, plan

    def run(self, tokens):
        jobs = []
        for a, b in sentences(tokens):
            words, plan = self._sentence(tokens[a:b])
            if words:
                jobs.append((words, plan))
        docs = self.nlp.pipe((Doc(self.nlp.vocab, words=w) for w, _ in jobs), batch_size=self.batch_size)
        for (_, plan), doc in zip(jobs, docs):
            analyses = iter(doc)
            for step in plan:
                if step[0] == 'skip':
                    next(analyses)
                elif step[0] == 'word':
                    w, a = step[1], next(analyses)
                    if '…' not in w.form:          # lost letters: no guess
                        w.lemma = clean_lemma(a.lemma_, a.text)
                        w.resp = 'spacy' if w.lemma else None
                        w.pos = a.pos_ or None
                        w.msd = str(a.morph) or None
                else:
                    _, w, pieces, lemmas = step
                    first, second = next(analyses), next(analyses)
                    if lemmas[1] is None:
                        self.crasis_by_rule[w.form] += 1
                        lemma2 = clean_lemma(second.lemma_, second.text)
                        resp2 = 'spacy' if lemma2 else None
                    else:
                        lemma2, resp2 = lemmas[1], 'lexicon'
                    w.crasis = [(pieces[0], lemmas[0], first.pos_ or None, 'lexicon'),
                                (pieces[1], lemma2, second.pos_ or None, resp2)]
