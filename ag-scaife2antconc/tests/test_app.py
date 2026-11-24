"""Tests on the two Scaife downloads in tests/data (Perseus, CC BY-SA 4.0):
isaeus-1.xml (Isaeus 1, prose with Loeb notes and a hypothesis) and
aristophanes-acharnians.xml (Aristophanes, Acharnians, verse with speakers)."""
import os
import xml.etree.ElementTree as ET

import pytest

from ag_diogenes_spacy_txt2xml import diogenes
from ag_diogenes_spacy_txt2xml.tokenize import Word
from ag_scaife2antconc import catalog, pos, review
from ag_scaife2antconc.pipeline import Options, SetupError, run
from ag_scaife2antconc.reader import Work, read, strip_deletions

DATA = os.path.join(os.path.dirname(__file__), 'data')
ISAEUS = os.path.join(DATA, 'isaeus-1.xml')
ACHARNIANS = os.path.join(DATA, 'aristophanes-acharnians.xml')
TEI = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><div type="edition" n="urn:cts:greekLit:tlg9999.tlg001.x" xml:lang="grc">{}</div></body></text></TEI>'


def tei(tmp_path, body):
    p = tmp_path / 'in.xml'
    p.write_text(TEI.format(body), encoding='utf-8')
    return str(p)


def text(work):
    return ' '.join(line.text for line in work.lines)


class FakeLemmatizer:
    """Stands in for Diogenes + OdyCy where only the cleansing is tested: each word is its own lemma."""
    heads, morpho, dio = set(), None, 'Diogenes (test)'

    class spacy:
        name = 'test'

    def __call__(self, tokens, words):
        from collections import Counter
        for w in words:
            w.lemma, w.pos, w.resp = w.form, 'X', 'diogenes'
        return Counter()

    def description(self):
        return 'test'

    def sources(self):
        return []


FAKE = FakeLemmatizer()


def forms_of(path):
    """The lines of an antconc-tagged file with the forms only (a crasis written as one word)."""
    from ag_scaife2antconc.pipeline import HAIR_SPACE
    return [' '.join(''.join(t.split('_')[0] for t in word.split(HAIR_SPACE)) for word in line.split(' '))
            for line in path.read_text(encoding='utf-8').splitlines()]


# -- reader -------------------------------------------------------------------------

def test_isaeus_units_and_metadata():
    w = read(ISAEUS)
    assert w.ident == 'tlg0017.tlg001' and w.lang == 'grc'
    assert [l.label for l in w.lines] == [str(n) for n in range(1, 52)]   # section 0 = hypothesis, left out
    assert w.lines[0].text.startswith('πολλὴ μὲν ἡ μεταβολή')


def test_isaeus_left_out():
    w = read(ISAEUS)
    t = text(w)
    for gone in ('ἀδελφιδοῖ', 'Ὑπόθεσις', 'Μάρτυρες', 'Schoemann', 'Buermann', 'add.', 'Aldus'):
        assert gone not in t
    assert w.dropped['hypothesis'] == 107
    assert w.elements['notes'] == 44
    # [ἐπὶ τούτοις] (2) is an editorial deletion; the supplement 〈ἡμᾶς〉 (1) is kept
    assert w.lines[1].text.startswith('καὶ οἱ μὲν οἰκεῖοι καὶ οἱ προσήκοντες οἱ τούτων')
    assert '〈ἡμᾶς〉' in w.lines[0].text
    assert 'ἀρχονίδην' not in w.lines[14].text          # [<seg type="noparse">ἀρχονίδην</seg>]


def test_acharnians_verse_lines_without_speakers():
    w = read(ACHARNIANS)
    assert w.lines[0].label == '1' and w.lines[0].text == 'ὅσα δὴ δέδηγμαι τὴν ἐμαυτοῦ καρδίαν,'
    assert w.speakers['Δικαιόπολις'] == 211
    assert sum(w.speakers.values()) == w.elements['speaker names'] == 489
    # only where the name is spoken: Δικαιόπολις καλεῖ σε (406), Δικαιόπολις εἴ μʼ ἴδοι (1196)
    assert [l.label for l in w.lines if l.text.startswith(('Δικαιόπολις', 'Χορός', 'Λάμαχος'))] == ['406', '1196']
    assert 'πρίω,' in next(l.text for l in w.lines if l.label == '35')    # <q> kept
    assert 'lines 208-235' not in text(w)                                  # inline English note
    assert w.lines[-1].label == '1234'


def test_hypothesis_by_subtype_and_choice(tmp_path):
    w = read(tei(tmp_path, '<div type="textpart" subtype="hypothesis" n="0"><p>ὑπόθεσις λόγου</p></div>'
                           '<div type="textpart" subtype="section" n="1"><p>ἀνὴρ <choice><sic>κακος</sic>'
                           '<corr>κακός</corr></choice> <app><lem>ἦν</lem><rdg>ἐστι</rdg></app>'
                           '<note>Reiske</note></p></div>'))
    assert [(l.label, l.text) for l in w.lines] == [('1', 'ἀνὴρ κακός ἦν')]
    assert w.dropped['hypothesis'] == 2 and w.elements['rejected readings'] == 2


def test_book_line_citation_and_lyric_divs(tmp_path):
    w = read(tei(tmp_path, '<div type="textpart" subtype="book" n="2"><div type="textpart" subtype="strophe" n="1">'
                           '<l n="5">ἄνδρα μοι ἔννεπε</l><l n="6" style="hidden"/></div></div>'))
    assert [(l.label, [m.n for m in l.milestones]) for l in w.lines] == [('2.5', ['2', '2.5'])]


@pytest.mark.parametrize('fragment', [
    'ἀν<gap/>πος', '<gap/>θρωπος', 'ἀνθρω<gap/>',
    'ἀν<seg><gap/></seg>πος', 'ἀν<gap quantity="2" unit="character"/>πος',
])
@pytest.mark.parametrize('paragraph', [False, True])
def test_inline_gaps_exclude_the_entire_damaged_word(tmp_path, fragment, paragraph):
    from ag_scaife2antconc import review

    body = f'λόγος {fragment} καλός'
    source = tei(tmp_path, f'<div n="1"><p>{body}</p></div>' if paragraph else f'<div n="1">{body}</div>')
    out = tmp_path / 'out'
    run([source], str(out), Options(xml=True), log=lambda m: None, lemmatizer=FAKE)
    assert forms_of(out / 'antconc-tagged' / 'in.txt') == ['λόγος καλός']
    assert {r[0] for r in review.rows_of(out / 'lemma-review.tsv')[1:]} == {'λόγος', 'καλός'}
    root = ET.parse(out / 'xml' / 'in.xml').getroot()
    gap = root.find('.//{http://www.tei-c.org/ns/1.0}w/{http://www.tei-c.org/ns/1.0}gap')
    assert gap is not None
    assert gap.get('quantity') == ('2' if 'quantity="2"' in fragment else '1')


@pytest.mark.parametrize('body', [
    'ἀνήρ <gap quantity="1" unit="line"/> λόγος',
    'ἀνήρ<gap quantity="1" unit="word"/>λόγος',
])
def test_standalone_gap_does_not_remove_neighboring_words(tmp_path, body):
    source = tei(tmp_path, f'<div n="1"><p>{body}</p></div>')
    out = tmp_path / 'out'
    run([source], str(out), Options(), log=lambda m: None, lemmatizer=FAKE)
    assert forms_of(out / 'antconc-tagged' / 'in.txt') == ['ἀνήρ λόγος']


@pytest.mark.parametrize('linked', [
    'ἀνήρ <ref target="#foo">λόγος</ref> γυνή',
    'ἀνήρ <ref target="#foo"><hi>λόγος</hi></ref> γυνή',
    'ἀνήρ λό<ref target="#foo">γος</ref> γυνή',
])
def test_running_text_references_are_kept_but_notes_are_removed(tmp_path, linked):
    source = tei(tmp_path, f'<div n="1"><p>{linked}<note><ref target="#editor">σημείωση</ref></note>'
                          '</p></div>')
    work = read(source)
    assert text(work) == 'ἀνήρ λόγος γυνή'
    assert work.elements['notes'] == 1
    out = tmp_path / 'out'
    run([source], str(out), Options(), log=lambda m: None, lemmatizer=FAKE)
    assert forms_of(out / 'antconc-tagged' / 'in.txt') == ['ἀνήρ λόγος γυνή']


def test_hyphenation_across_verse_lines():
    w = read(ACHARNIANS)
    lines = {l.label: l.text for l in w.lines}
    assert lines['695'].endswith('θερμὸν ἀπομορξάμενον')
    assert lines['696'].startswith('ἀνδρικὸν ἱδρῶτα')
    assert w.elements['hyphenation'] == 1
    assert 'πολύν—' in ' '.join(lines.values())                # a dash of interruption is not a hyphen


def test_hyphenation_inside_and_soft(tmp_path):
    w = read(tei(tmp_path, '<div type="textpart" n="1"><p>ἀπο-<lb/>θανεῖν καὶ Ἀθη‐ ναῖοι, πρό- Τοῦ, '
                           'ἄν­δρες</p></div><div type="textpart" n="2"><p>ἐγένε­</p></div>'
                           '<div type="textpart" n="3"><p>το δέ</p></div>'))
    assert [(l.label, l.text) for l in w.lines] == [('1', 'ἀποθανεῖν καὶ Ἀθηναῖοι, πρό- Τοῦ, ἄνδρες'),
                                                    ('2', 'ἐγένετο'), ('3', 'δέ')]
    assert w.elements['hyphenation'] == 4             # ἀπο-, Ἀθη‐, ἄν(soft)ρες, ἐγένε(soft) | το


def test_keep_steps():
    w = read(ACHARNIANS, keep=frozenset({'speaker names', 'hyphenation'}))
    lines = {l.label: l.text for l in w.lines}
    assert lines['1'] == 'Δικαιόπολις ὅσα δὴ δέδηγμαι τὴν ἐμαυτοῦ καρδίαν,'
    assert lines['695'].endswith('ἀπο-') and lines['696'].startswith('μορξάμενον')
    assert w.kept['speaker names'] == 489 and not w.elements['speaker names']
    w = read(ISAEUS, keep=frozenset({'hypothesis', 'deletions'}))
    assert w.lines[0].label == '0' and w.lines[0].text.startswith('ἀδελφιδοῖ')    # heading still out
    assert '[ἐπὶ τούτοις]' in w.lines[2].text
    assert 'Schoemann' not in ' '.join(l.text for l in w.lines)                    # notes still out
    w = read(ISAEUS, keep=frozenset({'notes'}))
    assert 'Buermann' in w.lines[0].text
    assert 'διέθετο Scaliger: δὴ ὑπέθετο.' in w.lines[2].text      # <foreign> not glued to the editor's name


def test_scan_for_the_questions():
    gui = pytest.importorskip('ag_scaife2antconc.gui')
    found = gui.scan([ISAEUS, ACHARNIANS])
    assert found['speaker names'][0] == 489 and 'Δικαιόπολις (211)' in found['speaker names'][2][0]
    assert found['hyphenation'][0] == 1 and found['hypothesis'][0] == 1
    assert 'stage directions' not in found
    count, enclitic, examples = found['accents']
    assert (count, enclitic) == (2253, 90) and 'καὶ → καί' in examples      # 2,163 graves


# -- accents ----------------------------------------------------------------------------

@pytest.mark.parametrize('printed, normalized', [
    ('ὄρνιθός', 'ὄρνιθος'), ('ἄνθρωπός', 'ἄνθρωπος'), ('δῶρόν', 'δῶρον'), ('οὗτοί', 'οὗτοι'),
    ('ἐνσκευάσασθαί', 'ἐνσκευάσασθαι'), ('πρεσβῦταί', 'πρεσβῦται'), ('Ἀμφίθεός', 'Ἀμφίθεος'),
    ('καὶ', 'καί'), ('Τὸν', 'Τόν'),
    ('ὦγαθοί', 'ὦγαθοί'), ('ὦγαθοὶ', 'ὦγαθοί'),     # crasis: both accents are real
    ('ὦκβάτανα', 'ὦκβάτανα'), ('ὦχαρνέων', 'ὦχαρνέων'), ('μέντἂν', 'μέντἄν'),
    ('ἀΐδιός', 'ἀΐδιος'),                            # ἀ-ΐ-δι-ος: the diaeresis parts the vowels
])
def test_accents(printed, normalized):
    from ag_scaife2antconc import accents
    assert accents.normalize(printed) == normalized


def test_accents_of_elided_words():
    from ag_scaife2antconc import accents
    from ag_diogenes_spacy_txt2xml.source import Line
    lines = [Line(None, '1', "δήξομἄρʼ ὄρνιθός τινος καὶ")]
    changed = accents.normalize_lines(lines)
    assert lines[0].text == "δήξομἄρʼ ὄρνιθος τινος καί"
    assert accents.summary(changed) == (1, 1, ['ὄρνιθός → ὄρνιθος', 'καὶ → καί'])


def test_deletion_spanning_units():
    w = Work('x')
    assert strip_deletions([('1', 'α [β'), ('2', 'γ] δ')], w) == [('1', 'α'), ('2', 'δ')]
    assert w.dropped['deletions'] == 2


# -- names -----------------------------------------------------------------------------

def test_catalog_names():
    e = catalog.lookup('tlg0017.tlg001')
    assert e.display == 'Isaeus, On The Estate of Cleonymus (Περὶ τοῦ Κλεωνύμου κλήρου)'
    assert e.file_name == 'Isaeus-On-The-Estate-of-Cleonymus'
    assert catalog.lookup('tlg0540.tlg001').file_name == 'Lysias-On-the-Murder-of-Eratosthenes'
    assert catalog.lookup('tlg9999.tlg999') is None
    assert catalog.slug('Ἀριστοφάνης: Ὄρνιθες') == 'Αριστοφανης-Ορνιθες'
    assert len(catalog.slug('word ' * 40)) <= catalog.MAX_NAME


def test_unknown_work_keeps_the_input_name(tmp_path):
    run([tei(tmp_path, '<div type="textpart" n="1"><p>ἀνὴρ ἦν</p></div>')], str(tmp_path / 'out'),
        Options(), log=lambda m: None, lemmatizer=FAKE)
    assert os.listdir(tmp_path / 'out' / 'antconc-tagged') == ['in.txt']


def test_results_bear_the_input_file_names(tmp_path):
    import shutil
    from ag_scaife2antconc.file_io import input_name, keep_name
    from ag_scaife2antconc.pipeline import legacy_names, plan
    own = tmp_path / 'Ἀχαρνῆς (Olson 2002) v.1.xml'
    shutil.copy(ACHARNIANS, own)
    twin = tmp_path / 'other' / own.name
    twin.parent.mkdir()
    shutil.copy(ACHARNIANS, twin)
    works = plan([str(own), ISAEUS, str(twin)])
    assert [name for _, _, name, _ in works] == ['Ἀχαρνῆς (Olson 2002) v.1', 'isaeus-1', 'Ἀχαρνῆς (Olson 2002) v.1-2']
    assert works[1][3].startswith('Isaeus, On The Estate of Cleonymus')        # the catalogue still gives the title
    assert legacy_names(works) == {'Ἀχαρνῆς (Olson 2002) v.1': 'Aristophanes-Acharnians',
                                   'isaeus-1': 'Isaeus-On-The-Estate-of-Cleonymus',
                                   'Ἀχαρνῆς (Olson 2002) v.1-2': 'Aristophanes-Acharnians-2'}
    run([str(own)], str(tmp_path / 'out'), Options(xml=True), log=lambda m: None, lemmatizer=FAKE)
    assert os.listdir(tmp_path / 'out' / 'antconc-tagged') == ['Ἀχαρνῆς (Olson 2002) v.1.txt']
    assert os.listdir(tmp_path / 'out' / 'xml') == ['Ἀχαρνῆς (Olson 2002) v.1.xml']
    assert input_name(r'C:\x\tlg0019.tlg001.perseus-grc2.xml') == 'tlg0019.tlg001.perseus-grc2'
    assert keep_name('a:b?c') == 'a_b_c' and keep_name('CON') == '_CON' and keep_name(' . ') == 'text'


def test_position_corrections_saved_under_the_old_catalogue_name_still_apply(tmp_path):
    import csv
    sheet = tmp_path / 'fixes.tsv'
    row = dict.fromkeys(review.COLUMNS, '')
    row.update({review.COLUMNS[0]: 'ὅσα', review.COLUMNS[1]: 'ὅσα', review.COLUMNS[8]: 'ὅσος', review.COLUMNS[9]: 'PRON'})
    with open(sheet, 'w', encoding='utf-8', newline='') as fh:
        csv.writer(fh, delimiter='\t').writerows([review.COLUMNS + review.SCOPE_COLUMNS,
                                                  list(row.values()) + ['Aristophanes-Acharnians', '1', '1']])
    run([ACHARNIANS], str(tmp_path / 'out'), Options(corrections=str(sheet)), log=lambda m: None, lemmatizer=FAKE)
    first = (tmp_path / 'out' / 'antconc-tagged' / 'aristophanes-acharnians.txt').read_text(encoding='utf-8').split()[0]
    assert first == 'ὅσα_PRON_ὅσος'


# -- part of speech -------------------------------------------------------------------

def test_pos_harmonize():
    table = {'μέν': 'PART', 'ἐν': 'ADP'}
    a = Word([('t', 'μὲν')], lemma='μέν', pos='AUX', msd='Mood=Ind')
    b = Word([('t', 'ἐν')], lemma='ἐν', pos='ADV', msd=None)
    c = Word([('t', 'κἀν')], crasis=[('κ', 'καί', 'CCONJ', 'lexicon'), ('ἀν', 'ἐν', 'VERB', 'lexicon')])
    assert pos.harmonize([a, b, c], table) == 2
    assert (a.pos, a.msd) == ('PART', None)
    assert b.pos == 'ADV'                                # prepositions in adverbial use stay ADV
    assert c.crasis[1][2] == 'ADP'


# -- whole run ------------------------------------------------------------------------

def test_cleansed_text_in_the_tagged_files(tmp_path):
    report = run([ISAEUS, ACHARNIANS], str(tmp_path), Options(), log=lambda m: None, lemmatizer=FAKE)
    forms = forms_of(tmp_path / 'antconc-tagged' / 'aristophanes-acharnians.txt')
    assert forms[0] == 'ὅσα δὴ δέδηγμαι τὴν ἐμαυτοῦ καρδίαν'          # no punctuation
    assert forms[16].startswith("ἀλλ' οὐδεπώποτ' ἐξ ὅτου 'γὼ")         # elision and prodelision with '
    lines = forms_of(tmp_path / 'antconc-tagged' / 'isaeus-1.txt')
    isaeus = '\n'.join(lines)
    assert len(lines) == 51 and '〈' not in isaeus and '†' not in isaeus
    assert 'ἐσώθη' in isaeus                                           # †ἐσώθη†: the word stays
    assert 'speaker names: 489 times' in open(report, encoding='utf-8').read()


def test_accents_as_printed_unless_asked(tmp_path):
    xml = tei(tmp_path, '<div type="textpart" n="1"><p>ὄρνιθός τινος καὶ</p></div>')
    for normalize, text in ((False, 'ὄρνιθός τινος καὶ'), (True, 'ὄρνιθος τινος καί')):
        out = tmp_path / str(normalize)
        report = run([xml], str(out), Options(accents=normalize), log=lambda m: None, lemmatizer=FAKE)
        assert forms_of(out / 'antconc-tagged' / 'in.txt') == [text]
        assert ('Accents normalised: 1 words with grave, 1 with second accent'
                in open(report, encoding='utf-8').read()) == normalize


def test_lemmas_without_diogenes_is_a_setup_error(tmp_path):
    with pytest.raises(SetupError):
        run([ISAEUS], str(tmp_path), Options(diogenes=str(tmp_path / 'none')), log=lambda m: None)


@pytest.mark.skipif(not diogenes.find(), reason='Diogenes is not installed')
def test_tagged_xml_and_review_sheet(tmp_path):
    from ag_diogenes_spacy_txt2xml.greberta import verified,default_path
    if not verified(default_path()): pytest.skip('prepared GreBERTa joint model is not installed')
    run([ISAEUS], str(tmp_path), Options(xml=True), log=lambda m: None)
    first = (tmp_path / 'antconc-tagged' / 'isaeus-1.txt').read_text(encoding='utf-8')
    assert first.split()[:2] == ['πολλὴ_ADJ_πολύς', 'μὲν_PART_μέν']
    root = ET.parse(tmp_path / 'xml' / 'isaeus-1.xml').getroot()
    w = root.findall('.//{http://www.tei-c.org/ns/1.0}w')
    assert (w[1].text, w[1].get('lemma'), w[1].get('pos')) == ('μὲν', 'μέν', 'PART')
    assert all(x.get('pos') for x in w if x.get('lemma'))
    assert sorted(os.listdir(tmp_path)) == ['analysis-review.jsonl', 'antconc-lemmata', 'antconc-tagged', 'lemma-review.tsv',
                                            'report.txt', 'xml']


# -- review sheet and corrections --------------------------------------------------------

def review_words():
    a = Word([('t', 'οἱ')], lemma='ὁ', pos='DET', resp='diogenes spacy')
    b = Word([('t', 'οἱ')], lemma='ἕ', pos='PRON', resp='diogenes spacy')
    c = Word([('t', 'γοργολόφα')], lemma='Γοργολόφας', pos='PROPN', resp='diogenes')
    d = Word([('t', 'ὦγαθοί')], crasis=[('ὦ', 'ὦ', 'INTJ', 'lexicon'), ('γαθοί', 'ἀγαθός', 'ADJ', 'lexicon')])
    e = Word([('t', 'ψαμμακοσιογάργαρα')])
    return [a, b, c, d, e]


def review_sections(ws):
    return [(None, [('21', [('οἱ', [ws[0].lemma], ws[0]), ('γοργολόφα', [ws[2].lemma], ws[2])]),
                    ('23', [('οἱ', [ws[1].lemma], ws[1]), ('ὦγαθοί', ['ὦ', 'ἀγαθός'], ws[3]),
                            ('ψαμμακοσιογάργαρα', [None], ws[4])])])]


def test_review_sheet_order_places_context():
    from ag_scaife2antconc import review
    ws = review_words()
    sheet = review.Sheet()
    sheet.add('Ach', review_sections(ws), {})
    rows = [r.split('\t') for r in sheet.text({}).lstrip(review.BOM).splitlines()]
    assert rows[0] == review.COLUMNS
    assert [r[:2] for r in rows[1:]] == [['ψαμμακοσιογάργαρα', ''], ['οἱ', 'ἕ'], ['οἱ', 'ὁ'],
                                         ['ὦγαθοί', 'ὦ+ἀγαθός'], ['γοργολόφα', 'Γοργολόφας']]
    assert rows[2][4:8] == ['Diogenes, chosen by OdyCy', 'ὁ', '23', '«οἱ» ὦγαθοί ψαμμακοσιογάργαρα']


def test_review_sheet_puts_lemmas_unknown_to_diogenes_first():
    from ag_scaife2antconc import review
    good = Word([('t', 'Ξανθίας')], lemma='Ξανθίας', pos='PROPN', resp='spacy')
    odd = Word([('t', 'κοῒ')], lemma='κοΐ', pos='NOUN', resp='spacy')
    sheet = review.Sheet(known={'Ξανθίας'})
    sheet.add('Ach', [(None, [('1', [('Ξανθίας', ['Ξανθίας'], good), ('κοῒ', ['κοΐ'], odd)])])], {})
    rows = [r.split('\t') for r in sheet.text({}).lstrip(review.BOM).splitlines()[1:]]
    assert [(r[0], r[4]) for r in rows] == [('κοῒ', 'OdyCy only, lemma not in Diogenes'),
                                            ('Ξανθίας', 'OdyCy only')]


def test_corrections_round_trip(tmp_path):
    from ag_scaife2antconc import review
    ws = review_words()
    sheet = review.Sheet()
    sheet.add('Ach', review_sections(ws), {})
    lines = sheet.text({}).splitlines()
    fixed = []
    for line in lines:
        cells = line.split('\t')
        if cells[:2] == ['οἱ', 'ἕ']:
            cells[8:10] = ['ὁ', 'DET']
        elif cells[:2] == ['γοργολόφα', 'Γοργολόφας']:
            cells[8:10] = ['γοργόλοφος', 'ADJ']
        elif cells[0] == 'ὦγαθοί':
            cells[8] = 'ἀγαθός'                                  # one lemma for two words: refused
        fixed.append('\t'.join(cells))
    fixed.append('\t'.join(['ὦχαρνηίδαι', '', '', '1', '', '', '322', '', 'Ἀχαρνηίδης', 'PROPN']))
    path = tmp_path / 'fixed.tsv'
    path.write_text('\n'.join(fixed) + '\n', encoding='utf-8')
    fixes = review.read(path)
    assert len(fixes) == 4

    ws = review_words()
    before = review.apply([(w.form, w) for w in ws], fixes)
    assert (ws[1].lemma, ws[1].pos, ws[1].resp) == ('ὁ', 'DET', 'corrector')
    assert (ws[2].lemma, ws[2].pos, ws[2].msd) == ('γοργόλοφος', 'ADJ', None)
    assert ws[0].resp == 'diogenes spacy' and ws[3].crasis[1][1] == 'ἀγαθός'
    assert before[id(ws[1])] == ('ἕ', 'PRON', 'diogenes spacy')
    assert fixes['ὦγαθοί', 'ὦ+ἀγαθός'].refused == 1

    sheet = review.Sheet()                               # the next sheet keeps every correction
    sheet.add('Ach', review_sections(ws), before)
    rows = [r.split('\t') for r in sheet.text(fixes).splitlines()[1:]]
    assert ['οἱ', 'ἕ'] + ['ὁ', 'DET'] in [r[:2] + r[8:] for r in rows]
    assert rows[-1][0] == 'ὦχαρνηίδαι' and rows[-1][4] == review.NOT_FOUND


@pytest.mark.parametrize('content, encoding, message', [
    ('μορφή\tλήμμα\nοἱ\tἕ\n', 'utf-8', 'missing columns'),
    ('μορφή\tλήμμα\tδιορθωμένο λήμμα\tδιορθωμένο μέρος λόγου\n??\t?\t?\t\n', 'utf-8', 'contains \\?'),
    ('μορφή\tλήμμα\tδιορθωμένο λήμμα\tδιορθωμένο μέρος λόγου\nοἱ\tἕ\tὁ\t\n', 'cp1253', 'UTF-8'),
])
def test_corrections_file_errors(tmp_path, content, encoding, message):
    from ag_scaife2antconc import review
    path = tmp_path / 'c.tsv'
    path.write_bytes(content.encode(encoding, errors='replace'))
    with pytest.raises(ValueError, match=message):
        review.read(path)


def test_corrections_from_a_spreadsheet(tmp_path):
    from ag_scaife2antconc import review
    head = 'μορφή;λήμμα;διορθωμένο λήμμα;διορθωμένο μέρος λόγου\n'
    (tmp_path / 'a.csv').write_text(head + 'Οἱ;ἕ;ὁ;\n', encoding='utf-8-sig')       # CSV with ;
    (tmp_path / 'b.txt').write_text(head.replace(';', '\t') + 'οἱ\tἕ\tὁ\t\n', encoding='utf-16')
    for name in ('a.csv', 'b.txt'):
        assert list(review.read(tmp_path / name)) == [('οἱ', 'ἕ')]


def test_legacy_greek_review_sheet_reads_and_preserves_scope_and_crasis(tmp_path):
    from ag_scaife2antconc import review
    header = ['μορφή', 'λήμμα', 'μέρος λόγου', 'εμφανίσεις', 'σημείωση', 'άλλα λήμματα',
              'θέσεις', 'συμφραζόμενα', 'διορθωμένο λήμμα', 'διορθωμένο μέρος λόγου',
              'έργο', 'θέση', 'λέξη', 'χειροκίνητη κράση']
    crasis_json = '{"cut": 1, "forms": ["καί", "ἐγώ"]}'
    rows = [
        '\t'.join(header),
        '\t'.join(['οἱ', 'ὁ', 'DET', '1', '', '', '1', '', 'ἕ', 'PRON', 'Work', '1', '2', '']),
        '\t'.join(['κἀγώ', 'κἀγώ', 'X', '1', '', '', '1', '', 'καί+ἐγώ', 'CCONJ+PRON', 'Work', '1', '3', crasis_json]),
    ]
    path = tmp_path / 'legacy-greek.tsv'
    path.write_text(review.BOM + '\n'.join(rows) + '\n', encoding='utf-8')
    fixes = review.read(path)
    assert ('οἱ', 'ὁ', 'Work', '1', 2) in fixes
    assert fixes['οἱ', 'ὁ', 'Work', '1', 2].lemma == 'ἕ'
    assert fixes['οἱ', 'ὁ', 'Work', '1', 2].pos == 'PRON'
    crasis_fix = fixes['κἀγώ', 'κἀγώ', 'Work', '1', 3]
    assert crasis_fix.lemma == 'καί+ἐγώ' and crasis_fix.pos == 'CCONJ+PRON'
    assert crasis_fix.crasis == review.ManualCrasis(1, ['καί', 'ἐγώ'])


@pytest.mark.skipif(not diogenes.find(), reason='Diogenes is not installed')
def test_review_and_rerun_with_corrections(tmp_path):
    from ag_diogenes_spacy_txt2xml.greberta import verified,default_path
    if not verified(default_path()): pytest.skip('prepared GreBERTa joint model is not installed')
    first = tmp_path / 'first'
    run([ISAEUS], str(first), Options(), log=lambda m: None)
    lines = (first / 'lemma-review.tsv').read_text(encoding='utf-8-sig').splitlines()
    i = next(i for i, l in enumerate(lines) if l.split('\t')[:2] == ['Κλεωνύμου', 'Κλεώνυμος'])
    cells = lines[i].split('\t')
    cells[8] = 'ΚΛΕΩΝΥΜΟΣ'
    lines[i] = '\t'.join(cells)
    fixed = tmp_path / 'fixed.tsv'
    fixed.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    second = tmp_path / 'second'
    report = run([ISAEUS], str(second), Options(xml=True, provenance=True, corrections=str(fixed)),
                 log=lambda m: None)
    tagged = (second / 'antconc-tagged' / 'isaeus-1.txt').read_text(encoding='utf-8')
    assert any(t.startswith('Κλεωνύμου_') and t.endswith('_ΚΛΕΩΝΥΜΟΣ') for t in tagged.split())
    xml = (second / 'xml' / 'isaeus-1.xml').read_text(encoding='utf-8')
    assert 'lemma="ΚΛΕΩΝΥΜΟΣ"' in xml and 'resp="#corrector"' in xml and 'xml:id="corrector"' in xml
    assert 'applied 1 to' in open(report, encoding='utf-8').read()
    kept = (second / 'lemma-review.tsv').read_text(encoding='utf-8-sig')
    assert 'Κλεωνύμου\tΚλεώνυμος' in kept and 'ΚΛΕΩΝΥΜΟΣ' in kept


# -- gold sample -------------------------------------------------------------------------

GOLD_TEI = '''<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><ab>
<milestone unit="line" n="1"/><w lemma="ὁ" resp="#diogenes">ὁ</w> <w lemma="{eur}" resp="#spacy">Εὐριπίδη</w> <w{thym}>θύμ’</w>
<milestone unit="line" n="2"/><seg type="crasis"><w lemma="καί" resp="#lexicon">κ</w><w lemma="ἐντεῦθεν" resp="#diogenes">ἀντεῦθεν</w></seg> <w lemma="λέγω" resp="#diogenes #spacy">λέγει</w>
</ab></body></text></TEI>'''


def test_gold_allocation():
    from ag_scaife2antconc import gold
    assert gold.allocate({'a': 1000, 'b': 10, 'c': 100}, 300) == {'b': 10, 'c': 100, 'a': 190}


def test_gold_sample_and_score(tmp_path):
    from ag_scaife2antconc import gold
    old, new = tmp_path / 'old' / 'Ach.xml', tmp_path / 'new' / 'Ach.xml'
    for path, eur, thym in [(old, 'Εὐριπίδη', ''), (new, 'Εὐριπίδης', ' lemma="θυμός" resp="#diogenes #frequency"')]:
        path.parent.mkdir()
        path.write_text(GOLD_TEI.format(eur=eur, thym=thym), encoding='utf-8')
    words = gold.tokens(str(old))
    assert [(t.cit, t.n, t.form, t.lemma) for t in words][2:4] == [('1', 3, 'θύμ’', ''), ('2', 1, 'κἀντεῦθεν', 'καί+ἐντεῦθεν')]
    assert [gold.group(t, {'ὁ', 'λέγω'}) for t in words] == ['diogenes', 'spacy unknown', None, 'crasis', 'diogenes spacy']

    rows = gold.sample([str(old)], n=10, heads={'ὁ', 'λέγω'})
    assert [r[4] for r in rows] == ['ὁ', 'Εὐριπίδη', 'θύμ’', 'κἀντεῦθεν', 'λέγει']    # all, in text order
    for r, mark in zip(rows, ['+', 'Εὐριπίδης', 'θυμός', '+', '?']):
        r[6] = mark
    sheet = tmp_path / 'gold-sample.tsv'
    sheet.write_text(gold.sheet_text(rows), encoding='utf-8')

    before, after = gold.score(str(sheet), [str(old)]), gold.score(str(sheet), [str(new)])
    assert 'Εκτίμηση για όλο το κείμενο: 50.0%' in before and 'Εὐριπίδη: Εὐριπίδη → Εὐριπίδης' in before
    assert 'Εκτίμηση για όλο το κείμενο: 100.0%' in after and 'Λάθη' not in after
    assert 'ελέγχθηκαν 4, αβέβαιες (?) 1' in after


def test_every_lemma_source_has_a_greek_name():
    from ag_scaife2antconc import pipeline, review
    resps = {'diogenes', 'diogenes spacy', 'diogenes morpho', 'diogenes frequency', 'morpho', 'morpho spacy',
             'morpho frequency', 'spacy'}
    assert resps <= set(pipeline.GREEK) and resps <= set(review.SOURCES)


def test_run_refuses_a_folder_with_other_works(tmp_path):
    quiet = lambda m: None
    run([ISAEUS], str(tmp_path), Options(), log=quiet, lemmatizer=FAKE)
    run([ISAEUS], str(tmp_path), Options(), log=quiet, lemmatizer=FAKE)        # the same work again: fine
    with pytest.raises(SetupError, match='isaeus-1'):
        run([ACHARNIANS], str(tmp_path), Options(), log=quiet, lemmatizer=FAKE)
    assert os.listdir(tmp_path / 'antconc-tagged') == ['isaeus-1.txt']   # untouched
    run([ACHARNIANS], str(tmp_path), Options(replace=True), log=quiet, lemmatizer=FAKE)
    assert os.listdir(tmp_path / 'antconc-tagged') == ['aristophanes-acharnians.txt']


@pytest.mark.parametrize('books', [(1,), (1, 3)])
def test_rerun_detects_and_replaces_obsolete_book_files(tmp_path, books):
    from ag_scaife2antconc.pipeline import expected_results, old_results, plan

    source = tei(tmp_path, ''.join(f'<div n="{n}"><l n="1">λόγος</l></div>' for n in (1, 2)))
    out = tmp_path / 'out'
    quiet = lambda m: None
    run([source], str(out), Options(xml=True), log=quiet, lemmatizer=FAKE)
    source = tei(tmp_path, ''.join(f'<div n="{n}"><l n="1">ἀνήρ</l></div>' for n in books))
    expected = expected_results(plan([source]), Options())
    obsolete = old_results(str(out), expected)
    assert os.path.join('antconc-tagged', 'in-2.txt') in obsolete
    assert os.path.join('xml', 'in.xml') in obsolete
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    with pytest.raises(SetupError, match='--replace'):
        run([source], str(out), Options(), log=quiet, lemmatizer=FAKE)
    assert {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()} == before
    run([source], str(out), Options(replace=True), log=quiet, lemmatizer=FAKE)
    assert sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()) == sorted(
        expected + ['analysis-review.jsonl', 'lemma-review.tsv', 'report.txt'])
    assert old_results(str(out), expected) == []


def test_gui_asks_about_obsolete_book_files(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ag_scaife2antconc import gui

    source = tei(tmp_path, '<div n="1"><l n="1">λόγος</l></div><div n="2"><l n="1">ἀνήρ</l></div>')
    out = tmp_path / 'out'
    run([source], str(out), Options(), log=lambda m: None, lemmatizer=FAKE)
    source = tei(tmp_path, '<div n="1"><l n="1">λόγος</l></div>')
    prompts = []

    def accept(title, message, **kwargs):
        prompts.append(message)
        return True
    monkeypatch.setattr(gui.messagebox, 'askyesno', accept)
    app = SimpleNamespace(outdir=SimpleNamespace(get=lambda: str(out)),
                          root=SimpleNamespace(config=lambda **kw: None, update=lambda: None))
    opts = Options()
    assert gui.App.ask_replace(app, [source], opts)
    assert opts.replace and len(prompts) == 1
    assert 'in-2.txt' in prompts[0]


def test_failed_obsolete_book_cleanup_restores_the_previous_run(tmp_path, monkeypatch):
    from ag_scaife2antconc import pipeline

    source = tei(tmp_path, ''.join(f'<div n="{n}"><l n="1">λόγος</l></div>' for n in (1, 2, 3)))
    out = tmp_path / 'out'
    quiet = lambda m: None
    run([source], str(out), Options(), log=quiet, lemmatizer=FAKE)
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    source = tei(tmp_path, ''.join(f'<div n="{n}"><l n="1">ἀνήρ</l></div>' for n in (1, 2)))
    real_remove = pipeline.os.remove

    def locked_book(path):
        if str(path) == str(out / 'antconc-tagged' / 'in-3.txt'):
            raise PermissionError('book is open')
        real_remove(path)
    monkeypatch.setattr(pipeline.os, 'remove', locked_book)
    with pytest.raises(SetupError, match='preserved'):
        run([source], str(out), Options(replace=True), log=quiet, lemmatizer=FAKE)
    assert {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()} == before
    assert not list(tmp_path.glob('.ag-scaife2antconc-*'))


@pytest.mark.parametrize('failure', ['setup', 'lemmatize', 'stage', 'publish', 'delete', 'invalid', 'empty'])
def test_failed_replacement_preserves_previous_results(tmp_path, monkeypatch, failure):
    from ag_scaife2antconc import pipeline

    out = tmp_path / 'out'
    quiet = lambda m: None
    run([ISAEUS], str(out), Options(xml=True), log=quiet, lemmatizer=FAKE)
    (out / 'notes.txt').write_text('keep this unrelated file', encoding='utf-8')
    # The output sheet may also be the corrections input. A late failure must restore it too.
    sheet = out / 'lemma-review.tsv'
    lines = sheet.read_text(encoding='utf-8-sig').splitlines()
    cells = lines[1].split('\t')
    cells[8] = 'διορθωμένο'
    lines[1] = '\t'.join(cells)
    sheet.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    opts = Options(xml=True, replace=True, corrections=str(sheet))
    paths = [ACHARNIANS, ACHARNIANS]
    lemmatizer = FAKE

    if failure == 'setup':
        def unavailable(*args):
            raise SetupError('model missing')
        monkeypatch.setattr(pipeline, 'Lemmatizer', unavailable)
        lemmatizer = None
    elif failure == 'lemmatize':
        class FailingLemmatizer(FakeLemmatizer):
            calls = 0

            def __call__(self, tokens, words):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError('second work failed')
                return super().__call__(tokens, words)
        lemmatizer = FailingLemmatizer()
    elif failure == 'stage':
        real_write = pipeline.write

        def full_disk(path, text):
            if os.path.basename(path) == 'report.txt':
                raise OSError('disk full')
            real_write(path, text)
        monkeypatch.setattr(pipeline, 'write', full_disk)
    elif failure == 'publish':
        real_replace = pipeline.os.replace

        def locked_report(source, target):
            if os.path.basename(target) == 'report.txt':
                raise PermissionError('report is open')
            real_replace(source, target)
        monkeypatch.setattr(pipeline.os, 'replace', locked_report)
    elif failure == 'delete':
        paths = [ACHARNIANS]
        real_remove = pipeline.os.remove

        def locked_xml(path):
            if str(path) == str(out / 'xml' / 'isaeus-1.xml'):
                raise PermissionError('XML is open')
            real_remove(path)
        monkeypatch.setattr(pipeline.os, 'remove', locked_xml)
    else:
        source = tmp_path / 'bad.xml'
        source.write_text('<TEI>' if failure == 'invalid' else TEI.format(''), encoding='utf-8')
        paths = [str(source)]

    with pytest.raises((SetupError, RuntimeError, OSError)):
        run(paths, str(out), opts, log=quiet, lemmatizer=lemmatizer)
    assert {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()} == before
    assert not list(tmp_path.glob('.ag-scaife2antconc-*'))


@pytest.mark.parametrize('reverse', [False, True])
def test_book_filenames_do_not_overwrite_another_input(tmp_path, reverse):
    from ag_scaife2antconc import review

    books, second = tmp_path / 'books.xml', tmp_path / 'second.xml'
    books.write_text(TEI.format('<div n="1"><l n="1">ἀνήρ</l></div>'
                                '<div n="2"><l n="1">γυνή</l></div>'), encoding='utf-8')
    second.write_text(TEI.format('<div n="1"><p>λόγος</p></div>'), encoding='utf-8')
    paths = [str(books), str(second)]
    if reverse:
        paths.reverse()
    out = tmp_path / 'out'
    report = run(paths, str(out), Options(xml=True), log=lambda m: None, lemmatizer=FAKE)
    files = list((out / 'antconc-tagged').glob('*.txt'))
    assert len(files) == 3
    assert sorted(form for path in files for line in forms_of(path) for form in line.split()) == [
        'γυνή', 'λόγος', 'ἀνήρ']
    assert len(list((out / 'xml').glob('*.xml'))) == 2
    rows = review.rows_of(out / 'lemma-review.tsv')[1:]
    assert {r[0]: int(r[3]) for r in rows} == {'ἀνήρ': 1, 'γυνή': 1, 'λόγος': 1}
    text = open(report, encoding='utf-8').read()
    assert '.ag-scaife2antconc-stage-' not in text
    assert all(os.path.relpath(path, out) in text for path in files)
    # A second run must use the same names and must not mistake its own files for other works.
    first = {p.relative_to(out): p.read_bytes() for p in files}
    run(paths, str(out), Options(xml=True), log=lambda m: None, lemmatizer=FAKE)
    assert {p.relative_to(out): p.read_bytes() for p in (out / 'antconc-tagged').glob('*.txt')} == first


def test_failed_rollback_keeps_recovery_copies(tmp_path, monkeypatch):
    from ag_scaife2antconc import pipeline

    out = tmp_path / 'out'
    run([ISAEUS], str(out), Options(), log=lambda m: None, lemmatizer=FAKE)
    original = (out / 'lemma-review.tsv').read_bytes()
    real_replace = pipeline.os.replace

    def locked_files(source, target):
        if os.path.basename(target) == 'report.txt' or (
                '.ag-scaife2antconc-backup-' in str(source) and os.path.basename(target) == 'lemma-review.tsv'):
            raise PermissionError('file is open')
        real_replace(source, target)
    monkeypatch.setattr(pipeline.os, 'replace', locked_files)
    with pytest.raises(SetupError, match='kept in') as error:
        run([ACHARNIANS], str(out), Options(replace=True), log=lambda m: None, lemmatizer=FAKE)
    backups = list(tmp_path.glob('.ag-scaife2antconc-backup-*'))
    assert len(backups) == 1
    assert (backups[0] / 'lemma-review.tsv').read_bytes() == original
    assert str(backups[0]) in str(error.value)
    assert not list(tmp_path.glob('.ag-scaife2antconc-stage-*'))


def test_run_keeps_a_review_sheet_with_corrections(tmp_path):
    from ag_scaife2antconc import review
    from ag_scaife2antconc.pipeline import unsaved_corrections
    sheet = tmp_path / 'lemma-review.tsv'
    row = ['ἀνήρ', 'ἀνήρ', 'NOUN', '1', '', '', '1', '', 'ἀνήρ2', '']
    sheet.write_text('\t'.join(review.COLUMNS) + '\n' + '\t'.join(row) + '\n', encoding='utf-8')
    opts = Options()
    with pytest.raises(SetupError, match='lemma-review.tsv'):
        run([ISAEUS], str(tmp_path), opts, log=lambda m: None, lemmatizer=FAKE)
    assert unsaved_corrections(str(tmp_path), Options(corrections=str(sheet))) is None
    sheet.write_text('\t'.join(review.COLUMNS) + '\n', encoding='utf-8')     # no corrections: may be replaced
    assert unsaved_corrections(str(tmp_path), opts) is None


def test_tagged_text_for_antconc():
    """form_POS_lemma, as AntConc's simple_word_pos_headword_indexer splits a token at _."""
    from ag_scaife2antconc.pipeline import HAIR_SPACE, tagged
    assert tagged('τὴν', Word([('t', 'τὴν')], lemma='ὁ', pos='DET')) == 'τὴν_DET_ὁ'
    crasis = Word([('t', 'κἀγώ')], crasis=[('κ', 'καί', 'CCONJ', 'lexicon'), ('ἀγώ', 'ἐγώ', 'PRON', 'lexicon')])
    assert tagged('κἀγώ', crasis) == 'κ_CCONJ_καί' + HAIR_SPACE + 'ἀγώ_PRON_ἐγώ'      # two words, one written form
    elided = Word([('t', 'κᾆτ' + chr(0x2019))], crasis=[('κ', 'καί', 'CCONJ', 'lexicon'), ('ᾆτ' + chr(0x2019), 'εἶτα', 'ADV', 'spacy')])
    assert tagged("κᾆτ'", elided) == 'κ_CCONJ_καί' + HAIR_SPACE + "ᾆτ'_ADV_εἶτα"       # elision mark as in the text
    odd = Word([('t', 'χὤ')], crasis=[('χ', 'καί', 'CCONJ', 'lexicon'), ('ὁ', 'ὁ', 'DET', 'lexicon')])
    assert tagged('χὤ', odd) == 'χὤ_DET_ὁ'                    # pieces do not spell the form: one token
    assert tagged("ἐφόδι'", Word([('t', "ἐφόδι'")])) == "ἐφόδι'_X_NOLEMMA"
    assert tagged('a_b', Word([('t', 'a_b')], lemma='c_d', pos='X')).split('_') == ['a-b', 'X', 'c-d']


@pytest.mark.skipif(not diogenes.find(), reason='Diogenes is not installed')
def test_automatic_analysis_never_asks_morpho_even_with_old_options(monkeypatch):
    from ag_diogenes_spacy_txt2xml import morpho
    from ag_diogenes_spacy_txt2xml.greberta import verified, default_path
    from ag_scaife2antconc import cli, pipeline
    if not verified(default_path()): pytest.skip('prepared GreBERTa joint model is not installed')

    class Network:
        def __init__(self, *args, **kwargs):
            pytest.fail('analysis must not create a Morpho client')
    monkeypatch.setattr(morpho, 'Morpho', Network)
    seen = {}

    class Stop(Exception):
        pass

    def contextual(store, greta, context, morpho_client, log):
        seen['morpho'] = morpho_client
        raise Stop
    from ag_diogenes_spacy_txt2xml import contextual as contextual_module
    monkeypatch.setattr(contextual_module, 'Contextual', contextual)
    with pytest.raises(Stop):
        pipeline.Lemmatizer(Options(morpho=True), log=lambda m: None)   # saved options from <= 0.7.1
    assert seen == {'morpho': None}
    calls = []
    monkeypatch.setattr(cli, 'run', lambda paths, outdir, opts, **kw: calls.append(opts) or 'report.txt')
    try:
        cli.main([ISAEUS, '-o', 'out', '--morpho'])                     # old command lines still parse
    except SystemExit as stop:
        assert not stop.code
    assert calls and not calls[0].morpho


def test_exclamation_mark_is_punctuation_while_gap_excludes_damaged_word(tmp_path):
    xml = tei(tmp_path, '<l n="1">Ζεῦ! φεῦ! ἀν<gap extent="1" unit="letter"/>πος</l>')
    out = tmp_path / 'out'
    run([xml], str(out), Options(), log=lambda m: None, lemmatizer=FAKE)
    assert forms_of(out / 'antconc-tagged' / 'in.txt') == ['Ζεῦ φεῦ']


def test_antilabe_propagates_line_citation(tmp_path):
    import json
    xml = tei(tmp_path, '<sp><l n="10" part="I">πρῶτον</l></sp><sp><l part="F">δεύτερον</l></sp>')
    out = tmp_path / 'out'
    run([xml], str(out), Options(), log=lambda m: None, lemmatizer=FAKE)
    rows = [json.loads(line) for line in (out / 'analysis-review.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [(r['form'], r['citation']) for r in rows] == [('πρῶτον', '10'), ('δεύτερον', '10')]


def test_split_verse_without_number_continues_its_line_and_keeps_ids_unique(tmp_path):
    from ag_scaife2antconc.workspace import Workspace, identity
    from .test_workspace import Engine
    xml = tei(tmp_path, '<div n="1"><sp><l n="5">οἱ λόγοι</l></sp><sp><l part="F">οἱ ἄνθρωποι</l></sp></div>')
    out = tmp_path / 'out'
    run([xml], str(out), Options(session=True), log=lambda m: None, lemmatizer=Engine())
    ids = [identity(r) for r in Workspace(out).records]
    assert len(ids) == len(set(ids)) == 4
    assert forms_of(out / 'antconc-tagged' / 'in.txt') == ['οἱ λόγοι οἱ ἄνθρωποι']


def test_page_break_separates_words_unless_break_no_and_gold_empty_crasis(tmp_path):
    from ag_scaife2antconc import gold
    xml = tei(tmp_path, '<div n="1"><p>λόγος<pb n="2"/>ἀνήρ ἀν<pb break="no"/>δρός</p></div>')
    out = tmp_path / 'out'
    run([xml], str(out), Options(), log=lambda m: None, lemmatizer=FAKE)
    assert forms_of(out / 'antconc-tagged' / 'in.txt') == ['λόγος ἀνήρ ἀνδρός']
    empty_crasis = tmp_path / 'empty-crasis.xml'
    empty_crasis.write_text(
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><p>'
        '<milestone unit="line" n="1"/><seg type="crasis"></seg><w lemma="λόγος">λόγος</w>'
        '</p></body></text></TEI>', encoding='utf-8')
    assert [t.form for t in gold.tokens(str(empty_crasis))] == ['λόγος']


def test_module_routes_options_after_input_to_cli(monkeypatch):
    import runpy
    import sys
    from ag_scaife2antconc import cli, gui
    opened = []
    monkeypatch.setattr(cli, 'main', lambda: opened.append('cli'))
    monkeypatch.setattr(gui, 'main', lambda: opened.append('workbench'))
    for args in (['work.xml', '-o', 'result'], ['work.xml']):
        monkeypatch.setattr(sys, 'argv', ['ag_scaife2antconc', *args])
        runpy.run_module('ag_scaife2antconc.__main__', run_name='__main__')
    assert opened == ['cli', 'workbench']


def test_lemma_text_replaces_each_tagged_token_by_its_lemma(tmp_path):
    from ag_scaife2antconc.pipeline import lemmatized, tagged_tokens
    crasis = Word([('t', 'κἀγώ')], crasis=[('κ', 'καί', 'CCONJ', 'lexicon'), ('ἀγώ', 'ἐγώ', 'PRON', 'lexicon')])
    assert lemmatized('κἀγώ', crasis) == 'καί ἐγώ'                      # two words, as two tagged tokens
    assert len(tagged_tokens('κἀγώ', crasis)) == 2
    assert lemmatized('λόγος', Word([('t', 'λόγος')], lemma='λόγος', pos='NOUN')) == 'λόγος'
    assert lemmatized('ξ', Word([('t', 'ξ')])) == 'NOLEMMA'
    run([ACHARNIANS], str(tmp_path), Options(), log=lambda m: None, lemmatizer=FAKE)
    tagged_lines = (tmp_path / 'antconc-tagged' / 'aristophanes-acharnians.txt').read_text(encoding='utf-8').splitlines()
    lemma_lines = (tmp_path / 'antconc-lemmata' / 'aristophanes-acharnians.txt').read_text(encoding='utf-8').splitlines()
    assert len(lemma_lines) == len(tagged_lines)
    from ag_scaife2antconc.pipeline import HAIR_SPACE
    for t, l in zip(tagged_lines, lemma_lines):
        assert l.split(' ') == [token.rsplit('_', 1)[1] for word in t.split(' ') for token in word.split(HAIR_SPACE)]
    from ag_scaife2antconc.pipeline import lemmata_of_tagged
    assert lemmata_of_tagged((tmp_path / 'antconc-tagged' / 'aristophanes-acharnians.txt').read_text(encoding='utf-8')) == (
        tmp_path / 'antconc-lemmata' / 'aristophanes-acharnians.txt').read_text(encoding='utf-8')
