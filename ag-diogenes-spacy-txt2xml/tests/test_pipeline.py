import unicodedata
import xml.etree.ElementTree as ET

import pytest

from ag_diogenes_spacy_txt2xml.crasis import Crasis
from ag_diogenes_spacy_txt2xml.normalize import Normalizer
from ag_diogenes_spacy_txt2xml.source import add_milestones, dehyphenate, parse_line
from ag_diogenes_spacy_txt2xml.tei import body, document
from ag_diogenes_spacy_txt2xml.tokenize import Word, tokenize

NS = '{http://www.tei-c.org/ns/1.0}'


def lines_of(*rows, apostrophe='’'):
    lines = [parse_line(r) for r in rows]
    add_milestones(lines)
    norm = Normalizer(apostrophe)
    for line in lines:
        line.text = norm(line.text)
    return dehyphenate(lines)


# --- normalization, milestones, tokens (same code as AG-LLeMmatizer) ----------

def test_normalization():
    n = Normalizer()
    assert n('ἄν ά') == 'ἄν ά'
    assert unicodedata.is_normalized('NFC', n('ά̓'))
    assert n('᾿Αθηναῖοι') == 'Ἀθηναῖοι'
    assert n('ἔφη· οἶδ´ ὅτι „ναί“') == 'ἔφη· οἶδ’ ὅτι «ναί»'


def test_segment_milestones_only_on_change():
    lines = lines_of('1   Or1.1.1 : α', '2   Or1.1.2 : β', '3   Or1.2.1 : γ')
    assert [[(m.unit, m.n) for m in line.milestones] for line in lines] == [
        [('segment', 'Or1'), ('segment', 'Or1.1'), ('line', 'Or1.1.1')],
        [('line', 'Or1.1.2')],
        [('segment', 'Or1.2'), ('line', 'Or1.2.1')]]


def test_hyphenated_word_moves_up_with_its_punctuation():
    lines = lines_of('1   Or1.1.2 : πράγματος γενέ–', '2   Or1.1.3 : σθαι, οἷοίπερ', '3   1.4 : ἦν δια–',
                     '4   1.5 : κεκρι–', '5   1.6 : μένος. καὶ')
    assert [line.text for line in lines] == ['πράγματος γενέσθαι,', 'οἷοίπερ', 'ἦν διακεκριμένος.', '', 'καὶ']


def test_tokens():
    toks = tokenize(lines_of('1   1.1 : [ἀγαθὴ] γενέ–', '2   1.2 : σθαι !!!ντο 12 λʹ Harp. δ̣ε̣'))
    words = [t for t in toks if isinstance(t, Word)]
    assert [(w.form, w.lang) for w in words] == [
        ('ἀγαθὴ', 'grc'), ('γενέσθαι', 'grc'), ('…ντο', 'grc'), ('Harp', 'la'), ('δε', 'grc')]


@pytest.mark.parametrize('apostrophe', ['’', "'", 'ʼ'])
def test_chosen_apostrophe_stays_in_the_word(apostrophe):
    toks = tokenize(lines_of('1   1.1 : οἶδ´ ὅτι', apostrophe=apostrophe), apostrophe)
    assert [t.form for t in toks if isinstance(t, Word)] == [f'οἶδ{apostrophe}', 'ὅτι']


# --- crasis lexicon and rule ---------------------------------------------------

@pytest.fixture(scope='module')
def crasis():
    return Crasis.load()


@pytest.mark.parametrize('form, cut, lemmas', [
    ('κἀγὼ', 1, ['καί', 'ἐγώ']),
    ('Κἀγὼ', 1, ['καί', 'ἐγώ']),
    ('κἂν', 1, ['καί', 'ἄν']),           # with accents: κἄν ≠ κἀν
    ('κἀν', 1, ['καί', 'ἐν']),
    ('κἀκείνῳ', 1, ['καί', 'ἐκεῖνος']),  # prefix entry κ|ἀκειν*
    ('ταὐτὰ', 1, ['ὁ', 'αὐτός']),
    ('ταὔτ’', 1, ['ὁ', 'αὐτός']),
    ('τοὔνομα', 1, ['ὁ', 'ὄνομα']),
    ('ἁνὴρ', 1, ['ὁ', 'ἀνήρ']),
    ('ἐγᾦδα', 2, ['ἐγώ', 'οἶδα']),
    ('ὦγαθοί', 1, ['ὦ', 'ἀγαθός']),    # ὦ + vocative: prefix entry ὦ|γαθ*
    ('ὦγάθʼ', 1, ['ὦ', 'ἀγαθός']),
    ('ὠγαθέ', 1, ['ὦ', 'ἀγαθός']),     # written without the accent on ω
    ('ὦκβάτανα', 1, ['ὦ', 'Ἐκβάτανα']),
    ('ὦχαρνικοί', 1, ['ὦ', 'Ἀχαρνικός']),
])
def test_crasis_lexicon(crasis, form, cut, lemmas):
    assert crasis.match(form) == (cut, lemmas)


@pytest.mark.parametrize('form', ['ταῦτα', 'ταύτην', 'αὑτὸν', 'προὔδωκεν', 'καὶ', 'τοῦ', '…ἀκηκόατε', 'Ἀθηναῖοι',
                                  'ὠδυνήθην', 'ὤμοσε', 'ὦσι'])
def test_not_crasis(crasis, form):
    assert crasis.match(form) is None


def test_crasis_by_coronis_rule(crasis):
    assert crasis.match('κἀπορεῖ') == (1, ['καί', None])     # not in the lexicon
    assert crasis.match('τοὐλάχιστον') == (1, ['ὁ', None])


# --- TEI -------------------------------------------------------------------------

SOURCES = [('diogenes', 'only lemma', 'Diogenes'), ('spacy', 'context', 'spaCy'),
           ('frequency', 'frequency', 'app'), ('lexicon', 'tables', 'app')]


def annotated():
    toks = tokenize(lines_of('1   Or1.5.3 : τἀληθῆ· ταύτην'))
    words = [t for t in toks if isinstance(t, Word)]
    words[0].crasis = [('τ', 'ὁ', 'DET', 'lexicon'), ('ἀληθῆ', 'ἀληθής', 'ADJ', 'lexicon')]
    words[1].lemma, words[1].pos, words[1].msd = 'οὗτος', 'DET', 'Case=Acc|Gender=Fem|Number=Sing'
    words[1].resp = 'diogenes spacy'
    return toks


@pytest.mark.parametrize('attrs', [set(), {'pos', 'msd'}, {'resp'}, {'pos', 'msd', 'resp'}])
def test_tei_writes_only_the_chosen_attributes(attrs):
    root = ET.fromstring(document(annotated(), 't', 't.txt', 'test', 'NFC', attrs, SOURCES).encode())
    w = [w for w in root.iter(f'{NS}w') if w.text == 'ταύτην'][0]
    expected = {'lemma': 'οὗτος', 'pos': 'DET', 'msd': 'Case=Acc|Gender=Fem|Number=Sing',
                'resp': '#diogenes #spacy'}
    assert dict(w.attrib) == {k: v for k, v in expected.items() if k == 'lemma' or k in attrs}
    seg = root.find(f'.//{NS}seg[@type="crasis"]')
    assert [(p.text, p.get('lemma')) for p in seg] == [('τ', 'ὁ'), ('ἀληθῆ', 'ἀληθής')]
    # @resp points to <respStmt xml:id> in the header, which is there only with @resp
    ids = {r.get('{http://www.w3.org/XML/1998/namespace}id') for r in root.iter(f'{NS}respStmt')}
    assert ids == ({'diogenes', 'spacy', 'frequency', 'lexicon'} if 'resp' in attrs else set())


def test_titles_become_seg():
    xml = body(tokenize(lines_of('1   Or1.t.1 : {1ΥΠΕΡ ΤΟΥ', '2   Or1.t.2 : ΦΟΝΟΥ}1')))
    assert xml.count('<seg type="title">') == 1 and xml.count('</seg>') == 1


# --- with OdyCy (skipped where it is not installed) ---------------------------------

def test_odycy_end_to_end():
    pytest.importorskip('spacy')
    from ag_diogenes_spacy_txt2xml.lemmatize import SpacyLemmatizer
    try:
        lem = SpacyLemmatizer()
    except OSError:
        pytest.skip('grc_odycy_joint_sm not installed')
    toks = tokenize(lines_of('1   Or1.5.3 : τἀληθῆ· ταύτην γὰρ ἐμαυτῷ', '2   Or1.5.4 : δ’ ἐγὼ δι’ ὑμῶν {1ΝΟΜΟΣ}1'))
    lem.run(toks)
    words = {w.form: w for w in toks if isinstance(w, Word)}
    assert [p[1] for p in words['τἀληθῆ'].crasis] == ['ὁ', 'ἀληθής']
    assert words['δ’'].lemma == 'δέ' and words['δ’'].pos != 'VERB'
    assert words['δι’'].lemma == 'διά'
    assert words['γὰρ'].lemma == 'γάρ'                      # grave turned into acute
    assert words['ΝΟΜΟΣ'].lemma is None                     # capitals without accents: no guess


# --- Diogenes -------------------------------------------------------------------------

# real lines of dependencies/data/greek-analyses.txt (glosses shortened)
ANALYSES = '''d'\t{21489648 9 de/,de/\tbut\tindeclform (particle)}
e)moi/xeuen\t{65646623 9 moixeu/w\tcommit adultery with\timperf ind act 3rd sg}
h(gou/mhn\t{428975 9 a(ge/omai\tcustom\timperf ind mp 1st sg}{44128412 9 h(ge/omai\tlead\timperf ind mid 1st sg}
h(gei=tai\t{44128412 9 h(ge/omai\tlead\tpres ind mp 3rd sg}
*)eratosqe/nhs\t{39362958 0 *)eratosqe/nhs\t \tmasc nom sg}
w)=\t{30209543 9 w)=\tO\tindeclform (exclam)}{30209543 9 ei)mi/\tbe\tpres subj act 1st sg}
!h/kw\t{44357879 9 h(/kw,h(/kw\tto have come\tpres subj act 1st sg}
e)/xeis\t{1 9 e)/xw2\thave\tpres ind act 2nd sg}
e)fu/latton\t{108868522 9 fula/ssw\tkeep watch\timperf ind act 3rd pl}{108868522 9 fula/ssw\tkeep watch\timperf ind act 1st sg}
prose/rxetai\t{88114233 9 prose/rxomai\tcome or go to\tpres ind mp 3rd sg}
no/mon\t{67935223 9 no/mos\tthat which is in habitual practice, use\tmasc acc sg}
*no/mon\t{67935223 6 *no/mos\tthat which is in habitual practice, use\tmasc acc sg}
'''


@pytest.fixture
def table(tmp_path):
    from ag_diogenes_spacy_txt2xml import diogenes
    path = tmp_path / 'greek-analyses.txt'
    path.write_text(ANALYSES, encoding='utf-8')
    return diogenes.load(str(path), cache_dir=str(tmp_path / 'cache'))


def test_diogenes_table(table):
    assert table["δ'"] == ('δέ',)
    assert table['ἐμοίχευεν'] == ('μοιχεύω',)
    assert table['ἡγούμην'] == ('ἁγέομαι', 'ἡγέομαι')
    assert table['ἐρατοσθένης'] == ('Ἐρατοσθένης',)        # keys lower case, lemmas as written
    assert table['ἔχεις'] == ('ἔχω',)                      # homograph number dropped
    assert not any(k.startswith('!') for k in table)


def test_enclitic_accent(table):
    from ag_diogenes_spacy_txt2xml import diogenes
    assert diogenes.without_enclitic_accent('ἐφύλαττόν') == 'ἐφύλαττον'
    assert diogenes.without_enclitic_accent('προσέρχεταί') == 'προσέρχεται'
    assert diogenes.without_enclitic_accent('ἄνθρωπός') == 'ἄνθρωπος'
    assert diogenes.without_enclitic_accent('ἐφύλαττον') is None          # one accent: nothing to drop
    assert diogenes.lookup(table, 'ἐφύλαττόν') == ('φυλάσσω',)
    assert diogenes.lookup(table, 'προσέρχεταί') == ('προσέρχομαι',)
    assert diogenes.lookup(table, 'Ξανθίας') == ()


def test_lower_case_word_is_not_a_name(table):
    from ag_diogenes_spacy_txt2xml import diogenes
    assert diogenes.lookup(table, 'νόμον') == ('Νόμος', 'νόμος')   # ΝΟΜΟΝ and νόμον share a key
    assert diogenes.by_case('νόμον', ('Νόμος', 'νόμος')) == ('νόμος',)
    assert diogenes.by_case('Νόμον', ('Νόμος', 'νόμος')) == ('Νόμος',)            # a capital marks a name
    assert diogenes.by_case('Φίλιππον', ('Φίλιππος', 'φίλιππος')) == ('Φίλιππος',)
    assert diogenes.by_case('Φίλιππον', ('φίλιππος',)) == ('φίλιππος',)            # no name to choose: kept
    assert diogenes.by_case('ΝΟΜΟΝ', ('Νόμος', 'νόμος')) == ('Νόμος', 'νόμος')    # all capitals: a heading
    assert diogenes.by_case('ἐρατοσθένης', ('Ἐρατοσθένης',)) == ('Ἐρατοσθένης',)
    w = Word([('t', 'νόμον')], lemma='νόμος')                    # spaCy's lemma
    diogenes.choose([w], table)
    assert (w.lemma, w.resp) == ('νόμος', 'diogenes')


@pytest.mark.parametrize('morpheus, headword', [
    ('περί-εἶδον', 'περιεῖδον'), ('ὑπέρ-εἶδον', 'ὑπερεῖδον'), ('σύν-λέγω', 'συλλέγω'),
    ('διά-λέγω', 'διαλέγω'), ('κατά-χράω', 'καταχράω'), ('ἀπό-ἐρῶ', 'ἀπερῶ'),
    ('ἐκ-ἁμαρτέω', 'ἐξαμαρτέω'), ('πρόσ-ἀπογράφω', 'προσαπογράφω'), ('ἐπί-τροπεύω', 'ἐπιτροπεύω'),
    ('ἀντί-δικάζω', 'ἀντιδικάζω'), ('ἐν-εἰμί', 'ἔνειμι'), ('πάρ-εἰμί', 'πάρειμι'),
    ('ἀπό-εἰμί', 'ἄπειμι'), ('κατά-ἵστημι', 'καθίστημι'), ('σύν-βαίνω', 'συμβαίνω'),
    ('κατά-καθεύδω', 'καθεύδω'), ('ἀμφί-ἀμφισβητέω', 'ἀμφισβητέω'), ('διά-διαιτάω', 'διαιτάω'),
    ('διά-ὁμιλέω', 'διομιλέω'), ('διά-ῥώννυμι', 'διαρρώννυμι'), ('διά-εἰμί', 'δίειμι'), ('λέγω', 'λέγω'),
])
def test_compound_headword(morpheus, headword):
    from ag_diogenes_spacy_txt2xml import diogenes
    assert diogenes.compound(morpheus) == headword


def test_diogenes_choose(table):
    from ag_diogenes_spacy_txt2xml import diogenes
    toks = tokenize(lines_of('1   1.1 : ἐμοίχευεν ὦ ἡγεῖται ἡγούμην δ’ Ξανθίας'))
    words = [t for t in toks if isinstance(t, Word)]
    spacy_says = ['ἐμοίχευεν', 'ὦ', 'ἡγεῖται', 'ἡγούμην', 'δέ', 'Ξανθίας']
    for w, lemma in zip(words, spacy_says):
        w.lemma = lemma
    diogenes.choose(words, table)
    assert [(w.lemma, w.resp) for w in words] == [
        ('μοιχεύω', 'diogenes'),             # one candidate: Diogenes, even against spaCy
        ('ὦ', 'diogenes spacy'),             # several (ὦ, εἰμί): spaCy's is one of them
        ('ἡγέομαι', 'diogenes'),
        ('ἡγέομαι', 'diogenes frequency'),   # spaCy's is not among them: the one seen unambiguously
        ('δέ', 'diogenes'),                  # elided form, ASCII apostrophe in the table
        ('Ξανθίας', 'spacy'),                # unknown to Diogenes
    ]


# Morpheus' own analysis of an elided form can miss the right lemma (ἀγάθʼ: not ἀγαθός)
MORE_ANALYSES = '''a)ga/q'\t{1 9 a)gasto/s\tadmirable\tneut nom/voc/acc pl}{2 9 a)gato/s\tgood\tneut pl}
a)gaqa/\t{3 9 a)gaqo/s\tgood\tneut nom/voc/acc pl}
e)nteu=qen\t{4 9 e)nteu=qen\thence\tadverb}
e)/pos\t{5 9 e)/pos\tword\tneut nom/voc/acc sg}
au(=tai\t{6 9 ou(=tos\tthis\tfem nom pl}
*eu)ripi/dhs\t{7 0 *eu)ripi/dhs\t \tmasc nom sg}
u(mw=n\t{8 9 su/\tyou\tpl gen}{9 9 u(mo/s\tyour\tmasc/fem/neut gen pl}
soi\t{8 9 su/\tyou\tsg dat}
'''


def test_unelided_and_uncontracted():
    from ag_diogenes_spacy_txt2xml import diogenes
    assert {'ἀγάθα', 'ἀγαθά', 'ἀγαθέ', 'ἀγαθοί'} <= set(diogenes.unelided('ἀγάθ’'))
    assert 'ἐσθῆτα' in diogenes.unelided('ἐσθῆτ’') and 'ἐσθήτα' not in diogenes.unelided('ἐσθῆτ’')
    assert diogenes.unelided('ἀγαθά') == []
    assert 'ἔπος' in diogenes.uncontracted('τ', 'οὔπος')
    assert 'ἐντεῦθεν' in diogenes.uncontracted('κ', 'ἀντεῦθεν')
    assert 'ὀφθαλμώ' in diogenes.uncontracted('τ', 'ὠφθαλμώ')
    assert 'ἱμάτιον' in diogenes.uncontracted('θ', 'οἰμάτιον')      # θ: the second word had a rough breathing
    assert 'αὗται' in diogenes.uncontracted('χ', 'αὖται')
    assert 'ἐς' in diogenes.uncontracted('κ', 'ἀς') and 'ὡς' not in diogenes.uncontracted('κ', 'ἀς')


def test_elision_crasis_and_headword_repair(tmp_path):
    from ag_diogenes_spacy_txt2xml import diogenes
    path = tmp_path / 'greek-analyses.txt'
    path.write_text(ANALYSES + MORE_ANALYSES, encoding='utf-8')
    table = diogenes.load(str(path), cache_dir=str(tmp_path / 'cache'))
    elided = Word([('t', 'ἀγάθ’')])                                 # spaCy: no lemma for it
    name = Word([('t', 'Εὐριπίδη')], lemma='Εὐριπίδη')               # spaCy's lemma lacks the ς
    unknown = Word([('t', 'κοΐ')], lemma='κοΐ')
    crases = [Word([('t', f)], crasis=[(f[0], first, 'X', 'lexicon'), (f[1:], f[1:], 'X', 'spacy')])
              for f, first in [('τοὔπος', 'ὁ'), ('κἀντεῦθεν', 'καί'), ('χαὖται', 'καί'), ('κἀβόων', 'καί')]]
    stats = diogenes.choose([elided, name, unknown] + crases, table)
    assert (elided.lemma, elided.resp) == ('ἀγαθός', 'diogenes')
    assert (name.lemma, name.resp) == ('Εὐριπίδης', 'spacy')
    assert (unknown.lemma, unknown.resp) == ('κοΐ', 'spacy')
    assert [w.crasis[-1][1:] for w in crases] == [
        ('ἔπος', 'X', 'diogenes'), ('ἐντεῦθεν', 'X', 'diogenes'), ('οὗτος', 'X', 'diogenes'),
        ('ἀβόων', 'X', 'spacy')]                                      # unknown: spaCy's lemma stays
    assert stats['spacy unknown'] == 1 and stats['crasis diogenes'] == 3


def test_morpho_spellings_and_answers(tmp_path):
    from ag_diogenes_spacy_txt2xml import morpho
    assert morpho.graved('κελεέ') == 'κελεὲ' and morpho.graved('Κελεέ') == 'Κελεὲ'
    assert morpho.graved('λόγος') is None and morpho.graved('λόγου') is None      # acute not on the last syllable
    assert morpho.queries('Κελεέ') == ['Κελεέ', 'Κελεὲ', 'κελεέ', 'κελεὲ']
    assert morpho.queries('θύραζ’') == ['θύραζʼ']                                # Perseus' elision mark U+02BC
    assert morpho.lemmas_of({'shortdef': ['ἅλς, salt', 'ἅλς2, (poetic) sea', 'ἁλής, thronged']}) == ('ἅλς', 'ἁλής')
    assert morpho.lemmas_of({'shortdef': ['No short definition found for ξυξ']}) == ()
    answers = {'Κελεὲ': ('Κελεός',), 'λῆψιν': ('λῆψις',), 'ξυξ': (), 'κοΐ': None}
    asked = []

    def fetch(q):
        asked.append(q)
        return answers.get(q, ())

    m = morpho.Morpho(cache_dir=str(tmp_path), delay=0, fetch=fetch)
    m.ask(['Κελεέ', 'λῆψιν', 'λῆψιν', 'ξυξ', 'κοΐ'])
    assert m.lemmas('Κελεέ') == ('Κελεός',) and m.lemmas('λῆψιν') == ('λῆψις',) and m.lemmas('ξυξ') == ()
    assert asked == ['Κελεέ', 'Κελεὲ', 'λῆψιν', 'ξυξ', 'κοΐ', 'κοῒ']        # each spelling once, until found
    again = morpho.Morpho(cache_dir=str(tmp_path), fetch=lambda q: pytest.fail(f'asked {q} again'))
    again.ask(['Κελεέ', 'λῆψιν', 'ξυξ'])                             # all answered: from the cache
    assert again.lemmas('λῆψιν') == ('λῆψις',)
    assert 'κοΐ' not in again.cache and again.cache['κοῒ'] == []     # an error answer is asked again next time

    def offline(q):
        raise OSError('no connection')
    down = morpho.Morpho(cache_dir=str(tmp_path / 'down'), delay=0, fetch=offline)
    down.ask(['ἄνθρακες', 'γονάτων'])
    assert down.failed == 'no connection' and down.asked == 1         # stops at the first failure


def test_morpho_error_for_one_form_is_not_a_failure(monkeypatch):
    import io
    import urllib.error
    import urllib.request
    from ag_diogenes_spacy_txt2xml import morpho

    def answer(body):
        def urlopen(req, timeout, context=None):
            raise urllib.error.HTTPError(req.full_url, 500, 'INTERNAL SERVER ERROR', {}, io.BytesIO(body))
        return urlopen
    monkeypatch.setattr(urllib.request, 'urlopen', answer(b'{"error":"Something went wrong."}\n'))
    assert morpho.fetch('ἐχαρίσω') is None                          # Morpho is up, this form fails
    monkeypatch.setattr(urllib.request, 'urlopen', answer(b'<html>Bad Gateway</html>'))
    with pytest.raises(urllib.error.HTTPError):                      # no answer from Morpho itself
        morpho.fetch('ἐχαρίσω')


def test_morpho_retries_with_certifi_after_certificate_failure(monkeypatch):
    import io
    import ssl
    import urllib.error
    import urllib.request
    from ag_diogenes_spacy_txt2xml import morpho
    pytest.importorskip('certifi')
    calls = []

    def urlopen(req, timeout, context=None):
        calls.append(context)
        if context is None:                                          # the computer's certificates fail
            raise urllib.error.URLError(ssl.SSLCertVerificationError(1, 'certificate verify failed'))
        return io.BytesIO('{"shortdef": ["ἀνήρ, a man"]}'.encode())
    monkeypatch.setattr(morpho, '_context', None)
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    assert morpho.fetch('ἄνδρες') == ('ἀνήρ',)
    assert morpho.fetch('ἄνδρα') == ('ἀνήρ',)                       # certifi from now on, no new failure
    assert calls[0] is None and all(isinstance(c, ssl.SSLContext) for c in calls[1:]) and len(calls) == 3

    def no_connection(req, timeout, context=None):
        raise urllib.error.URLError(ConnectionRefusedError('refused'))
    monkeypatch.setattr(morpho, '_context', None)
    monkeypatch.setattr(urllib.request, 'urlopen', no_connection)
    with pytest.raises(urllib.error.URLError):                       # other errors: no retry
        morpho.fetch('ἄνδρες')
    assert morpho._context is None


def test_choose_with_morpho(tmp_path):
    from ag_diogenes_spacy_txt2xml import diogenes, morpho
    path = tmp_path / 'greek-analyses.txt'
    path.write_text(ANALYSES + MORE_ANALYSES, encoding='utf-8')
    table = diogenes.load(str(path), cache_dir=str(tmp_path / 'cache'))
    answers = {'λῆψιν': ('λῆψις',), 'ἡγούμην': ('ἡγέομαι',), 'ἁλῶν': ('ἁλή', 'ἅλς'), 'Ξανθίας': (),
               'κἀγώ': ('ἐγώ',), 'χὠ': ('καί',), 'ὑμῶν': ('ὑμός', 'ὑμεῖς')}
    m = morpho.Morpho(cache_dir=str(tmp_path), delay=0, fetch=lambda q: answers.get(q, ()))
    words = [Word([('t', f)], lemma=lemma) for f, lemma in [
        ('λῆψιν', 'λῆψι'),             # unknown to Diogenes: Morpho's only lemma
        ('ἡγούμην', 'ἡγοῦμαι'),         # Diogenes ἁγέομαι/ἡγέομαι, spaCy's not among them: Morpho chooses
        ('ἡγεῖται', 'ἡγέομαι'),         # decided by Diogenes: Morpho not asked
        ('ἁλῶν', 'ἅλς'),               # two Morpho lemmas: spaCy's
        ('Ξανθίας', 'Ξανθίας'),        # Morpho does not know it: spaCy as before
        ('ὑμῶν', 'ὑμέτερος'),           # Morpho has two lemmas: it does not choose among σύ/ὑμός
        ('σοι', 'σύ')]]
    crases = [Word([('t', f)], crasis=[(f[0], 'καί', 'X', 'lexicon'), (f[1:], f[1:], 'X', 'spacy')])
              for f in ('κἀγώ', 'χὠ')]
    stats = diogenes.choose(words + crases, table, morpho=m)
    assert [(w.lemma, w.resp) for w in words] == [
        ('λῆψις', 'morpho'), ('ἡγέομαι', 'diogenes morpho'), ('ἡγέομαι', 'diogenes'),
        ('ἅλς', 'morpho spacy'), ('Ξανθίας', 'spacy'), ('σύ', 'diogenes frequency'), ('σύ', 'diogenes')]
    assert crases[0].crasis[-1][1:] == ('ἐγώ', 'X', 'morpho')
    assert crases[1].crasis[-1][1:] == ('ὠ', 'X', 'spacy')            # Morpho's καί is the first word's lemma
    assert 'ἡγεῖται' not in m.cache and stats['morpho'] == 1 and stats['crasis morpho'] == 1


def test_diogenes_and_odycy_end_to_end():
    pytest.importorskip('spacy')
    from ag_diogenes_spacy_txt2xml import diogenes
    from ag_diogenes_spacy_txt2xml.lemmatize import SpacyLemmatizer
    path = diogenes.find()
    if not path:
        pytest.skip('Diogenes not installed')
    try:
        lem = SpacyLemmatizer()
    except OSError:
        pytest.skip('grc_odycy_joint_sm not installed')
    toks = tokenize(lines_of('1   Or1.1.1 : Περὶ πολλοῦ ἂν ποιησαίμην, ὦ ἄνδρες, ἐμοίχευεν'))
    lem.run(toks)
    words = [t for t in toks if isinstance(t, Word)]
    diogenes.choose(words, diogenes.load(path))
    assert [w.lemma for w in words] == ['περί', 'πολύς', 'ἄν', 'ποιέω', 'ὦ', 'ἀνήρ', 'μοιχεύω']


TEI_FOR_ANTCONC = '''<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><ab>
<milestone unit="segment" n="Or1"/><milestone unit="segment" n="Or1.t"/><milestone unit="line" n="Or1.t.1"/>
<seg type="title"><w>ΥΠΕΡ</w></seg>
<milestone unit="segment" n="Or1.1"/><milestone unit="line" n="Or1.1.1"/>
<w lemma="περί">Περὶ</w> <w lemma="οἶδα">οἶδ’</w><pc>,</pc> <seg type="crasis"><w lemma="καί">κ</w><w lemma="ἐγώ">ἀγὼ</w></seg>
<milestone unit="line" n="Or1.1.2"/><w lemma="ἀδικέω">ἠδί<c type="editorial">[</c>κει</w> <w>ς<gap quantity="2" unit="chars"/></w>
<milestone unit="segment" n="Or1.2"/><milestone unit="line" n="Or1.2.1"/><w>Ξανθίας</w>
<milestone unit="segment" n="Or1.3"/><milestone unit="line" n="Or1.3.1.1"/><w lemma="παπῦρος">πάπυρος</w>
<milestone unit="segment" n="Or2"/><milestone unit="segment" n="Or2.1"/><milestone unit="line" n="Or2.1.1"/><w lemma="ὁ">ὁ</w>
<milestone unit="segment" n="5"/><milestone unit="segment" n="5.1"/><milestone unit="line" n="5.1.1"/><w lemma="ἀπόσπασμα">ἀπόσπασμα</w>
</ab></body></text></TEI>'''


def test_tei2antconc(tmp_path):
    from ag_diogenes_spacy_txt2xml.antconc import export
    tei = tmp_path / 'x.tei.xml'
    tei.write_text(TEI_FOR_ANTCONC, encoding='utf-8')
    report, n_forms, n_lemmas = export(str(tei), str(tmp_path), 'lys', keep='^Or', levels=3)
    assert [(name, n) for name, n, _ in report] == [('lys-01.txt', 'Or1'), ('lys-02.txt', 'Or2')]
    forms = (tmp_path / 'lys-forms' / 'lys-01.txt').read_text(encoding='utf-8').splitlines()
    lemmas = (tmp_path / 'lys-lemmas' / 'lys-01.txt').read_text(encoding='utf-8').splitlines()
    # title left out, editorial sign removed, word with lost letters left out, 4-level line left out
    assert forms == ["Περὶ οἶδ' κἀγὼ ἠδίκει", 'Ξανθίας']
    assert lemmas == ['περί οἶδα καί ἐγώ ἀδικέω', 'NOLEMMA']
    lemma_list = (tmp_path / 'lys-lemma-list-antconc4.txt').read_text(encoding='utf-8').splitlines()
    assert 'ἐγώ\tκἀγὼ' in lemma_list                            # crasis: lemma of the second word
    tagged = (tmp_path / 'lys-tagged' / 'lys-01.txt').read_text(encoding='utf-8').splitlines()
    hair = chr(0x200A)                                         # crasis: two tokens, one written form
    assert tagged == ["Περὶ_X_περί οἶδ'_X_οἶδα κ_X_καί" + hair + 'ἀγὼ_X_ἐγώ ἠδίκει_X_ἀδικέω', 'Ξανθίας_X_NOLEMMA']
    # without filters: the fragment and the papyrus line come back
    report, _, _ = export(str(tei), str(tmp_path / 'all'), 'lys')
    assert [n for _, n, _ in report] == ['Or1', 'Or2', '5']
    assert 'πάπυρος' in (tmp_path / 'all' / 'lys-forms' / 'lys-01.txt').read_text(encoding='utf-8')
