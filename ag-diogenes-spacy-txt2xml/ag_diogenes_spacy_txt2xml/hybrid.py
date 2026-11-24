"""GreTa lemma evidence + contextual grammar + complete, defeasible Morpheus analyses.

Ranking weights are explicit heuristics, not measured probabilities. Alternatives and
disagreements are retained for review instead of being hidden behind a single source label.
"""
from collections import Counter
from dataclasses import asdict, dataclass
import math
from ag_greek_rules import assess_pos

from .analyses import Analysis, features
from .crasis import Crasis, with_accents
from .expansion import CRASIS_RULE_ID, CRASIS_RULE_VERSION, Expander, compatible, load_elisions
from .normalize import APOS_LIKE
from .pronouns import pronoun_score
from .review_priority import word_priority
from .tokenize import Token, Word


@dataclass(frozen=True)
class Tag:
    pos: str | None = None
    msd: str | None = None


class OdyCyTagger:
    """OdyCy supplies grammar only. Neither of its lemmatizers is loaded or called."""

    def __init__(self, model='grc_odycy_joint_sm', batch_size=32):
        import spacy
        from spacy.tokens import Doc
        self.Doc = Doc
        self.batch_size = batch_size
        self.nlp = spacy.load(model, exclude=['trainable_lemmatizer', 'frequency_lemmatizer', 'parser'])
        self.name = f'{model} {self.nlp.meta.get("version", "")} (grammar only)'

    def pipe(self, sentences):
        docs = (self.Doc(self.nlp.vocab, words=words) for words in sentences)
        for doc in self.nlp.pipe(docs, batch_size=self.batch_size):
            yield [Tag(t.pos_ or None, str(t.morph) or None) for t in doc]


def same_lemma(a, b):
    # Case and grave/acute are presentation differences; breathings and other accents are not.
    return bool(a and b) and with_accents(a) == with_accents(b)


def function_word_score(analysis, form):
    """Retained uninflected spelling is defeasible evidence for its own headword.

    In a productive crasis this helps preserve an attested particle such as ἄν
    against a model's expansion to ἐάν. It does not veto other candidates.
    """
    return 1.0 if (analysis.pos in ('PART', 'CCONJ', 'SCONJ', 'ADP', 'ADV', 'INTJ') and
                   same_lemma(analysis.lemma, form)) else 0.0


def _feature_compatible(key, a, t):
    if a[key] & t[key]:
        return True
    # Morpheus represents pluperfect as Tense=Past|Aspect=Perf while GreBERTa emits Tense=Pqp.
    if key == 'Tense':
        if ('Past' in a['Tense'] and 'Perf' in a.get('Aspect', ()) and 'Pqp' in t['Tense']):
            return True
        if ('Pqp' in a['Tense'] and 'Past' in t['Tense'] and 'Perf' in t.get('Aspect', ())):
            return True
    return False


def grammatical_score(analysis, tag):
    score = 0.0
    if analysis.pos and tag.pos:
        equivalent = ({analysis.pos, tag.pos} <= {'AUX', 'VERB'} or
                      {analysis.pos, tag.pos} <= {'CCONJ', 'SCONJ'})
        score += 0.75 if analysis.pos == tag.pos or equivalent else -1.5
    a, t = features(analysis.msd), features(tag.msd)
    for key in a.keys() & t.keys():
        score += 0.4 if _feature_compatible(key, a, t) else -1.3
    return score


def grammatical_conflicts(analysis, tag):
    lexical, contextual = features(analysis.msd), features(tag.msd)
    conflicts = [k for k in lexical.keys() & contextual.keys() if not _feature_compatible(k, lexical, contextual)]
    if analysis.pos and tag.pos and analysis.pos != tag.pos and not (
            {analysis.pos, tag.pos} <= {'AUX', 'VERB'} or {analysis.pos, tag.pos} <= {'CCONJ', 'SCONJ'}):
        conflicts.append('POS')
    return sorted(conflicts)


def selected_grammar(analysis, tag):
    pos = analysis.pos or tag.pos
    if {pos, tag.pos} <= {'AUX', 'VERB'} or {pos, tag.pos} <= {'CCONJ', 'SCONJ'}:
        pos = tag.pos
    if not analysis.msd:
        return pos, tag.msd if grammatical_score(analysis, tag) >= 0 else None
    lexical, contextual = features(analysis.msd), features(tag.msd)
    # Narrow disjunctive lexical features only where the contextual reading is compatible.
    chosen = {key: (values & contextual[key] or values) if key in contextual else values
              for key, values in lexical.items()}
    return pos, '|'.join(f'{k}={",".join(sorted(v))}' for k, v in sorted(chosen.items()))


def sentence_groups(tokens, max_words=150):
    group, count = [], 0
    for t in tokens:
        if isinstance(t, Word) and t.lang == 'grc':
            if count >= max_words:
                yield group
                group, count = [], 0
            count += 1
        group.append(t)
        if isinstance(t, Token) and t.kind == 'pc' and t.value in '.;··;!' and count:
            yield group
            group, count = [], 0
    if group:
        yield group


class Hybrid:
    def __init__(self, store, greta, tagger, morpho=None, log=lambda m: None):
        self.store, self.greta, self.tagger, self.morpho, self.log = store, greta, tagger, morpho, log
        self.expander = Expander(store, Crasis.load(), load_elisions())

    def _sentence(self, tokens, choices, override=None):
        forms, owners = [], []
        for t in tokens:
            if isinstance(t, Word) and t.lang == 'grc' and '…' not in t.form:
                index = override[1] if override and override[0] == id(t) else 0
                reading = choices[id(t)][index]
                forms.extend(a.form for a in reading.atoms)
                owners.extend((id(t), index, i) for i in range(len(reading.atoms)))
            elif isinstance(t, Token) and t.kind in ('pc', 'num'):
                forms.append(str(t.value))
                owners.append(None)
        return forms, owners

    def run(self, tokens, words):
        active = [w for w in words if '…' not in w.form]
        choices = {id(w): self.expander.readings(w.form) for w in active}
        followers, previous = {}, None
        for token in tokens:
            if isinstance(token, Word) and token.lang == 'grc':
                if previous is not None:
                    followers[id(previous)] = token.form
                previous = token
            elif isinstance(token, Token) and token.kind == 'pc':
                previous = None
        jobs, aliases = [], {}
        for sentence in sentence_groups(tokens):
            jobs.append(self._sentence(sentence, choices))
            for w in sentence:
                if isinstance(w, Word) and id(w) in choices:
                    for index in range(1, len(choices[id(w)])):
                        baseline, alternative = choices[id(w)][0], choices[id(w)][index]
                        shared_context = (len(baseline.atoms) == len(alternative.atoms) and (
                            (baseline.shared_context and alternative.shared_context) or
                            (w.form[-1:] in APOS_LIKE and
                             (len(baseline.atoms) == 1 or baseline.atoms[0].form == alternative.atoms[0].form))))
                        if shared_context:
                            # Do not let an invented restoration (e.g. φέροι for φέρ’)
                            # supply its own optative grammar as corroborating evidence.
                            for ai in range(len(alternative.atoms)):
                                aliases[id(w), index, ai] = (id(w), 0, ai)
                        else:
                            jobs.append(self._sentence(sentence, choices, (id(w), index)))
        tags = {}
        self.log('  OdyCy: γραμματική των κλιτών αναπτύξεων (χωρίς λημματοποιητή)…')
        for (_, owners), result in zip(jobs, self.tagger.pipe(forms for forms, _ in jobs)):
            if len(owners) != len(result):
                raise ValueError('Context tagger returned a different number of tokens')
            for owner, tag in zip(owners, result):
                # Alternate sentences must not overwrite the baseline tags of other words.
                if owner and owner not in tags:
                    tags[owner] = tag
        for owner, baseline in aliases.items():
            tags[owner] = tags.get(baseline, Tag())
        atoms = [a for ws in choices.values() for r in ws for a in r.atoms]
        self.log(f'  GreTa: νευρωνικά λήμματα για {len({a.form for a in atoms}):,} αναπτυγμένους τύπους…')
        predictions = self.greta.predict_many(a.form for a in atoms)
        if self.morpho:
            self.morpho.ask(a.form for a in atoms if len({x.lemma for x in a.analyses}) != 1 or
                            not any(same_lemma(predictions.get(a.form), x.lemma) for x in a.analyses))
        pools = {}
        for a in atoms:
            pool = list(a.analyses)
            prediction = predictions.get(a.form)
            if prediction and not any(same_lemma(prediction, x.lemma) for x in pool):
                pool.append(Analysis(a.form, prediction, 'greta:' + prediction, source='greta'))
            if self.morpho:
                for lemma in self.morpho.lemmas(a.form):
                    if not any(same_lemma(lemma, x.lemma) for x in pool):
                        pool.append(Analysis(a.form, lemma, 'morpho:' + lemma, source='morpho'))
            pools[id(a)] = tuple(pool)
        pairs = [(a.form, c.lemma) for a in atoms for c in pools[id(a)]]
        self.log('  GreTa: βαθμολόγηση υποψήφιων λημμάτων…')
        neural = self.greta.score_many(pairs)
        unsupported_lemmas = {c.lemma for pool in pools.values() for c in pool if not c.pos}
        lookup = getattr(self.store, 'lemma_evidence', None)
        lemma_evidence = lookup(unsupported_lemmas) if lookup else {}
        stats = Counter()
        for w in active:
            options = []
            for ri, reading in enumerate(choices[id(w)]):
                selected, score, evidence = [], 0.0, []
                for ai, atom in enumerate(reading.atoms):
                    tag = tags.get((id(w), ri, ai), Tag())
                    following = reading.atoms[ai + 1].form if ai + 1 < len(reading.atoms) else followers.get(id(w))
                    ranked = []
                    for candidate in pools[id(atom)]:
                        n = neural.get((atom.form, candidate.lemma), float('-inf'))
                        value = max(-8.0, n) if math.isfinite(n) else -8.0
                        value += {'diogenes': 1.5, 'lexicon': 0.5, 'morpho': 0.25}.get(candidate.source, 0)
                        value += grammatical_score(candidate, tag)
                        value += pronoun_score(candidate.lemma, atom.form, tag.msd, following)
                        if ai and reading.shared_context:
                            value += function_word_score(candidate, atom.form)
                        if ai and selected[0][0].lemma == 'ὁ' and not compatible(selected[0][2], candidate.msd):
                            value -= 4.0
                        if candidate.lemma[:1].isupper() and w.form[:1].islower():
                            value -= 0.5
                        if same_lemma(candidate.lemma, atom.hint):
                            value += 0.25
                            if ai == 0 and reading.kind == 'crasis':
                                value += 2.0  # structural evidence for the identified first word
                        if self.morpho and any(same_lemma(candidate.lemma, m) for m in self.morpho.lemmas(atom.form)):
                            value += 0.25  # unverified headword evidence, never a hard override
                        ranked.append((value, candidate))
                    ranked.sort(key=lambda item: (-item[0], item[1].lemma_id))
                    candidate = ranked[0][1] if ranked else Analysis(atom.form, '', '', source='unresolved')
                    pos, msd = selected_grammar(candidate, tag)
                    resp = candidate.source if candidate.lemma else None
                    if resp == 'diogenes':
                        resp += ' greta' if same_lemma(candidate.lemma, predictions.get(atom.form)) else ' context'
                    elif resp == 'lexicon':
                        resp += ' greta'
                    selected.append((candidate, pos, msd, resp))
                    score += ranked[0][0] if ranked else -20.0
                    evidence.append({'form': atom.form, 'tagger': asdict(tag), 'greta': predictions.get(atom.form),
                                     'context_form': (choices[id(w)][0].atoms[ai].form
                                                      if (id(w), ri, ai) in aliases else atom.form),
                                     'following_form': following,
                                     'selected_id': candidate.lemma_id, 'selected_pos': pos, 'selected_msd': msd,
                                     'candidates': [dict(asdict(c), score=v,
                                         greta_log_probability=(neural[atom.form, c.lemma]
                                             if math.isfinite(neural.get((atom.form, c.lemma), float('-inf'))) else None),
                                         function_word_score=(function_word_score(c, atom.form)
                                                              if ai and reading.shared_context else 0.0),
                                         context_score=grammatical_score(c, tag)) for v, c in ranked]})
                    for item in evidence[-1]['candidates']:
                        item['pronoun_accent_score'] = pronoun_score(item['lemma'], atom.form, tag.msd, following)
                    if self.morpho:
                        evidence[-1]['morpho'] = self.morpho.evidence(atom.form)
                options.append((score / len(reading.atoms) + reading.prior, reading, selected, evidence))
            options.sort(key=lambda o: -o[0])
            score, reading, selected, evidence = options[0]
            w.expansion = [a.form for a in reading.atoms]
            w.evidence = [{'expansion': [a.form for a in r.atoms], 'kind': r.kind,
                           'restoration_prior': r.prior, 'notes': list(r.notes), 'shared_context': r.shared_context,
                           'score': s, 'components': e}
                          for s, r, _, e in options]
            for trace in w.evidence:
                if trace['kind'] in ('crasis', 'unresolved crasis'):
                    trace['restoration_rule'] = {'rule_id': CRASIS_RULE_ID, 'rule_version': CRASIS_RULE_VERSION}
            w.review_reasons = list(reading.notes)
            if reading.kind == 'unresolved crasis':
                w.review_reasons.append('crasis detected but no supported inflected expansion')
            if len(choices[id(w)]) == 8:
                w.review_reasons.append('expansion candidate limit reached (8)')
            if len(options) > 1 and score - options[1][0] < 0.75:
                w.review_reasons.append('ambiguous expansion; ranking margin below 0.75 (not a probability)')
            for atom, (candidate, pos, msd, resp), entry in zip(reading.atoms, selected, evidence):
                if entry['greta'] and not same_lemma(candidate.lemma, entry['greta']):
                    w.review_reasons.append('GreTa and selected lexical analysis disagree')
                if candidate.source not in ('diogenes', 'lexicon') or (
                        candidate.source == 'lexicon' and not candidate.pos and not candidate.msd):
                    w.review_reasons.append('lemma lacks a full local morphological analysis')
                top = entry['candidates'][0] if entry['candidates'] else None
                different = [c for c in entry['candidates'][1:] if
                             (c['lemma_id'], c['pos'], c['msd']) != (top['lemma_id'], top['pos'], top['msd'])]
                if different and top['score'] - different[0]['score'] < 0.75:
                    w.review_reasons.append('ambiguous grammatical/lexical analysis')
                if conflicts := grammatical_conflicts(candidate, Tag(**entry['tagger'])):
                    w.review_reasons.append('lexical and contextual grammar disagree: ' + ', '.join(conflicts))
                if atom.form in getattr(self.greta, 'skipped', ()):
                    w.review_reasons.append('GreTa length limit or incomplete prediction')
                stats[resp if candidate.lemma else 'no lemma'] += 1
            if reading.kind == 'crasis':
                from .tei import split_pieces
                left, right = split_pieces(w.pieces, len(reading.pieces[0]))
                surfaces = [''.join(v for kind, v in part if kind == 't') for part in (left, right)]
                w.crasis = [(piece, a.lemma or None, pos, resp)
                            for piece, (a, pos, _, resp) in zip(surfaces, selected)]
                w.crasis_msd = [msd for _, _, msd, _ in selected]
            else:
                a, w.pos, w.msd, w.resp = selected[0]
                w.lemma, w.lemma_id = a.lemma or None, a.lemma_id or None
            traces = []
            for index, (atom, (candidate, pos, _, _), entry) in enumerate(zip(reading.atoms, selected, evidence)):
                if candidate.pos or not candidate.lemma:
                    continue
                lexical = lemma_evidence.get(candidate.lemma, [])
                surface = [asdict(a) for a in atom.analyses if a.source == 'diogenes']
                assessment = assess_pos(atom.form, candidate.lemma, original_pos=pos,
                                        form_analyses=surface, lemma_analyses=lexical)
                if assessment.status == 'not_applicable' or not lexical or not surface:
                    continue
                entry['dictionary_pos_evidence'] = lexical
                traces.append(dict(assessment.as_dict(), component=index))
                if assessment.status == 'applied':
                    if w.crasis:
                        piece, lemma, _, resp = w.crasis[index]
                        w.crasis[index] = (piece, lemma, assessment.proposed_pos, resp)
                        w.crasis_msd[index] = None
                    else:
                        w.pos, w.msd = assessment.proposed_pos, None
                    stats['corroborated pos'] += 1
            if traces:
                w.evidence[0]['rule_contributions'] = traces
            if word_priority(w):
                stats['review needed'] += 1
            elif w.review_reasons:
                stats['analysis warnings'] += 1
        return stats
