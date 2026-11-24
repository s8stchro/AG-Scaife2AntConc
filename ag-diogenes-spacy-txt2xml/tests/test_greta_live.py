"""Explicit offline model checks; enable with AG_RUN_MODEL_TESTS=1 after preparation."""
import os

import pytest

from ag_diogenes_spacy_txt2xml.greta import Greta


@pytest.mark.skipif(os.environ.get('AG_RUN_MODEL_TESTS') != '1', reason='opt-in trained GreTa model check')
def test_trained_greta_generates_and_scores_without_lookup():
    model = Greta()
    forms = ['ἐμοί', 'ἀληθῆ', 'ἐστί', 'θήσονται', 'λόγου']
    expected = dict(zip(forms, ['ἐγώ', 'ἀληθής', 'εἰμί', 'τίθημι', 'λόγος']))
    assert model.predict_many(forms) == expected
    model.predictions.clear()
    model.batch_size = 1
    assert model.predict_many(forms) == expected
    scores = model.score_many([('ἐμοί', 'ἐγώ'), ('ἐμοί', 'ἐμός'), ('ἐστί', 'εἰμί'), ('ἐστί', 'ἐστί')])
    assert scores['ἐμοί', 'ἐγώ'] > scores['ἐμοί', 'ἐμός']
    assert scores['ἐστί', 'εἰμί'] > scores['ἐστί', 'ἐστί']
