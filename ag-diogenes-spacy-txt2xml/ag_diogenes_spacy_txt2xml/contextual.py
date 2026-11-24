"""Contextual lemma selection with independently inspectable provider evidence.

Restoration generation, its evidence requirement, its priors, and sentence
boundaries remain those of the existing expander. GreTa no longer scores all
candidates. Morpho is optional evidence and cannot silently change a lemma.
"""
from collections import Counter
from dataclasses import asdict
import math
import time
import unicodedata

from ag_greek_rules import assess_pos, assess_pronoun, select_lemma
from ag_greek_rules.selection import lemma_key
from .analyses import Analysis
from .expansion import CRASIS_RULE_ID, CRASIS_RULE_VERSION
from .greberta import Prediction
from .hybrid import Hybrid, Tag, grammatical_conflicts, grammatical_score, same_lemma, selected_grammar, sentence_groups
from .normalize import APOS_LIKE, is_greek_letter
from .review_priority import word_priority, REVIEW_RULE_ID, REVIEW_RULE_VERSION
from .tokenize import Token, Word

MORPHO_LIMIT = 20


def _context_form(form):
    """Align unexpanded trailing elision apostrophes with GreBERTa's U+0313 training convention."""
    if len(form) >= 2 and form[-1] in APOS_LIKE and is_greek_letter(form[-2]):
        return form[:-1] + '\u0313'
    return form


class Contextual(Hybrid):
    """One contextual selector; other providers retain their distinct roles."""

    def _sentence(self, tokens, choices, override=None):
        forms, owners = super()._sentence(tokens, choices, override)
        return [_context_form(form) if owner else form for form, owner in zip(forms, owners)], owners

    def _jobs(self, tokens, choices):
        jobs, aliases = [], {}
        for sentence in sentence_groups(tokens):
            jobs.append(self._sentence(sentence, choices))
            for word in sentence:
                if not isinstance(word, Word) or id(word) not in choices: continue
                for index in range(1, len(choices[id(word)])):
                    baseline, alternative = choices[id(word)][0], choices[id(word)][index]
                    shared = len(baseline.atoms) == len(alternative.atoms) and (
                        baseline.shared_context and alternative.shared_context or
                        word.form[-1:] in APOS_LIKE and
                        (len(baseline.atoms) == 1 or baseline.atoms[0].form == alternative.atoms[0].form))
                    if shared:
                        for ai in range(len(alternative.atoms)):
                            aliases[id(word), index, ai] = (id(word), 0, ai)
                    else:
                        jobs.append(self._sentence(sentence, choices, (id(word), index)))
        return jobs, aliases

    def run(self, tokens, words):
        started = time.perf_counter()
        active = [word for word in words if '…' not in word.form]
        choices = {id(word): self.expander.readings(word.form) for word in active}
        followers, previous = {}, None
        for token in tokens:
            if isinstance(token, Word) and token.lang == 'grc':
                if previous is not None:
                    followers[id(previous)] = token.form
                previous = token
            elif isinstance(token, Token) and token.kind == 'pc':
                previous = None
        jobs, aliases = self._jobs(tokens, choices)
        predictions = {}
        self.log(f'  GreBERTa joint: λήμματα και γραμματική συμφραζομένων ({len(jobs):,} ενότητες)…')
        results = iter(self.tagger.pipe(forms for forms, _ in jobs))
        for _, owners in jobs:
            try: result = next(results)
            except StopIteration: raise ValueError('Contextual model omitted an input sentence') from None
            if len(result) != len(owners):
                raise ValueError('Contextual model returned a different number of tokens')
            for owner, prediction in zip(owners, result):
                if owner and owner not in predictions: predictions[owner] = prediction
        try: next(results)
        except StopIteration: pass
        else: raise ValueError('Contextual model returned extra sentences')
        for owner, baseline in aliases.items():
            predictions[owner] = predictions.get(baseline, Prediction())
        contextual_time = time.perf_counter()-started
        atoms = [atom for readings in choices.values() for reading in readings for atom in reading.atoms]
        self.log(f'  GreTa: συγκριτικά λήμματα για {len({a.form for a in atoms}):,} τύπους (χωρίς βαθμολόγηση υποψηφίων)…')
        started = time.perf_counter()
        comparator = self.greta.predict_many(atom.form for atom in atoms)
        greta_time = time.perf_counter()-started
        requested, deferred = [], 0
        if self.morpho:
            targets = []
            for word in active:
                for ri, reading in enumerate(choices[id(word)]):
                    for ai, atom in enumerate(reading.atoms):
                        prediction = predictions.get((id(word), ri, ai), Prediction())
                        unsupported = not any(same_lemma(prediction.lemma, c.lemma) for c in atom.analyses)
                        disagrees = comparator.get(atom.form) and not same_lemma(prediction.lemma, comparator[atom.form])
                        if not prediction.resolved or unsupported and disagrees: targets.append(atom.form)
            targets = list(dict.fromkeys(targets))
            requested, deferred = targets[:MORPHO_LIMIT], max(0, len(targets)-MORPHO_LIMIT)
            self.log(f'  Morpho: πρόσθετα στοιχεία για {len(requested)} τύπους· δεν αλλάζουν αυτόματα το λήμμα.')
            self.morpho.ask(requested)
        headwords = getattr(self.store, 'headwords', None)
        known = {lemma_key(h) for h in headwords()} if headwords else None
        known_lemma = (lambda lemma: lemma_key(lemma) in known) if known is not None else None
        lookup = getattr(self.store, 'lemma_evidence', None)
        lemmas = ({c.lemma for atom in atoms for c in atom.analyses if c.lemma} |
                  {p.lemma for p in predictions.values() if p.lemma} |
                  {p.canonical_lemma for p in predictions.values() if p.canonical_lemma} |
                  {l for l in comparator.values() if l})
        lemma_evidence = lookup(lemmas) if lookup else {}
        stats = Counter()
        for word in active:
            options = []
            for ri, reading in enumerate(choices[id(word)]):
                selected, components, traces, script_scores, context_matches, grammar_scores = [], [], [], [], [], []
                for ai, atom in enumerate(reading.atoms):
                    prediction = predictions.get((id(word), ri, ai), Prediction())
                    tag = Tag(prediction.pos, prediction.msd)
                    pool = list(atom.analyses)  # Never share occurrence-specific inventories by spelling.
                    for lemma, provider in ((prediction.lemma, 'greberta'), (comparator.get(atom.form), 'greta')):
                        if lemma and not any(same_lemma(lemma, c.lemma) for c in pool):
                            pool.append(Analysis(atom.form, lemma, provider+':'+lemma, source=provider))
                    if self.morpho:
                        for lemma in self.morpho.lemmas(atom.form):
                            if not any(same_lemma(lemma, c.lemma) for c in pool):
                                pool.append(Analysis(atom.form, lemma, 'morpho:'+lemma, source='morpho'))
                    decision = select_lemma(prediction.as_dict(), (c.lemma for c in atom.analyses),
                                            comparator.get(atom.form), known_lemma)
                    traces.append(dict(decision, component=ai))
                    matches = [c for c in pool if same_lemma(c.lemma, decision['selected_lemma'])]
                    # Grammar resolves morphology within the already selected lemma,
                    # never competing lemmata or provider bonuses.
                    # Case variants (πόλις / Πόλις) tie on grammar when candidates carry no POS:
                    # keep the spelling that was selected, not whichever identifier sorts first.
                    # GreTa writes every lemma in lower case, so for its proposals a proper name
                    # is recognised from a title-cased written form (excluding ALL-CAPS headings).
                    spelled = decision['selected_lemma'] or ''
                    if (decision['selected_by'] == 'greta' and spelled and
                            (prediction.pos == 'PROPN' or
                             (atom.form[:1].isupper() and any(ch.islower() for ch in atom.form[1:])))):
                        spelled = unicodedata.normalize('NFC', spelled[:1].upper() + spelled[1:])
                    matches.sort(key=lambda c: (-grammatical_score(c, tag), c.lemma != spelled, c.lemma_id))
                    candidate = matches[0] if matches else Analysis(atom.form, '', '', source='unresolved')
                    pos, msd = selected_grammar(candidate, tag)
                    resp = candidate.source if candidate.lemma else None
                    if decision['selected_by'] in ('context', 'context_topk', 'context_lookup') and resp in ('diogenes', 'lexicon'):
                        resp += ' greberta'
                    selected.append((candidate, pos, msd, resp))
                    if prediction.script_log_probability is not None:
                        script_scores.append(prediction.script_log_probability)
                    in_ctx = (decision['selected_by'] in ('context', 'context_topk') or
                              (decision['reason'] == 'unique_local_lemma' and
                               (same_lemma(candidate.lemma, prediction.lemma) or
                                any(same_lemma(candidate.lemma, item.get('lemma')) for item in prediction.top_k))))
                    context_matches.append(1.0 if in_ctx else 0.0)
                    grammar_scores.append(grammatical_score(candidate, tag))
                    components.append({'form': atom.form, 'tagger': asdict(tag), 'context_model': 'greberta',
                        'context': prediction.as_dict(), 'greta': comparator.get(atom.form), 'selection': decision,
                        'context_form': choices[id(word)][0].atoms[ai].form if (id(word), ri, ai) in aliases else atom.form,
                        'selected_id': candidate.lemma_id, 'selected_pos': pos, 'selected_msd': msd,
                        'candidates': [dict(asdict(c), score=None, selected=c == candidate,
                            morphology_choice_score=grammatical_score(c, tag)) for c in pool]})
                    if self.morpho:
                        extra = self.morpho.evidence(atom.form)
                        for item in extra:
                            raw = getattr(self.morpho, 'records', {}).get(item['query'])
                            if raw and 'response' in raw: item['raw_response'] = raw['response']
                        components[-1]['morpho'] = extra
                score = (sum(script_scores)/len(script_scores) if script_scores else -100.0)+reading.prior
                context_match = sum(context_matches)/len(context_matches) if context_matches else 0.0
                grammar_fit = sum(grammar_scores)/len(grammar_scores) if grammar_scores else 0.0
                options.append((score, context_match, grammar_fit, reading, selected, components, traces))
            options.sort(key=lambda option: (-round(option[0], 9), -option[1], -option[2]))
            score, context_match, grammar_fit, reading, selected, components, traces = options[0]
            word.expansion = [atom.form for atom in reading.atoms]
            word.evidence = [{'expansion': [atom.form for atom in r.atoms], 'kind': r.kind,
                'score': value, 'score_kind': 'mean contextual edit-script log probability plus retained restoration prior; not correctness',
                'context_match': ctx_fit, 'grammar_fit': gram_fit,
                'restoration_prior': r.prior, 'notes': list(r.notes), 'shared_context': r.shared_context,
                'components': evidence, 'rule_contributions': decisions,
                'provider_receipt': getattr(self.tagger, 'receipt', {}),
                'review_policy': {'rule_id': REVIEW_RULE_ID, 'rule_version': REVIEW_RULE_VERSION, 'category': 'review_policy'},
                **({'restoration_rule': {'rule_id': CRASIS_RULE_ID, 'rule_version': CRASIS_RULE_VERSION}}
                   if r.kind in ('crasis', 'unresolved crasis') else {})}
                for value, ctx_fit, gram_fit, r, _, evidence, decisions in options]
            word.review_reasons = list(reading.notes)
            if reading.kind == 'unresolved crasis':
                word.review_reasons.append('crasis detected but no supported inflected expansion')
            if len(choices[id(word)]) == 8: word.review_reasons.append('expansion candidate limit reached (8)')
            for atom, (candidate, pos, msd, resp), entry in zip(reading.atoms, selected, components):
                distinct = {c.lemma for c in atom.analyses}
                if entry['greta'] and not same_lemma(candidate.lemma, entry['greta']):
                    word.review_reasons.append('GreTa and selected lexical analysis disagree')
                if len(distinct) == 1 and entry['context']['lemma'] and not same_lemma(candidate.lemma, entry['context']['lemma']):
                    word.review_reasons.append('GreBERTa and unambiguous local lemma disagree')
                if candidate.source not in ('diogenes', 'lexicon') or candidate.source == 'lexicon' and not candidate.pos and not candidate.msd:
                    word.review_reasons.append('lemma lacks a full local morphological analysis')
                if not entry['context']['resolved']:
                    word.review_reasons.append('contextual model unresolved; fallback needs review')
                if entry['selection']['reason'] == 'context_topk_inventory_disambiguation':
                    word.review_reasons.append('contextual top-1 outside local candidates; top-k contextual candidate used')
                elif entry['selection']['reason'].startswith('context_outside_inventory'):
                    word.review_reasons.append('contextual lemma outside the local candidates; '
                                               + entry['selection']['selected_by'] + ' used')
                if entry['context']['context_scope'] != 'complete sentence':
                    word.review_reasons.append('contextual model used a shortened context')
                if len(distinct) > 1 and entry['context']['lemma_source'].startswith('lookup'):
                    word.review_reasons.append('contextual model used a lookup fallback for an ambiguous form')
                if conflicts := grammatical_conflicts(candidate, Tag(**entry['tagger'])):
                    word.review_reasons.append('lexical and contextual grammar disagree: '+', '.join(conflicts))
                if atom.form in getattr(self.greta, 'skipped', ()):
                    word.review_reasons.append('GreTa length limit or incomplete prediction')
                stats[resp if candidate.lemma else 'no lemma'] += 1
            if reading.kind == 'crasis':
                from .tei import split_pieces
                pieces = split_pieces(word.pieces, len(reading.pieces[0]))
                surfaces = [''.join(value for kind, value in part if kind == 't') for part in pieces]
                word.crasis = [(surface, candidate.lemma or None, pos, resp)
                    for surface, (candidate, pos, _, resp) in zip(surfaces, selected)]
                word.crasis_msd = [msd for _, _, msd, _ in selected]
            else:
                candidate, word.pos, word.msd, word.resp = selected[0]
                word.lemma, word.lemma_id = candidate.lemma or None, candidate.lemma_id or None
            # Retain the approved, post-selection POS consistency policy and τις/τίς review observation.
            for index, (atom, (candidate, pos, _, _), entry) in enumerate(zip(reading.atoms, selected, components)):
                if candidate.lemma in ('τίς', 'τις'):
                    following = (reading.atoms[index + 1].form if index + 1 < len(reading.atoms)
                                 else followers.get(id(word)))
                    pron = assess_pronoun(atom.form, candidate.lemma, following=following)
                    if pron.status != 'not_applicable':
                        traces.append(dict(pron.as_dict(), component=index))
                        if pron.status == 'applied' and pron.score_delta < 0:
                            word.review_reasons.append('interrogative/indefinite accent conflicts with selected lemma')
                if candidate.pos or not candidate.lemma: continue
                lexical = lemma_evidence.get(candidate.lemma, [])
                surface = [asdict(c) for c in atom.analyses if c.source == 'diogenes' and same_lemma(c.lemma, candidate.lemma)]
                assessment = assess_pos(atom.form, candidate.lemma, original_pos=pos,
                                        form_analyses=surface, lemma_analyses=lexical)
                if assessment.status == 'not_applicable' or not lexical or not surface: continue
                entry['morpheus_headword_pos_evidence'] = lexical
                traces.append(dict(assessment.as_dict(), component=index))
                if assessment.status == 'applied':
                    if word.crasis:
                        piece, lemma, _, resp = word.crasis[index]
                        word.crasis[index] = (piece, lemma, assessment.proposed_pos, resp)
                        word.crasis_msd[index] = None
                    else: word.pos, word.msd = assessment.proposed_pos, None
                    stats['corroborated pos'] += 1
            word.evidence[0]['timings'] = {'context_seconds': contextual_time, 'greta_comparator_seconds': greta_time}
            if word_priority(word): stats['review needed'] += 1
            elif word.review_reasons: stats['analysis warnings'] += 1
        if deferred: stats['morpho deferred forms'] = deferred
        return stats
