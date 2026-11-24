import pytest
from ag_scaife2antconc import accents


@pytest.mark.parametrize('form, expected', [('τὶ', 'τι'), ('τὶς', 'τις'), ('Τὶς', 'Τις'),
                                          ('τί', 'τί'), ('τίς', 'τίς'), ('καὶ', 'καί')])
def test_accent_normalization_preserves_indefinite_interrogative_distinction(form, expected):
    assert accents.normalize(form) == expected


def test_accent_normalization_with_greek_punctuation_preserves_indefinite():
    from ag_diogenes_spacy_txt2xml.source import Line
    lines = [Line(None, '1', 'τὶς· τὶ; τίς; καὶ·')]
    accents.normalize_lines(lines)
    assert lines[0].text == 'τις· τι; τίς; καί·'
