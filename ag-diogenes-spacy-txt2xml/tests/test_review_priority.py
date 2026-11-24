from copy import deepcopy
from ag_diogenes_spacy_txt2xml.review_priority import priority_reasons, LEXICAL_AMBIGUITY, EXPANSION_AMBIGUITY


def record():
    return {'lemma': 'λόγος', 'review_reasons': ['ambiguous grammatical/lexical analysis'],
            'readings': [{'score': 3, 'components': [{'selected_id': 'one',
                'candidates': [{'lemma': 'λόγος', 'lemma_id': 'one', 'score': 3, 'source': 'diogenes'},
                               {'lemma': 'λόγος', 'lemma_id': 'two', 'score': 3, 'source': 'diogenes'}]}]}]}


def test_same_lemma_different_grammar_or_ids_is_not_a_lemma_doubt():
    r = record()
    r['review_reasons'] += ['lexical and contextual grammar disagree: Case, Gender']
    assert not priority_reasons(r)


def test_close_distinct_lexeme_keeps_its_priority():
    r = record()
    r['readings'][0]['components'][0]['candidates'][1]['lemma'] = 'λέγω'
    assert LEXICAL_AMBIGUITY in priority_reasons(r)


def test_disagreement_with_unsupported_distant_neural_proposal_is_secondary():
    r = record()
    r['review_reasons'] = ['GreTa and selected lexical analysis disagree']
    r['readings'][0]['components'][0]['candidates'][1].update(lemma='λέγω', source='greta', score=-2)
    assert not priority_reasons(r)
    r['readings'][0]['components'][0]['candidates'][1]['score'] = 2.5
    assert priority_reasons(r) == [LEXICAL_AMBIGUITY]


def test_unsupported_winning_lemma_remains_high_priority():
    r = record()
    r['readings'][0]['components'][0]['candidates'][0]['source'] = 'greta'
    assert 'lemma lacks a full local morphological analysis' in priority_reasons(r)


def test_inflected_restorations_with_the_same_lemmas_are_secondary():
    r = record()
    r['readings'].append(deepcopy(r['readings'][0]))
    r['readings'][1]['score'] -= 0.2
    r['review_reasons'].append('ambiguous expansion; ranking margin below 0.75 (not a probability)')
    assert not priority_reasons(r)
    r['readings'][1]['components'][0]['candidates'][0]['lemma'] = 'λέγω'
    assert EXPANSION_AMBIGUITY in priority_reasons(r)


def test_missing_lemma_and_unresolved_crasis_remain_visible():
    r = record()
    r['lemma'] = ''
    r['review_reasons'] = ['crasis detected but no supported inflected expansion']
    assert set(priority_reasons(r)) == {'unresolved lemma', r['review_reasons'][0]}


def test_greberta_topk_margin_out_of_inventory_and_pronoun_conflict_are_prioritized():
    r = {
        'lemma': 'ὁ',
        'review_reasons': [
            'contextual lemma outside the local candidates; context_lookup used',
            'interrogative/indefinite accent conflicts with selected lemma',
        ],
        'readings': [{
            'score': -0.5,
            'components': [{
                'selected_id': 'one',
                'context': {'top_k': [
                    {'lemma': 'ὁ', 'log_probability': -0.5},
                    {'lemma': 'ἕ', 'log_probability': -0.9},
                ]},
                'candidates': [
                    {'lemma': 'ὁ', 'lemma_id': 'one', 'score': None, 'source': 'diogenes'},
                    {'lemma': 'ἕ', 'lemma_id': 'two', 'score': None, 'source': 'diogenes'},
                ],
            }],
        }],
    }
    reasons = priority_reasons(r)
    assert 'contextual lemma outside the local candidates; context_lookup used' in reasons
    assert 'interrogative/indefinite accent conflicts with selected lemma' in reasons
    assert LEXICAL_AMBIGUITY in reasons
    # Wide margin (>= 0.75) does not trigger LEXICAL_AMBIGUITY.
    r['readings'][0]['components'][0]['context']['top_k'][1]['log_probability'] = -2.0
    assert LEXICAL_AMBIGUITY not in priority_reasons(r)
