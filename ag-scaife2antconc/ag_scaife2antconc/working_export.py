"""Revision-bound exports from an editable edition, never from the legacy result stream.

Original sources and legacy result files remain untouched.  Native TEI is retained
when text changes map safely to source text nodes.  Otherwise the XML is explicitly
identified as a derived edition.  The complete working snapshot accompanies both.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import csv
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

from ag_diogenes_spacy_txt2xml.tokenize import Word
from ag_diogenes_spacy_txt2xml.tei import crasis_cuts

from . import __version__
from .pipeline import APOSTROPHES, LEMMA_FOLDER, lemmata_of_tagged, lemmatized, tagged, unique_name
from .file_io import keep_name
from .reader import BLOCKS, DROP, PREFERRED
from .web_storage import atomic_json
from . import review

TEI = 'http://www.tei-c.org/ns/1.0'
XML_ID = '{http://www.w3.org/XML/1998/namespace}id'


class ExportConflict(ValueError):
    """The working edition changed while its export was being prepared."""


class UnsafeSourceEdit(ValueError):
    """A correction cannot safely be projected onto the preserved XML tree."""


def _local(element):
    return element.tag.rsplit('}', 1)[-1] if isinstance(element.tag, str) else ''


def _safe_name(value):
    return keep_name(value)


def _current_revision(model):
    loader = getattr(type(model), 'load', None)
    if loader is not None and (Path(model.folder) / 'working-text.json').is_file():
        return loader(model.folder).revision
    return model.revision


def _annotation(token):
    values = dict(token.get('record') or {})
    values.update(token.get('annotation') or {})
    for key in ('lemma', 'pos', 'expansion', 'manual_crasis', 'reviewed', 'stale', 'context_stale', 'machine_crasis'):
        if key in token:
            values[key] = token[key]
    if values.get('stale'):
        values.update(lemma='', pos='X', reviewed=False, expansion=[token['form']], manual_crasis=None)
    return values


def _crasis_parts(form, token, values):
    lemmas = (values.get('lemma') or '').split('+')
    tags = (values.get('pos') or 'X').split('+')
    if len(lemmas) <= 1 or len(tags) != len(lemmas):
        return None
    manual = values.get('manual_crasis')
    if manual:
        from .review import ManualCrasis
        pieces = ManualCrasis(manual['cut'], manual['forms']).pieces(form)
    else:
        previous = values.get('machine_crasis') or token.get('machine_crasis') or (token.get('record') or {}).get('machine_crasis')
        previous_pieces = [unicodedata.normalize('NFC', p[0].translate(APOSTROPHES)) for p in previous] if previous else []
        if previous and len(previous) == len(lemmas) and ''.join(previous_pieces) == form:
            pieces = previous_pieces
        else:
            expansion = values.get('expansion') or [form]
            cuts = crasis_cuts(form, expansion)
            if cuts is None or len(cuts) + 1 != len(lemmas):
                return None
            boundaries = [0] + cuts + [len(form)]
            pieces = [form[a:b] for a, b in zip(boundaries, boundaries[1:])]
    return [(piece, lemma, tag, 'working-text') for piece, lemma, tag in zip(pieces, lemmas, tags)]


def _antconc_texts(by_work):
    """[(relative path, text)] of antconc-tagged and antconc-lemmata, one file per work or book."""
    out, used = [], set()
    for work, passages in by_work.items():
        base_stem = unique_name(_safe_name(work), used)
        books = {}
        for passage in passages:
            cit = passage.get('citation') or ''
            top = cit.split('.', 1)[0] if '.' in cit else ''
            books.setdefault(top, []).append(passage)
        sections = list(books.items()) if len(books) > 1 and all(books) else [('', passages)]
        for top, group in sections:
            stem = base_stem if not top else unique_name(f'{base_stem}-{_safe_name(top)}', used)
            for subfolder, render in (('antconc-tagged', tagged), (LEMMA_FOLDER, lemmatized)):
                lines = []
                for passage in group:
                    line = [_antconc_token(token, render) for token in passage['tokens']]
                    lines.append(' '.join(value for value in line if value))
                out.append((subfolder + '/' + stem + '.txt', '\n'.join(lines) + '\n'))
    return out


def _antconc_token(token, render=tagged):
    """The token as antconc-tagged writes it (render=tagged) or antconc-lemmata (render=lemmatized)."""
    values = _annotation(token)
    form = unicodedata.normalize('NFC', token['form'].translate(APOSTROPHES))
    if not form or '…' in form:
        return None
    word = Word([('t', form)], lemma=values.get('lemma') or None, pos=values.get('pos') or 'X')
    word.crasis = _crasis_parts(form, token, values)
    return render(form, word)


def _element_at(root, path):
    element = root
    for index in path:
        children = [child for child in element if isinstance(child.tag, str)]
        if type(index) is not int or not 0 <= index < len(children):
            raise UnsafeSourceEdit('Could not safely locate the original position in the XML.')
        element = children[index]
    return element


def _projection(element, keep):
    """Reader-like visible text with a character-to-source-text-node map.

    This deliberately abstains on transformations (cross-line hyphenation, gaps,
    conflicting editorial markup) for which there is no exact reversible map.
    """
    slots, chars, mapping = [], [], []

    def text_slot(owner, attribute):
        value = getattr(owner, attribute) or ''
        number = len(slots)
        slots.append((owner, attribute, value))
        chars.extend(value)
        mapping.extend((number, index) for index in range(len(value)))

    def blank():
        chars.append(' ')
        mapping.append(None)

    def collect(parent):
        text_slot(parent, 'text')
        for child in parent:
            tag = _local(child)
            if not tag:
                text_slot(child, 'tail')
                continue
            if tag == 'gap':
                chars.append('\ufffd')
                mapping.append(None)
            elif tag == 'lb':
                if child.get('break') != 'no':
                    blank()
            elif tag in DROP and (DROP[tag] is None or DROP[tag] not in keep):
                if tag in ('note', 'label', 'head', 'stage', 'speaker'):
                    blank()
            elif tag in ('choice', 'app'):
                chosen = next((c for name in PREFERRED for c in child if _local(c) == name), None)
                if chosen is None and len(child):
                    chosen = child[0]
                if 'rejected readings' in keep:
                    for part in child:
                        if part is not child[0]:
                            blank()
                        collect(part)
                elif chosen is not None:
                    collect(chosen)
            else:
                surround = tag in BLOCKS or tag in DROP or tag == 'foreign'
                if surround:
                    blank()
                collect(child)
                if surround:
                    blank()
            text_slot(child, 'tail')

    collect(element)
    if 'deletions' not in keep:
        close, filtered, remapped = None, [], []
        for ch, location in zip(chars, mapping):
            if close:
                if ch == close:
                    close = None
                continue
            if ch in ('[', '⟦'):
                close = ']' if ch == '[' else '⟧'
                continue
            if ch in (']', '⟧'):
                continue
            filtered.append(ch)
            remapped.append(location)
        chars, mapping = filtered, remapped
    visible, mapped = [], []
    for ch, location in zip(chars, mapping):
        if ch.isspace():
            if visible and visible[-1] != ' ':
                visible.append(' ')
                mapped.append(location)
        else:
            visible.append(ch)
            mapped.append(location)
    if visible and visible[-1] == ' ':
        visible.pop()
        mapped.pop()
    return ''.join(visible), mapped, slots


def _apply_text(element, original, current, keep):
    if original == current:
        return
    visible, mapping, slots = _projection(element, keep)
    if visible != original:
        raise UnsafeSourceEdit('The edited passage does not match the text nodes of the original XML.')
    edits = defaultdict(list)
    for operation, a, b, c, d in SequenceMatcher(None, original, current, autojunk=False).get_opcodes():
        if operation == 'equal':
            continue
        locations = [m for m in mapping[a:b] if m is not None]
        if locations:
            numbers = {m[0] for m in locations}
            if len(numbers) != 1 or len(locations) != b - a:
                raise UnsafeSourceEdit('The edit crosses internal XML markup.')
            number = locations[0][0]
            start, end = locations[0][1], locations[-1][1] + 1
            if [m[1] for m in locations] != list(range(start, end)):
                raise UnsafeSourceEdit('The edit crosses omitted or editorial XML content.')
        else:
            adjacent = mapping[a] if a < len(mapping) else None
            if adjacent is not None:
                number, start = adjacent
                end = start
            elif a and mapping[a - 1] is not None:
                number, previous = mapping[a - 1]
                start = end = previous + 1
            elif not original and not len(element):
                number = 0
                start = end = 0
            else:
                raise UnsafeSourceEdit('No unique insertion point exists in the original XML.')
        edits[number].append((start, end, current[c:d]))
    for number, replacements in edits.items():
        owner, attribute, value = slots[number]
        for start, end, replacement in sorted(replacements, reverse=True):
            value = value[:start] + replacement + value[end:]
        setattr(owner, attribute, value)
    projected, _, _ = _projection(element, keep)
    if projected != current:
        raise UnsafeSourceEdit('The edit cannot be round-tripped without losing original XML markup.')


def _q(root, name):
    namespace = root.tag.partition('}')[0] + '}' if root.tag.startswith('{') else ''
    return namespace + name


def _identifier(proposed, used):
    proposed = re.sub(r'[^A-Za-z0-9_.-]', '-', proposed)
    candidate, number = proposed, 2
    while candidate in used:
        candidate = proposed + '-' + str(number)
        number += 1
    used.add(candidate)
    return candidate


def _add_annotations(root, passages, revision, native_elements=None):
    used = {element.get(XML_ID) for element in root.iter() if element.get(XML_ID)}
    q = lambda name: _q(root, name)
    header = root.find(q('teiHeader'))
    if header is None:
        header = ET.Element(q('teiHeader'))
        root.insert(0, header)
    changes = header.find(q('revisionDesc'))
    if changes is None:
        changes = ET.SubElement(header, q('revisionDesc'))
    ET.SubElement(changes, q('change'), {'when': datetime.now(timezone.utc).isoformat(), 'n': revision}).text = (
        'Working-text revision exported by AG-Scaife2AntConc ' + __version__ +
        '. Token offsets are zero-based Unicode code-point offsets into the exact working-passage transcription in standOff; end offsets are exclusive.')
    stand = ET.SubElement(root, q('standOff'), {'type': 'ag-working-text', 'n': revision})
    transcriptions = ET.SubElement(stand, q('interpGrp'), {'type': 'working-passages'})
    lemmata = ET.SubElement(stand, q('interpGrp'), {'type': 'lemmata'})
    parts = ET.SubElement(stand, q('interpGrp'), {'type': 'parts-of-speech'})
    morphology = ET.SubElement(stand, q('interpGrp'), {'type': 'morphology'})
    provenance = ET.SubElement(stand, q('interpGrp'), {'type': 'provenance'})
    components = ET.SubElement(stand, q('interpGrp'), {'type': 'analysis-components'})
    status = ET.SubElement(stand, q('interpGrp'), {'type': 'annotation-status'})
    spans = ET.SubElement(stand, q('spanGrp'), {'type': 'working-text-tokens'})
    history = ET.SubElement(stand, q('interpGrp'), {'type': 'working-text-revisions'})
    source_annotations = ET.SubElement(stand, q('interpGrp'), {'type': 'historical-source-inline-annotations'})
    historical_words = set()
    for passage in passages:
        passage_id = _identifier('ag-p-' + passage['id'], used)
        attrs = {XML_ID: passage_id, 'n': passage['citation']}
        native = (native_elements or {}).get(passage['id'])
        if native is not None:
            source_id = native.get(XML_ID)
            if not source_id:
                source_id = _identifier('ag-source-' + passage['id'], used)
                native.set(XML_ID, source_id)
            attrs['corresp'] = '#' + source_id
            # Inline annotations imported with a source belong to that source's
            # historical version. Preserve them explicitly, so an obsolete @lemma
            # cannot contradict the current revision's authoritative standOff.
            for word in native.iter():
                if _local(word) != 'w' or id(word) in historical_words:
                    continue
                historical_words.add(id(word))
                old = {name: word.attrib.pop(name) for name in ('lemma', 'lemmaRef', 'pos', 'msd') if name in word.attrib}
                if old:
                    word_id = word.get(XML_ID)
                    if not word_id:
                        word_id = _identifier('ag-source-word-' + passage['id'] + '-' + str(len(historical_words)), used)
                        word.set(XML_ID, word_id)
                    ET.SubElement(source_annotations, q('interp'), {'corresp': '#' + word_id}).text = json.dumps(old, ensure_ascii=False)
        ET.SubElement(transcriptions, q('interp'), attrs).text = passage['text']
        for token in passage['tokens']:
            values = _annotation(token)
            raw = token.get('record') or {}
            token_id = _identifier('ag-t-' + token['uid'], used)
            references = []
            prov = {k: raw[k] for k in ('resp', 'lemma_id', 'rule_contributions') if raw.get(k)}
            if values.get('manual'):
                prov['resp'] = 'corrector'
            msd_value = '' if values.get('stale') or values.get('manual') else (raw.get('msd') or '')
            for group, suffix, value in (
                    (lemmata, 'lemma', values.get('lemma') or ''),
                    (parts, 'pos', values.get('pos') or 'X'),
                    (morphology, 'msd', msd_value),
                    (provenance, 'provenance', json.dumps(prov, ensure_ascii=False) if prov and not values.get('stale') else ''),
                    (components, 'components', json.dumps({'expansion': values.get('expansion') or [token['form']],
                        'manual_crasis': values.get('manual_crasis'), 'machine_crasis': values.get('machine_crasis')}, ensure_ascii=False)),
                    (status, 'status', 'stale' if values.get('stale') else 'context-stale' if values.get('context_stale') else
                        'reviewed' if values.get('reviewed') else 'unreviewed')):
                if not value:
                    continue
                ident = _identifier(token_id + '-' + suffix, used)
                ET.SubElement(group, q('interp'), {XML_ID: ident}).text = value
                references.append('#' + ident)
            start, end = token['start'], token['end']
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(passage['text']):
                raise ValueError('Invalid word offset in the working text.')
            ET.SubElement(spans, q('span'), {XML_ID: token_id, 'corresp': '#' + passage_id,
                'n': f'{start}:{end}', 'ana': ' '.join(references)}).text = passage['text'][start:end]
        for index, event in enumerate(passage.get('history') or []):
            ident = _identifier('ag-revision-' + passage['id'] + '-' + str(index), used)
            ET.SubElement(history, q('interp'), {XML_ID: ident, 'corresp': '#' + passage_id}).text = json.dumps(event, ensure_ascii=False)


def _derived(passages, source, revision, reason):
    root = ET.Element('{' + TEI + '}TEI', {'{http://www.w3.org/XML/1998/namespace}lang': 'grc'})
    q = lambda name: '{' + TEI + '}' + name
    header = ET.SubElement(root, q('teiHeader'))
    desc = ET.SubElement(header, q('fileDesc'))
    ET.SubElement(ET.SubElement(desc, q('titleStmt')), q('title')).text = 'Corrected and annotated working edition'
    ET.SubElement(ET.SubElement(desc, q('publicationStmt')), q('p')).text = 'Generated by AG-Scaife2AntConc.'
    ET.SubElement(ET.SubElement(desc, q('sourceDesc')), q('p')).text = (
        'DERIVED TEI from ' + (source.get('kind') or 'working text') + ': ' + (source.get('original_path') or source.get('path') or '') +
        '. This document does not reconstruct or preserve the original Perseus XML apparatus. ' + reason)
    body = ET.SubElement(ET.SubElement(root, q('text')), q('body'))
    native = {}
    groups = {}
    for passage in passages:
        if passage['work'] not in groups:
            groups[passage['work']] = ET.SubElement(body, q('div'), {'type': 'textpart', 'n': passage['work']})
        block = ET.SubElement(groups[passage['work']], q('ab'), {'n': passage['citation']})
        block.text = passage['text']
        native[passage['id']] = block
    _add_annotations(root, passages, revision, native)
    return ET.ElementTree(root)


def _source_xml(folder, source, passages, revision, keep):
    path = (folder / source['path']).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError('The archived original text is not available.')
    content = path.read_bytes()
    if source.get('sha256') and hashlib.sha256(content).hexdigest() != source['sha256']:
        raise ValueError('The archived original text changed. Export aborted.')
    if source.get('kind') != 'xml':
        return _derived(passages, source, revision, 'The source supplied annotation rows rather than an XML tree.'), 'derived', []
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(content, parser=parser)
    native = {}
    try:
        for passage in passages:
            anchor = passage.get('source_anchor')
            if not isinstance(anchor, dict) or not isinstance(anchor.get('path'), list):
                raise UnsafeSourceEdit('No unique link from this passage to the original XML is stored.')
            element = _element_at(root, anchor['path'])
            if anchor.get('tag') and _local(element) != anchor['tag'].rsplit('}', 1)[-1]:
                raise UnsafeSourceEdit('The original XML position does not match the stored passage.')
            native[passage['id']] = element
            _apply_text(element, passage['original_text'], passage['text'], keep)
    except UnsafeSourceEdit as error:
        warning = 'Generated derived TEI for ' + ', '.join(sorted({p['work'] for p in passages})) + ': ' + str(error)
        return _derived(passages, source, revision, str(error)), 'derived', [warning]
    _add_annotations(root, passages, revision, native)
    return ET.ElementTree(root), 'source-preserving', []


def export_project(model, destination=None, before_publish=None):
    """Publish a complete immutable export directory and atomically select it.

    ``destination`` optionally selects an export root inside the project folder.
    Returned file paths are relative to the project.  ``current.json`` is updated
    only after all files exist and the working revision has been checked again.
    """
    folder = Path(model.folder).resolve()
    payload = deepcopy(model.export_data())
    revision = payload.get('revision') or model.revision
    if not re.fullmatch(r'[0-9a-f]{64}', revision) or _current_revision(model) != revision:
        raise ExportConflict('The working text changed. Restart the export.')
    base = Path(destination).resolve() if destination else folder / 'working-exports'
    if not base.is_relative_to(folder) or base == folder:
        raise ValueError('Exports must be saved in a dedicated subfolder inside the project.')
    base.mkdir(parents=True, exist_ok=True)
    final = base / revision
    warnings, fidelity, filenames = [], {}, []
    sources = {source['id']: source for source in payload.get('sources') or []}
    for source in sources.values():
        if not source.get('path'):
            continue
        archived = (folder / source['path']).resolve()
        if (not archived.is_relative_to(folder) or not archived.is_file() or
                (source.get('sha256') and hashlib.sha256(archived.read_bytes()).hexdigest() != source['sha256'])):
            raise ValueError('The archived original text is missing or changed. Export aborted.')
    if final.exists():
        existing = json.loads((final / 'manifest.json').read_text(encoding='utf-8'))
        if existing.get('revision') != revision:
            raise ValueError('A different export already exists for this revision. Not overwritten.')
        for relative, digest in existing.get('sha256', {}).items():
            exported = (final / relative).resolve()
            if (not exported.is_relative_to(final) or not exported.is_file() or
                    hashlib.sha256(exported.read_bytes()).hexdigest() != digest):
                raise ValueError('A saved export was modified. Not overwritten.')
        if before_publish:
            before_publish()
        if _current_revision(model) != revision:
            raise ExportConflict('The working text changed during export. Files were not updated.')
        # Exports made before antconc-lemmata existed gain only the missing lemma files; every
        # saved file stays as it is. Each is derived from its saved tagged file, so both folders
        # keep the same files and lines even where that export used an older file layout.
        saved_hashes = existing.get('sha256', {})
        added = [(LEMMA_FOLDER + name[len('antconc-tagged'):],
                  lemmata_of_tagged((final / name).read_text(encoding='utf-8')))
                 for name in sorted(saved_hashes) if name.startswith('antconc-tagged/')
                 and LEMMA_FOLDER + name[len('antconc-tagged'):] not in saved_hashes]
        if added:
            for name, text in added:
                path = final / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8', newline='\n')
                existing.setdefault('sha256', {})[name] = hashlib.sha256(path.read_bytes()).hexdigest()
                existing['files'].append((final / name).relative_to(folder).as_posix())
            saved = dict(existing, files=[f for f in existing['files'] if not f.endswith('/manifest.json')])
            atomic_json(final / 'manifest.json', saved)
        relative = (final / 'manifest.json').relative_to(folder).as_posix()
        if relative not in existing['files']:
            existing['files'].append(relative)
        atomic_json(base / 'current.json', existing)
        return existing
    by_source = defaultdict(list)
    by_work = defaultdict(list)
    for passage in payload['passages']:
        by_source[passage.get('source_id')].append(passage)
        by_work[passage['work']].append(passage)
    with tempfile.TemporaryDirectory(prefix='.working-export-', dir=base) as temporary:
        stage = Path(temporary)

        def write(relative, value):
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding='utf-8', newline='\n')
            filenames.append(relative)

        for relative, text in _antconc_texts(by_work):
            write(relative, text)
        used = set()
        for source_id, passages in by_source.items():
            source = sources.get(source_id) or {'id': source_id, 'kind': 'unknown'}
            stem = unique_name(_safe_name(passages[0]['work']), used)
            if source.get('path'):
                tree, mode, notes = _source_xml(folder, source, passages, revision, set(payload.get('options', {}).get('keep') or []))
            else:
                tree, mode, notes = _derived(passages, source, revision, 'Original source unavailable.'), 'derived', []
            for passage in passages:
                fidelity[passage['work']] = mode
            warnings.extend(notes)
            xml = ET.tostring(tree.getroot(), encoding='unicode', xml_declaration=False)
            ET.fromstring(xml)
            write('xml/' + stem + '.xml', '<?xml version="1.0" encoding="UTF-8"?>\n' + xml + '\n')
        write('working-edition.json', json.dumps(payload, ensure_ascii=False, indent=2))
        evidence, sheet = [], io.StringIO()
        writer = csv.writer(sheet, delimiter='\t', lineterminator='\n')
        writer.writerow(review.COLUMNS + review.SCOPE_COLUMNS + [review.CRASIS_COLUMN, 'stable id', 'status'])
        for passage in payload['passages']:
            forms = [t['form'] for t in passage['tokens']]
            for index, token in enumerate(passage['tokens']):
                values = _annotation(token)
                raw = token.get('record') or {}
                status = 'stale' if values.get('stale') else 'context changed' if values.get('context_stale') else 'reviewed' if values.get('reviewed') else 'unreviewed'
                current = dict(raw, work=passage['work'], citation=passage['citation'], token=token['token'],
                               uid=token['uid'], form=token['form'], lemma=values.get('lemma', ''), pos=values.get('pos', 'X'),
                               expansion=values.get('expansion') or [token['form']],
                               manual_crasis=values.get('manual_crasis'),
                               reviewed=bool(values.get('reviewed')),
                               working_revision=revision, annotation_status=status,
                               prior_annotations=token.get('prior_annotations', []))
                evidence.append(json.dumps(current, ensure_ascii=False, allow_nan=False))
                crasis_cell = json.dumps(values['manual_crasis'], ensure_ascii=False) if values.get('manual_crasis') else ''
                writer.writerow([token['form'], raw.get('lemma', ''), raw.get('pos', 'X'), 1, raw.get('resp') or '', '',
                                 passage['citation'], review.context(forms, index), values.get('lemma', ''), values.get('pos', 'X'),
                                 passage['work'], passage['citation'], token['token'], crasis_cell, token['uid'], status])
        write('analysis-review.jsonl', '\n'.join(evidence) + ('\n' if evidence else ''))
        write('lemma-review.tsv', review.BOM + sheet.getvalue())
        total = sum(len(p['tokens']) for p in payload['passages'])
        stale = sum(bool(_annotation(t).get('stale')) for p in payload['passages'] for t in p['tokens'])
        checked = sum(bool(_annotation(t).get('reviewed')) for p in payload['passages'] for t in p['tokens'])
        report = ['AG-Scaife2AntConc — working-text export', 'Revision: ' + revision,
                  f'Words: {total} · reviewed analyses: {checked} · words needing re-analysis: {stale}',
                  'XML: preserves the current spelling and linked standOff annotations.',
                  'Offsets: zero-based Unicode code-point offsets into exact working-passages text; end exclusive.',
                  'Prior analyses of edited words are kept in history and not exported as active lemmata.']
        report += [work + ': ' + ('source-preserving XML' if mode == 'source-preserving' else 'derived TEI') for work, mode in fidelity.items()]
        report += warnings
        write('report.txt', '\n'.join(report) + '\n')
        manifest = {'schema': 1, 'revision': revision, 'created_utc': datetime.now(timezone.utc).isoformat(),
                    'directory': final.relative_to(folder).as_posix(),
                    'files': [(final / name).relative_to(folder).as_posix() for name in filenames],
                    'fidelity': fidelity, 'warnings': warnings,
                    'counts': {'words': total, 'reviewed': checked, 'stale': stale},
                    'sha256': {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in filenames}}
        write('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        manifest['files'].append((final / 'manifest.json').relative_to(folder).as_posix())
        if _current_revision(model) != revision:
            raise ExportConflict('The working text changed during export. Files were not updated.')
        if before_publish:
            before_publish()
        if _current_revision(model) != revision:
            raise ExportConflict('The working text changed during export. Files were not updated.')
        if final.exists():
            raise ExportConflict('Another export completed for the same revision. Please try again.')
        os.replace(stage, final)
        atomic_json(base / 'current.json', manifest)
    return manifest
