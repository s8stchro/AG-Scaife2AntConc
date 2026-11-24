import json

from ag_scaife2antconc import review
from ag_scaife2antconc.pipeline import Options, run
from ag_diogenes_spacy_txt2xml.tokenize import Word
from .test_app import FAKE, FakeLemmatizer, tei


def test_occurrence_correction_overrides_global_without_changing_other_tokens(tmp_path):
    path = tmp_path / 'corrections.tsv'
    common = ['οἱ', 'ὁ', 'DET', '2', '', '', '', '', 'ἕ', 'PRON']
    occurrence = ['οἱ', 'ὁ', 'DET', '1', '', '', '', '', 'ὁ', 'DET']
    import csv
    with path.open('w', encoding='utf-8', newline='') as fh:
        csv.writer(fh, delimiter='\t').writerows([review.COLUMNS + review.SCOPE_COLUMNS,
                                               common + ['', '', ''], occurrence + ['Work', '1', '2']])
    corrections = review.read(path)
    words = [Word([('t', 'οἱ')], lemma='ὁ', pos='DET') for _ in range(2)]
    before = review.apply([('οἱ', w, '1', i) for i, w in enumerate(words, 1)], corrections, work='Work')
    assert [(w.lemma, w.pos) for w in words] == [('ἕ', 'PRON'), ('ὁ', 'DET')]
    sheet = review.Sheet()
    sheet.add('Work', [(None, [('1', [('οἱ', [w.lemma], w) for w in words])])], before)
    path.write_text(sheet.text(corrections), encoding='utf-8')
    restored = review.read(path)
    assert ('οἱ', 'ὁ') in restored and ('οἱ', 'ὁ', 'Work', '1', 2) in restored
    assert restored['οἱ', 'ὁ', 'Work', '1', 2].lemma == 'ὁ'


def test_evidence_records_final_corrections_and_machine_alternatives(tmp_path):
    class EvidenceLemmatizer(FakeLemmatizer):
        def __call__(self, tokens, words):
            stats = super().__call__(tokens, words)
            for w in words:
                w.evidence = [{'expansion': [w.form], 'components': [{'selected_id': 'test:1'}]}]
                w.review_reasons = ['sources disagree']
            return stats
    source = tei(tmp_path, '<div n="1"><p>λόγος</p></div>')
    out = tmp_path / 'out'
    run([source], str(out), Options(), log=lambda m: None, lemmatizer=EvidenceLemmatizer())
    record = json.loads((out / 'analysis-review.jsonl').read_text(encoding='utf-8'))
    assert (record['work'], record['citation'], record['token']) == ('in', '1', 1)
    assert record['review_reasons'] == ['sources disagree'] and record['readings']
    rows = review.rows_of(out / 'lemma-review.tsv')
    assert 'analysis-review.jsonl' in rows[1][4]
