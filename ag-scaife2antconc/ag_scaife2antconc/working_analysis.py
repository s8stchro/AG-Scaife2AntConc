"""Analyse the current text of affected passages; retain manual choices in the working model."""
from . import pipeline, review
from ag_diogenes_spacy_txt2xml.normalize import Normalizer
from ag_diogenes_spacy_txt2xml.source import Line, add_milestones
from ag_diogenes_spacy_txt2xml.tokenize import Token, Word, tokenize


def _passage_tokens_and_spans(passage):
    """Use stored Greek token boundaries, retaining intervening punctuation/context.

    Returns (all_tokens, span_words) where span_words matches passage['tokens'] 1:1,
    preventing damaged words or stray Greek characters in gaps from mismatching spans.
    """
    line = Line(None, passage['citation'], '')
    add_milestones([line])
    tokens, span_words = [Token('ms', line.milestones)], []
    text, cursor = passage['text'], 0
    for span in passage['tokens']:
        gap = text[cursor:span['start']]
        extra = [t for t in tokenize([Line(None, None, Normalizer()(gap))], tlg_gap=False)
                 if not isinstance(t, Token) or t.kind != 'ms']
        if extra:
            extra[0].space = cursor == 0 or bool(gap[:1].isspace())
        tokens.extend(extra)
        word = Word([('t', span['form'])], space=span['start'] == 0 or text[span['start']-1].isspace())
        tokens.append(word)
        span_words.append(word)
        cursor = span['end']
    gap = text[cursor:]
    extra = [t for t in tokenize([Line(None, None, Normalizer()(gap))], tlg_gap=False)
             if not isinstance(t, Token) or t.kind != 'ms']
    if extra:
        extra[0].space = cursor == 0 or bool(gap[:1].isspace())
    return tokens + extra, span_words


def passage_tokens(passage):
    """Use stored Greek token boundaries, retaining intervening punctuation/context."""
    return _passage_tokens_and_spans(passage)[0]


SENTENCE_END = '.;··;!'
CONTEXT_RADIUS = 15


def _ends_sentence(passage):
    text = (passage.get('text') or '').rstrip(' \t\r\n"»”’)]〉⟩')
    return bool(text) and text[-1] in SENTENCE_END


def _sentence_windows(all_passages, pending_ids):
    """Expand pending passages to enclosing sentence boundaries within each work."""
    by_work = {}
    for passage in all_passages:
        by_work.setdefault(passage['work'], []).append(passage)
    groups = []
    for work_passages in by_work.values():
        intervals = []
        for idx, passage in enumerate(work_passages):
            if passage['id'] not in pending_ids:
                continue
            lo = idx
            while lo > 0 and idx - lo < CONTEXT_RADIUS and not _ends_sentence(work_passages[lo - 1]):
                lo -= 1
            hi = idx
            while hi + 1 < len(work_passages) and hi - idx < CONTEXT_RADIUS and not _ends_sentence(work_passages[hi]):
                hi += 1
            if intervals and lo <= intervals[-1][1] + 1:
                intervals[-1] = (intervals[-1][0], max(intervals[-1][1], hi))
            else:
                intervals.append((lo, hi))
        for lo, hi in intervals:
            groups.append(work_passages[lo:hi + 1])
    return groups


def analyse(model, options, *, log=print, before_publish=None, engine=None, scope='changed'):
    if scope not in ('changed', 'all'):
        raise ValueError('Invalid analysis scope.')
    all_passages = model.export_data()['passages']
    pending = [p for p in all_passages if scope == 'all' or p.get('annotation_stale') or
               any(t.get('stale') or t.get('context_stale') or not t.get('lemma') for t in p['tokens'])]
    if not pending:
        log('No new or edited passages need analysis.')
        return 0
    pending_ids = {p['id'] for p in pending}
    expected = model.revision
    own_engine = engine is None
    engine = engine or pipeline.Lemmatizer(options, log)
    updates = []
    try:
        if scope == 'all':
            by_work = {}
            for passage in pending:
                by_work.setdefault(passage['work'], []).append(passage)
            windows = list(by_work.values())
        else:
            windows = _sentence_windows(all_passages, pending_ids)
        for i, passages in enumerate(windows, 1):
            label = passages[0]['work'] if scope == 'all' else next(p['citation'] for p in passages if p['id'] in pending_ids)
            log(f"Analysing current text: {label} ({i}/{len(windows)})")
            parts = [(passage, *_passage_tokens_and_spans(passage)) for passage in passages]
            tokens = [token for _, part, _ in parts for token in part]
            words = [w for _, _, span_words in parts for w in span_words]
            engine(tokens, words)
            for passage, _, span_words in parts:
                if passage['id'] not in pending_ids:
                    continue
                records = []
                for word in span_words:
                    records.append({'form': pipeline.word_text(word), 'lemma': review.lemma_of(word), 'pos': review.pos_of(word) or 'X',
                                    'expansion': word.expansion or [pipeline.word_text(word)],
                                    'machine_crasis': word.crasis,
                                    'msd': word.crasis_msd if word.crasis else word.msd,
                                    'resp': review.resp_of(word), 'lemma_id': word.lemma_id,
                                    'reviewed': False, 'review_reasons': word.review_reasons,
                                    'readings': word.evidence,
                                    'rule_contributions': word.evidence[0].get('rule_contributions', []) if word.evidence else []})
                updates.append({'passage_id': passage['id'], 'records': records})
        if before_publish:
            before_publish()
        from . import __version__
        from ag_diogenes_spacy_txt2xml import __version__ as core_version
        from dataclasses import asdict
        model.apply_analyses(updates, expected_revision=expected, metadata={
            'app_version': __version__, 'core_version': core_version,
            'providers': engine.sources() if hasattr(engine, 'sources') else [],
            'options': dict(asdict(options), keep=sorted(options.keep)),
            'context_scope': 'whole work; existing sentence boundaries; stored token boundaries' if scope == 'all' else
                             'enclosing sentence context; stored token boundaries',
            'analysis_scope': scope})
        log('Analyses saved. Manual choices were preserved.')
        return len(updates)
    finally:
        if own_engine and hasattr(engine, 'close'):
            engine.close()
