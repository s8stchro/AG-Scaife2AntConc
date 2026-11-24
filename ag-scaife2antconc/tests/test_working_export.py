"""Working exports preserve one text revision, annotation identities and source evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from ag_scaife2antconc.working_export import ExportConflict, export_project

NS = {'t': 'http://www.tei-c.org/ns/1.0'}


class Model:
    def __init__(self, folder, data):
        self.folder, self.data = folder, data
        self.revision = data['revision']

    def export_data(self):
        return deepcopy(self.data)


def token(uid, form, start, end, lemma='', pos='X', **annotation):
    return {'uid': uid, 'token': int(uid[1:]), 'start': start, 'end': end, 'form': form,
            'record': {'lemma': 'old-lemma', 'pos': 'VERB'},
            'annotation': {'lemma': lemma, 'pos': pos, 'expansion': [form], 'reviewed': False,
                           'stale': False, 'context_stale': False, **annotation}}


@pytest.fixture
def edition(tmp_path):
    source = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt>'
              '<title>Original edition</title></titleStmt><publicationStmt><p>Source</p></publicationStmt>'
              '<sourceDesc><p>Original provenance</p></sourceDesc></fileDesc></teiHeader>'
              '<text><body><sp><speaker>Χορός</speaker><!-- editorial comment -->'
              '<l n="293">μἀλλαʼ <hi rend="italic">ἀκούσατε</hi>.<note>untouched note</note></l>'
              '</sp></body></text></TEI>').encode()
    archived = tmp_path / '.working-text/sources/original.xml'
    archived.parent.mkdir(parents=True)
    archived.write_bytes(source)
    current = "μἀλλ' ἀκούσατε."
    data = {'revision': 'a' * 64, 'options': {'keep': []},
            'sources': [{'id': 's1', 'kind': 'xml', 'path': str(archived.relative_to(tmp_path)),
                         'original_path': 'Perseus-Acharnians.xml', 'sha256': hashlib.sha256(source).hexdigest()}],
            'passages': [{'id': 'p1', 'work': 'Aristophanes-Acharnians', 'citation': '293', 'source_id': 's1',
                          'source_anchor': {'path': [1, 0, 0, 1], 'tag': 'l', 'faithful': False},
                          'original_text': 'μἀλλαʼ ἀκούσατε.', 'text': current,
                          'tokens': [token('t1', "μἀλλ'", 0, 5, 'bad-old-lemma', 'VERB', stale=True),
                                     token('t2', 'ἀκούσατε', 6, 14, 'ἀκούω', 'VERB', reviewed=True, context_stale=True)],
                          'history': [{'id': 'r1', 'old_text': 'μἀλλαʼ ἀκούσατε.', 'new_text': current,
                                       'reason': 'Correction of imported reading'}]}]}
    return Model(tmp_path, data), archived, source


def exported(model, manifest, suffix):
    return next(model.folder / path for path in manifest['files'] if path.endswith(suffix))


def test_corrected_text_and_effective_annotations_feed_both_exports(edition):
    model, archived, original = edition
    legacy = model.folder / 'analysis-review.jsonl'
    legacy.write_text('legacy results remain unchanged', encoding='utf-8')
    result = export_project(model)
    assert result['revision'] == model.revision
    assert result['fidelity'] == {'Aristophanes-Acharnians': 'source-preserving'}
    xml = ET.parse(exported(model, result, '.xml'))
    line = xml.find('.//t:l', NS)
    assert line.text == "μἀλλ' "
    assert line.find('t:hi', NS).text == 'ἀκούσατε'
    assert line.find('t:hi', NS).get('rend') == 'italic'
    assert line.find('t:note', NS).text == 'untouched note'
    assert xml.find('.//t:speaker', NS).text == 'Χορός'
    assert 'editorial comment' in exported(model, result, '.xml').read_text(encoding='utf-8')
    assert xml.find('.//t:title', NS).text == 'Original edition'
    text = exported(model, result, '/Aristophanes-Acharnians.txt').read_text(encoding='utf-8')
    assert text == "μἀλλ'_X_NOLEMMA ἀκούσατε_VERB_ἀκούω\n"
    assert archived.read_bytes() == original
    assert legacy.read_text(encoding='utf-8') == 'legacy results remain unchanged'
    snapshot = json.loads(exported(model, result, 'working-edition.json').read_text(encoding='utf-8'))
    assert snapshot['passages'][0]['history'][0]['old_text'] == 'μἀλλαʼ ἀκούσατε.'
    spans = xml.findall('.//t:spanGrp[@type="working-text-tokens"]/t:span', NS)
    assert [(s.get('n'), s.text) for s in spans] == [('0:5', "μἀλλ'"), ('6:14', 'ἀκούσατε')]
    assert json.loads((model.folder / 'working-exports/current.json').read_text(encoding='utf-8'))['revision'] == model.revision


def test_repeated_export_reuses_identical_immutable_revision(edition):
    model, _, _ = edition
    first = export_project(model)
    before = {p: (model.folder / p).read_bytes() for p in first['files']}
    second = export_project(model)
    assert second == first
    assert all((model.folder / p).read_bytes() == value for p, value in before.items())


def test_tampered_archive_or_published_file_is_not_silently_overwritten(edition):
    model, archived, original = edition
    manifest = export_project(model)
    text = exported(model, manifest, '/Aristophanes-Acharnians.txt')
    text.write_text('external change', encoding='utf-8')
    with pytest.raises(ValueError, match='saved export was modified'):
        export_project(model)
    assert text.read_text(encoding='utf-8') == 'external change'
    archived.write_bytes(original + b' ')
    with pytest.raises(ValueError, match='archived original text'):
        export_project(model)


def test_edit_across_markup_is_explicitly_derived_instead_of_flattening_source(edition):
    model, archived, original = edition
    passage = model.data['passages'][0]
    passage['text'] = 'νέον.'
    passage['tokens'] = [token('t3', 'νέον', 0, 4, stale=True)]
    result = export_project(model)
    assert result['fidelity']['Aristophanes-Acharnians'] == 'derived'
    assert result['warnings']
    tree = ET.parse(exported(model, result, '.xml'))
    assert tree.find('.//t:ab', NS).text == 'νέον.'
    assert 'DERIVED TEI' in ''.join(tree.find('.//t:sourceDesc', NS).itertext())
    assert archived.read_bytes() == original


def test_oga_is_identified_as_derived_tei_with_literal_working_punctuation(edition):
    model, archived, _ = edition
    archived.write_bytes(b'# original OGA rows\n')
    model.data['sources'][0].update(kind='oga', sha256=hashlib.sha256(archived.read_bytes()).hexdigest())
    result = export_project(model)
    assert result['fidelity']['Aristophanes-Acharnians'] == 'derived'
    tree = ET.parse(exported(model, result, '.xml'))
    assert tree.find('.//t:ab', NS).text == "μἀλλ' ἀκούσατε."
    assert 'DERIVED TEI from oga' in ''.join(tree.find('.//t:sourceDesc', NS).itertext())


def test_obsolete_inline_source_annotations_are_explicitly_historical(edition):
    model, archived, _ = edition
    raw = archived.read_text(encoding='utf-8').replace('<hi rend="italic">ἀκούσατε</hi>',
        '<w lemma="παλαιός" pos="ADJ" msd="old-features">ἀκούσατε</w>')
    archived.write_text(raw, encoding='utf-8')
    model.data['sources'][0]['sha256'] = hashlib.sha256(archived.read_bytes()).hexdigest()
    result = export_project(model)
    tree = ET.parse(exported(model, result, '.xml'))
    word = tree.find('.//t:l/t:w', NS)
    assert not any(name in word.attrib for name in ('lemma', 'pos', 'msd'))
    historical = tree.find('.//t:interpGrp[@type="historical-source-inline-annotations"]/t:interp', NS)
    assert json.loads(historical.text) == {'lemma': 'παλαιός', 'pos': 'ADJ', 'msd': 'old-features'}
    current = tree.findall('.//t:interpGrp[@type="lemmata"]/t:interp', NS)
    assert [item.text for item in current] == ['ἀκούω']


def test_export_revision_conflict_preserves_previous_pointer_and_files(edition):
    model, _, _ = edition
    first = export_project(model)
    pointer = model.folder / 'working-exports/current.json'
    original = pointer.read_bytes()
    model.revision = model.data['revision'] = 'b' * 64
    def change_again():
        model.revision = 'c' * 64
    with pytest.raises(ExportConflict):
        export_project(model, before_publish=change_again)
    assert pointer.read_bytes() == original
    assert not (model.folder / 'working-exports' / ('b' * 64)).exists()
    assert all((model.folder / path).is_file() for path in first['files'])


def test_insertions_deletions_and_manual_crasis_use_current_token_order(edition):
    model, _, _ = edition
    passage = model.data['passages'][0]
    passage['text'] = 'κἀγώ νῦν.'
    passage['tokens'] = [token('t5', 'κἀγώ', 0, 4, 'καί+ἐγώ', 'CCONJ+PRON',
                               manual_crasis={'cut': 1, 'forms': ['καί', 'ἐγώ']}),
                         token('t7', 'νῦν', 5, 8, 'νῦν', 'ADV')]
    result = export_project(model)
    text = exported(model, result, '/Aristophanes-Acharnians.txt').read_text(encoding='utf-8')
    assert text == 'κ_CCONJ_καί\u200aἀγώ_PRON_ἐγώ νῦν_ADV_νῦν\n'
    assert 'ἀκούσατε' not in text


def test_archived_source_cannot_escape_project_directory(edition, tmp_path):
    model, _, _ = edition
    model.data['sources'][0]['path'] = '../outside.xml'
    with pytest.raises(ValueError, match='archived original text'):
        export_project(model)
    assert not (model.folder / 'working-exports/current.json').exists()


def test_working_export_preserves_manual_crasis_and_numbers_duplicate_stems_cleanly(edition):
    model, _, _ = edition
    manual = {'cut': 1, 'forms': ['καί', 'ἐγώ']}
    base = model.data['passages'][0]
    base['text'] = 'κἀγώ.'
    base['tokens'] = [token('t5', 'κἀγώ', 0, 4, 'καί+ἐγώ', 'CCONJ+PRON', manual_crasis=manual)]
    base['tokens'][0]['record'].update(msd='Case=Nom|Number=Sing', resp='diogenes greberta', lemma_id='lsj:kago')
    # Three distinct work names that sanitize to the same filename stem ("a_b")
    p2 = deepcopy(base); p2['id'] = 'p2'; p2['work'] = 'a:b'
    p3 = deepcopy(base); p3['id'] = 'p3'; p3['work'] = 'a?b'
    base['work'] = 'a_b'
    model.data['passages'] = [base, p2, p3]
    result = export_project(model)
    rows = [json.loads(line) for line in exported(model, result, 'analysis-review.jsonl').read_text(encoding='utf-8').splitlines()]
    assert rows[0]['manual_crasis'] == manual
    import csv
    tsv = list(csv.reader(exported(model, result, 'lemma-review.tsv').read_text(encoding='utf-8-sig').splitlines(), delimiter='\t'))
    assert 'manual crasis' in tsv[0] and json.loads(tsv[1][tsv[0].index('manual crasis')]) == manual
    tagged_names = sorted(Path(p).name for p in result['files'] if '/antconc-tagged/' in p)
    assert tagged_names == ['a_b-2.txt', 'a_b-3.txt', 'a_b.txt']
    xml = ET.parse(exported(model, result, '.xml'))
    assert xml.find('.//t:interpGrp[@type="morphology"]/t:interp', NS).text == 'Case=Nom|Number=Sing'
    assert json.loads(xml.find('.//t:interpGrp[@type="provenance"]/t:interp', NS).text)['lemma_id'] == 'lsj:kago'


def test_working_export_splits_multibook_antconc_files(edition):
    model, _, _ = edition
    p1 = deepcopy(model.data['passages'][0]); p1['id'] = 'p1'; p1['citation'] = '1.1'
    p2 = deepcopy(model.data['passages'][0]); p2['id'] = 'p2'; p2['citation'] = '2.1'
    model.data['passages'] = [p1, p2]
    result = export_project(model)
    tagged_names = sorted(Path(p).name for p in result['files'] if '/antconc-tagged/' in p)
    assert tagged_names == ['Aristophanes-Acharnians-1.txt', 'Aristophanes-Acharnians-2.txt']


def test_lemma_texts_mirror_the_tagged_texts(edition):
    model, _, _ = edition
    result = export_project(model)
    lemmata = next(model.folder / p for p in result['files'] if '/antconc-lemmata/' in p)
    assert lemmata.name == 'Aristophanes-Acharnians.txt'
    assert lemmata.read_text(encoding='utf-8') == 'NOLEMMA ἀκούω\n'


def test_an_export_made_before_lemma_texts_gains_only_the_missing_files(edition):
    model, _, _ = edition
    result = export_project(model)
    final = model.folder / result['directory']
    manifest = json.loads((final / 'manifest.json').read_text(encoding='utf-8'))
    # As written by versions without antconc-lemmata.
    (final / 'antconc-lemmata/Aristophanes-Acharnians.txt').unlink()
    (final / 'antconc-lemmata').rmdir()
    manifest['files'] = [f for f in manifest['files'] if '/antconc-lemmata/' not in f]
    manifest['sha256'] = {k: v for k, v in manifest['sha256'].items() if not k.startswith('antconc-lemmata/')}
    (final / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    before = {p: p.read_bytes() for p in final.rglob('*') if p.is_file() and p.name != 'manifest.json'}
    again = export_project(model)
    assert all(p.read_bytes() == content for p, content in before.items())
    assert (final / 'antconc-lemmata/Aristophanes-Acharnians.txt').read_text(encoding='utf-8') == 'NOLEMMA ἀκούω\n'
    assert any('/antconc-lemmata/' in f for f in again['files'])
    saved = json.loads((final / 'manifest.json').read_text(encoding='utf-8'))
    assert 'antconc-lemmata/Aristophanes-Acharnians.txt' in saved['sha256']
    assert export_project(model)['files'] == again['files']          # stable on the next export


def test_an_old_export_gains_lemma_files_that_mirror_its_own_tagged_files(edition):
    model, _, _ = edition
    final = model.folder / export_project(model)['directory']
    manifest = json.loads((final / 'manifest.json').read_text(encoding='utf-8'))
    # An older export with another file layout: its tagged file has a name the current layout does not use.
    for path in (final / 'antconc-lemmata').iterdir():
        path.unlink()
    (final / 'antconc-lemmata').rmdir()
    tagged = final / 'antconc-tagged/Aristophanes-Acharnians.txt'
    tagged.rename(final / 'antconc-tagged/Old-layout.txt')
    manifest['sha256'] = {k: v for k, v in manifest['sha256'].items() if not k.startswith('antconc-')}
    manifest['sha256']['antconc-tagged/Old-layout.txt'] = hashlib.sha256((final / 'antconc-tagged/Old-layout.txt').read_bytes()).hexdigest()
    manifest['files'] = [f for f in manifest['files'] if '/antconc-' not in f] + [
        (final / 'antconc-tagged/Old-layout.txt').relative_to(model.folder).as_posix()]
    (final / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    export_project(model)
    assert sorted(p.name for p in (final / 'antconc-lemmata').iterdir()) == ['Old-layout.txt']
    assert (final / 'antconc-lemmata/Old-layout.txt').read_text(encoding='utf-8') == 'NOLEMMA ἀκούω\n'
