"""Dictionary identities are verified separately from morphological candidates."""
from copy import deepcopy
from dataclasses import asdict
import hashlib

from ag_diogenes_spacy_txt2xml import analyses
from ag_diogenes_spacy_txt2xml.headwords import Reference, standardize_record, WARNING, PROVISIONAL


def reference(tmp_path, keys, rows=''):
    lines = [f'<div2 key="{key}" orig_id="n{i}"><head>x</head></div2>\n'.encode() for i, key in enumerate(keys)]
    (tmp_path/'grc.lsj.xml').write_bytes(b''.join(lines))
    source = tmp_path/'greek-analyses.txt'
    source.write_text(rows.format(second=len(lines[0])), encoding='utf-8')
    store = analyses.load(str(source), str(tmp_path/'cache'))
    return Reference.from_store(store), store


def test_strong_association_keeps_the_homograph_identity(tmp_path):
    ref, store = reference(tmp_path, ['a)/peimi1', 'a)/peimi2'],
        'a)pie/nai\t{{{second} 9 a)/peimi2\tgo away\tpres inf act}}\n')
    try:
        selected = asdict(store.lookup('ἀπιέναι')[0]); before = deepcopy(selected)
        assert ref.assess('ἀπιέναι', 'ἄπειμι').reason == 'ambiguous_reference_identity'
        result = ref.assess('ἀπιέναι', 'ἄπειμι', candidate=selected)
        assert result.reference_id.endswith(':n1') and result.reference_entries[0]['dictionary_key'] == 'a)/peimi2'
        assert selected == before
    finally: ref.close(); store.close()


def test_dictionary_link_case_and_homograph_mismatches_are_provisional(tmp_path):
    ref, store = reference(tmp_path, ['la/maxos', 'a)/peimi1'],
        '*lama/xwn\t{{0 9 *la/maxos\tname\tmasc gen pl}}\n'
        'a)pie/nai\t{{{second} 9 a)/peimi2\tdepart\tpres inf act}}\n')
    try:
        for form in ('Λαμάχων', 'ἀπιέναι'):
            candidate = asdict(store.lookup(form)[0])
            assert ref.linked_entry(candidate) is None
            assert ref.assess(form, candidate['lemma'], candidate=candidate).reason == 'provisional_provider_link'
    finally: ref.close(); store.close()


def test_weak_existing_headword_remains_raw_and_unverified(tmp_path):
    ref, store = reference(tmp_path, ['h(mo/s'], 'h(mw=n\t{{0 1 h(mo/s\tguess\tmasc gen pl}}\n')
    try:
        candidate = asdict(store.lookup('ἡμῶν')[0]); raw = deepcopy(candidate)
        record = dict(form='ἡμῶν', lemma='ἡμός', pos='ADJ', lemma_id=candidate['lemma_id'], resp='diogenes',
            readings=[dict(expansion=['ἡμῶν'], components=[dict(candidates=[candidate], selected_id=candidate['lemma_id'])])])
        standardize_record(record, ref)
        assert record['lemma'] == 'ἡμός' and record['lemma_id'] == raw['lemma_id']
        assert PROVISIONAL in record['review_reasons']
        assert record['readings'][0]['components'][0]['candidates'][0] == raw
        assert record['rule_contributions'][-1]['reference_id'] is None
    finally: ref.close(); store.close()


def test_record_normalization_is_idempotent_and_does_not_rewrite_raw_oga(tmp_path):
    ref, store = reference(tmp_path, ['trei=s'])
    try:
        raw = {'lemma':'τρία','row':'original OGA row'}
        record = dict(form='τρία', lemma='τρία', pos='NUM', resp='oga', readings=[dict(oga=raw, components=[])])
        original = deepcopy(raw)
        stats = standardize_record(record, ref)
        assert record['lemma'] == 'τρεῖς' and stats['headwords renamed'] == 1
        assert record['lemma_id'].startswith('lsj:') and record['readings'][0]['oga'] == original
        normalized = deepcopy(record)
        assert not standardize_record(record, ref) and record == normalized
        outside = dict(form='ἥσθην', lemma='σθω', pos='VERB')
        standardize_record(outside, ref)
        assert outside['lemma'] == 'σθω' and WARNING in outside['review_reasons']
    finally: ref.close(); store.close()


def test_reference_is_invalidated_when_dictionary_bytes_change(tmp_path):
    ref, store = reference(tmp_path, ['trei=s'])
    old_path, old_sha = ref.path, ref.sha256
    ref.close(); store.close()
    dictionary = tmp_path/'grc.lsj.xml'
    dictionary.write_text('<div2 key="du/o" orig_id="n2"><head>x</head></div2>', encoding='utf-8')
    new = Reference(dictionary, cache_dir=tmp_path/'cache')
    try:
        assert new.path != old_path and new.sha256 != old_sha
        assert new.sha256 == hashlib.sha256(dictionary.read_bytes()).hexdigest()
        assert not new.lookup('τρεῖς') and new.lookup('δύο')
    finally: new.close()


def test_number_omission_and_quantity_typography_do_not_create_false_warnings(tmp_path):
    ref, store = reference(tmp_path, ['de/1', 'pa/nu^'],
        'de/\t{{0 9 de/\tbut\tindeclform(conj)}}\n'
        'pa/nu\t{{{second} 9 pa/nu\tvery\tindeclform(adverb)}}\n')
    try:
        for form in ('δέ', 'πάνυ'):
            candidate = asdict(store.lookup(form)[0])
            assert ref.linked_entry(candidate) is not None
            assert ref.assess(form, candidate['lemma'], candidate=candidate).status == 'applied'
    finally: ref.close(); store.close()


def test_repeated_source_identifiers_keep_distinct_dictionary_entries(tmp_path):
    dictionary = tmp_path/'dictionary.xml'
    dictionary.write_text('<div2 key="lo/gos" orig_id="same"><head>λόγος</head></div2>\n'
                          '<div2 key="e)/rgon" orig_id="same"><head>ἔργον</head></div2>\n', encoding='utf-8')
    ref = Reference(dictionary, cache_dir=tmp_path/'cache')
    try:
        assert ref.lookup('λόγος')[0]['id'] != ref.lookup('ἔργον')[0]['id']
    finally: ref.close()


def test_rendered_heading_preserves_proper_name_case(tmp_path):
    dictionary = tmp_path/'dictionary.xml'
    dictionary.write_text('<div2 key="falh=s" orig_id="name"><head>Φαλῆς</head></div2>\n', encoding='utf-8')
    ref = Reference(dictionary, cache_dir=tmp_path/'cache')
    try:
        assert ref.assess('Φαλῆς', 'Φαλῆς').headword == 'Φαλῆς'
        assert ref.assess('Φαλῆς', 'Φαλῆς').status == 'applied'
    finally: ref.close()


def test_oga_base_deictic_uses_existing_separate_headword_rule(tmp_path):
    ref, store = reference(tmp_path, ['ou(=tos'])
    try:
        record = dict(form='αὑτηί', expansion=['αὑτηί'], lemma='οὗτος', pos='PRON', resp='oga',
                      readings=[{'kind':'oga','oga':{'lemma':'οὗτος'},'components':[]}])
        standardize_record(record, ref)
        assert record['lemma'] == 'οὑτοσί' and record['expansion'] == ['αὑτηί']
        assert record['readings'][0]['oga']['lemma'] == 'οὗτος'
        assert any(t['rule_id']=='grc.lemma.deictic-iota-headword' and t['status']=='applied' for t in record['rule_contributions'])
    finally: ref.close(); store.close()
