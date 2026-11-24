"""Editable working editions with immutable sources and revision-linked annotations.

This layer stores editorial actions. It supplies no new linguistic decisions:
token boundaries and analysis spelling come from the existing normalizer/tokenizer.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import time

from ag_diogenes_spacy_txt2xml.normalize import APOS_LIKE, LOOKALIKES, Normalizer
from ag_diogenes_spacy_txt2xml.source import Line
from ag_diogenes_spacy_txt2xml.tokenize import Word, tokenize
from ag_diogenes_spacy_txt2xml.review_priority import priority_reasons

from . import pipeline, review
from .reader import local, NOT_CITED
from .session import Saved
from .web_storage import atomic_json
from .workspace import Workspace, POS_NAMES, identity

SCHEMA = 1
FILENAME = 'working-text.json'
TEXT_STALE = 'text changed; annotation needs review'
CONTEXT_STALE = 'context changed; annotation needs review'


class Conflict(ValueError):
    """The durable working edition changed since the caller loaded it."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uid():
    return uuid.uuid4().hex


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _json_bytes(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8')


@contextmanager
def _file_lock(folder):
    """Serialize model commits across the server and its background worker."""
    path = Path(folder) / '.working-text' / 'write.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            deadline=time.monotonic()+5
            while True:
                try:
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1); break
                except OSError:
                    if time.monotonic()>=deadline: raise Conflict('The working text is being saved by another process. Please try again.')
                    time.sleep(0.02)
            try: yield
            finally:
                stream.seek(0); msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise Conflict('The working text is being saved by another process. Please try again.')
                    time.sleep(0.02)
            try: yield
            finally: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _fold(text):
    out, positions = [], []
    for i, char in enumerate(text):
        value = ('’' if char in APOS_LIKE else char).translate(LOOKALIKES)
        for letter in unicodedata.normalize('NFD', value):
            out.append(letter); positions.append(i)
    return ''.join(out), positions


def token_spans(text):
    """Existing Greek tokenization, linked back to literal user-text character spans."""
    normalized = Normalizer()(text)
    old, positions = _fold(text)
    new, normalized_positions = _fold(normalized)
    mapped = {}
    for block in SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            mapped[block.b + offset] = positions[block.a + offset]
    decomp = defaultdict(list)
    for i, position in enumerate(normalized_positions):
        decomp[position].append(i)
    cursor, result = 0, []
    for word in tokenize([Line(None, None, normalized)], tlg_gap=False):
        if not isinstance(word, Word) or word.lang != 'grc':
            continue
        form = pipeline.word_text(word)
        if not form:
            continue  # Damaged words remain literal text and are not invented annotation units.
        literal = ''.join(value if kind in ('t', 'c') else '!' * value if kind == 'gap' else ''
                          for kind, value in word.pieces)
        start = normalized.find(literal, cursor)
        if start < 0:
            raise ValueError('Could not safely locate a word position in the text.')
        stop = start + len(literal); cursor = stop
        candidates = [mapped[j] for i in range(start, stop) for j in decomp[i] if j in mapped]
        if not candidates:
            raise ValueError('Could not safely locate a word position in the text.')
        a, b = min(candidates), max(candidates) + 1
        while b < len(text) and unicodedata.combining(text[b]):
            b += 1
        check = [w for w in tokenize([Line(None, None, Normalizer()(text[a:b]))], tlg_gap=False)
                 if isinstance(w, Word) and w.lang == 'grc' and pipeline.word_text(w)]
        if len(check) != 1 or pipeline.word_text(check[0]) != form:
            raise ValueError('The edited spelling could not be aligned safely to its words.')
        result.append({'form': form, 'start': a, 'end': b})
    if any(a['end'] > b['start'] for a, b in zip(result, result[1:])):
        raise ValueError('Word offsets overlap.')
    return result


def _stable_tokens(old, new, old_text, new_text):
    """Retain identities only where every longest token alignment agrees.

    Whole-passage edits do not disclose which identical repetition was inserted
    or removed. Ambiguous tokens remain in history instead of inheriting a
    neighbour's manually reviewed annotation.
    """
    left=[(t['form'],old_text[t['start']:t['end']]) for t in old]
    right=[(t['form'],new_text[t['start']:t['end']]) for t in new]
    n,m=len(left),len(right)
    if n*m>1_000_000:
        raise ValueError('The passage has too many words for safe alignment. Split the edit into smaller passages.')
    def lengths(a,b):
        out=[[0]*(len(b)+1) for _ in range(len(a)+1)]
        for i,x in enumerate(a,1):
            for j,y in enumerate(b,1):
                out[i][j]=out[i-1][j-1]+1 if x==y else max(out[i-1][j],out[i][j-1])
        return out
    prefix=lengths(left,right); suffix=lengths(left[::-1],right[::-1]); optimum=prefix[n][m]
    found={}
    for i,value in enumerate(left):
        partners=[j for j,other in enumerate(right) if value==other and
            prefix[i][j]+1+suffix[n-i-1][m-j-1]==optimum]
        if len(partners)==1 and not any(prefix[i][j]+suffix[n-i-1][m-j]==optimum for j in range(m+1)):
            found[partners[0]]=old[i]
    return found


def _xml_anchors(path):
    """Structural anchors only. Complex inline content stays explicitly non-faithful."""
    root = ET.parse(path).getroot()
    found = defaultdict(list)
    def walk(el, chain, path):
        tag = local(el)
        n = el.get('n')
        inner = chain
        if tag == 'div' and n and el.get('type') != 'edition' and (el.get('subtype') or '').lower() not in NOT_CITED:
            inner = chain + [n]
            if not any(local(d) == 'div' and d.get('n') for d in el.iter() if d is not el):
                found['.'.join(inner)].append({'path': path, 'tag': tag, 'faithful': len(el) == 0})
        if tag == 'l' and n and el.get('style') != 'hidden':
            found['.'.join(chain + [n])].append({'path': path, 'tag': tag, 'faithful': len(el) == 0})
        for i, child in enumerate(el):
            walk(child, inner, path + [i])
    walk(root, [], [])
    return {key: values[0] for key, values in found.items() if len(values) == 1}


def _annotation(record, workspace=None):
    lemma, pos = workspace.values(record) if workspace else (record.get('lemma', ''), record.get('pos', 'X'))
    expansion = workspace.expansion(record) if workspace else record.get('expansion', [record['form']])
    manual = workspace.manual_crasis(record) if workspace else record.get('manual_crasis')
    return {'lemma': lemma, 'pos': pos, 'expansion': deepcopy(expansion), 'manual_crasis': deepcopy(manual),
            'reviewed': workspace.checked(record) if workspace else bool(record.get('reviewed')),
            'manual': bool(workspace.matching_correction(record) or record.get('machine_before_correction') or record.get('reviewed'))
                      if workspace else bool(record.get('manual') or record.get('reviewed')),
            'stale': False, 'context_stale': False}


class WorkingText:
    def __init__(self, folder, data, revision=None):
        self.folder = Path(folder).resolve()
        self.path = self.folder / FILENAME
        self.sheet = self.folder / 'lemma-review.tsv'
        self.data = data
        self.revision = revision or _digest(_json_bytes(data))
        self._index()

    @classmethod
    def exists(cls, folder):
        return (Path(folder) / FILENAME).is_file()

    @classmethod
    def load(cls, folder):
        path = Path(folder) / FILENAME
        try:
            content = path.read_bytes()
            data = json.loads(content)
            if data['schema'] != SCHEMA or not isinstance(data['passages'], list) or not isinstance(data['sources'], list):
                raise ValueError('unsupported working edition')
            obj = cls(folder, data, _digest(content))
            obj._validate_sources()
            obj._check()
            return obj
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError('Could not read the working edition. Files were preserved.') from error

    @classmethod
    def ensure(cls, folder):
        if cls.exists(folder):
            return cls.load(folder)
        workspace, saved = Workspace(folder), Saved.load(folder)
        return cls._build(folder, saved.paths, saved.opts, workspace, saved)

    @classmethod
    def create(cls, folder, paths, opts=None):
        folder = Path(folder).resolve()
        if folder.exists() and any(folder.iterdir()):
            raise ValueError('Choose an empty folder for a new working edition.')
        return cls._build(folder, paths, opts or pipeline.Options(), None)

    @classmethod
    def _build(cls, folder, paths, opts, workspace, saved=None):
        folder = Path(folder).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        with _file_lock(folder):
            if cls.exists(folder):
                return cls.load(folder)
            return cls._build_unlocked(folder, paths, opts, workspace, saved)

    @classmethod
    def _build_unlocked(cls, folder, paths, opts, workspace, saved=None):
        folder = Path(folder).resolve()
        if (folder / FILENAME).exists():
            raise Conflict('A working edition already exists in this folder.')
        folder.mkdir(parents=True, exist_ok=True)
        data = {'schema': SCHEMA, 'id': _uid(), 'created': _now(), 'sequence': 0,
                'sources': [], 'passages': [], 'history': [], 'options': asdict(opts),
                'legacy_hashes': {}}
        data['options']['keep'] = sorted(opts.keep)
        if saved:
            data['imported_analysis_metadata'] = deepcopy(saved.metadata)
        if workspace:
            for name in ('lemma-review.tsv', 'analysis-review.jsonl'):
                content = (folder / name).read_bytes()
                data['legacy_hashes'][name] = _digest(content)
                backup = folder / '.working-text' / 'legacy' / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(content)
        works = pipeline.plan(paths, opts.keep)
        legacy = pipeline.legacy_names(works)
        for work_index,(path, work, name, _label) in enumerate(works):
            if name is None:
                raise ValueError('Could not read the source text.')
            source_id = _uid(); raw = Path(path).read_bytes()
            archived = Path('.working-text') / 'sources' / source_id / Path(path).name
            (folder / archived).parent.mkdir(parents=True, exist_ok=True)
            (folder / archived).write_bytes(raw)
            is_oga = pipeline.oga.is_oga(path)
            data['sources'].append({'id': source_id, 'path': archived.as_posix(), 'original_path': str(Path(path).resolve()),
                                    'kind': 'oga' if is_oga else 'xml', 'works': [name], 'sha256': _digest(raw)})
            anchors = {} if is_oga else _xml_anchors(path)
            records_by_citation = defaultdict(list)
            machine_crasis = {}
            if saved:
                # Snapshot states include damaged words not present in review records.
                original_tokens,_=pipeline.tokens_for(work,opts)
                original_words=[w for w in original_tokens if isinstance(w,Word) and w.lang=='grc']
                states=saved.entries[work_index]['states']
                if len(original_words)!=len(states):
                    raise ValueError('The saved session does not match the source text.')
                state_by_word={id(w):state for w,state in zip(original_words,states)}
                for _,lines in pipeline.units(original_tokens):
                    for citation,items in lines:
                        for ordinal,(_,_,word) in enumerate(items,1):
                            state=state_by_word[id(word)]
                            if state.get('crasis'):
                                machine_crasis[citation,ordinal]=deepcopy(state['crasis'])
            if workspace:
                # Analyses saved up to 0.7.1 name the work after the catalogue, not the input file.
                saved_name = name if any(r['work'] == name for r in workspace.records) else legacy[name]
                for record in workspace.records:
                    if record['work'] == saved_name:
                        records_by_citation[record['citation']].append(record)
            elif is_oga:
                for _,lines in pipeline.units(work.tokens):
                    for citation,items in lines:
                        for ordinal,(form,_,word) in enumerate(items,1):
                            record=cls._blank_record(name,citation,ordinal,form)
                            record.update(lemma=word.lemma or '',pos=word.pos or 'X',resp='oga',
                                          readings=deepcopy(word.evidence),review_reasons=[] if word.lemma else ['unresolved lemma'])
                            records_by_citation[citation].append(record)
            # OGA rows preserve original punctuation and source row identities separately.
            if is_oga:
                passages = cls._oga_passages(raw, name, work, records_by_citation)
            else:
                passages = [(line.label or str(i), line.text, records_by_citation.get(line.label or str(i), []),None)
                            for i, line in enumerate(work.lines, 1)]
            for citation, text, original_records,native_spans in passages:
                spans = native_spans if native_spans is not None else token_spans(text)
                if workspace and [s['form'] for s in spans] != [r['form'] for r in original_records]:
                    # Accent-normalized legacy analyses need their exact historical analysis spelling.
                    if len(spans) != len(original_records):
                        raise ValueError(f'Words at {citation} do not match the saved analysis. The original project was preserved.')
                    if any(review.key(s['form']) != review.key(r['form']) for s,r in zip(spans,original_records)) and not opts.accents:
                        raise ValueError(f'The spelling at {citation} differs from the saved analysis.')
                passage = {'id': _uid(), 'work': name, 'citation': citation, 'source_id': source_id,
                           'source_anchor': anchors.get(citation), 'original_text': text, 'text': text,
                           'text_reviewed': False, 'history': [], 'tokens': [], 'next_token': 1}
                for i, span in enumerate(spans):
                    record = deepcopy(original_records[i]) if workspace or is_oga else cls._blank_record(name,citation,i+1,span['form'])
                    token = dict(span, uid=_uid(), token=record['token'], record=record,
                                 annotation=_annotation(record, workspace), prior_annotations=[])
                    if not workspace and not is_oga:
                        token['annotation']['stale']=True
                    if (citation,record['token']) in machine_crasis:
                        token['machine_crasis']=machine_crasis[citation,record['token']]
                    if workspace:
                        token['form'] = record['form']
                    passage['tokens'].append(token)
                passage['next_token'] = max((t['token'] for t in passage['tokens']), default=0) + 1
                if passage['tokens'] or text:
                    data['passages'].append(passage)
        obj = cls(folder, data)
        obj._validate_sources()
        atomic_json(obj.path, data)
        return cls.load(folder)

    @staticmethod
    def _oga_passages(raw, name, work, records_by_citation):
        grouped = []
        current_citation, parts, spans = None, [], []
        citations = defaultdict(int)
        sentence = 1
        for line in raw.decode('utf-8-sig').splitlines():
            if not line.strip():
                sentence += 1; continue
            if line.startswith('#'): continue
            fields = line.split('\t')
            if len(fields) != 10: continue
            misc = fields[9].split('|'); tid = misc[0]
            if not tid.startswith('t_'): continue
            reference = next((s[4:] for s in misc[1:] if s.startswith('ref=')), '')
            citation = (reference.rpartition('_')[2] or reference) if reference else f'§ {sentence}'
            if citation != current_citation:
                if current_citation is not None:
                    grouped.append((label, ' '.join(parts), records_by_citation.get(label, []),spans))
                citations[citation] += 1
                label = citation if citations[citation] == 1 else f'{citation} [{citations[citation]}]'
                current_citation, parts, spans = citation, [], []
            start=sum(map(len,parts))+len(parts)
            parts.append(fields[1])
            if fields[3]!='u' and any(pipeline.oga.is_greek_letter(c) for c in fields[1]):
                form=pipeline.word_text(Word([('t',fields[1])]))
                spans.append({'form':form,'start':start,'end':start+len(fields[1])})
        if current_citation is not None:
            grouped.append((label, ' '.join(parts), records_by_citation.get(label, []),spans))
        return grouped

    @staticmethod
    def _blank_record(work, citation, token, form):
        return {'work': work, 'citation': citation, 'token': token, 'form': form, 'lemma': '', 'pos': 'X',
                'expansion': [form], 'msd': None, 'resp': None, 'lemma_id': None, 'readings': [],
                'review_reasons': [TEXT_STALE], 'reviewed': False, 'rule_contributions': []}

    def _validate_sources(self):
        for source in self.data['sources']:
            path = (self.folder / source['path']).resolve()
            if not path.is_relative_to(self.folder) or not path.is_file() or _digest(path.read_bytes()) != source['sha256']:
                raise ValueError('The archived original text is missing or changed. No edit was applied.')

    @staticmethod
    def _indexed_record(passage, token):
        annotation = token['annotation']
        record = deepcopy(token['record'])
        record.update(work=passage['work'], citation=passage['citation'], token=token['token'],
                      uid=token['uid'], form=token['form'], lemma=annotation['lemma'], pos=annotation['pos'],
                      expansion=deepcopy(annotation['expansion']), manual_crasis=deepcopy(annotation['manual_crasis']),
                      reviewed=annotation['reviewed'] and not annotation['stale'] and not annotation['context_stale'],
                      stale=annotation['stale'], context_stale=annotation['context_stale'],
                      prior_annotations=deepcopy(token.get('prior_annotations', [])),
                      working_machine=deepcopy(token['record']), passage_id=passage['id'])
        reasons = list(record.get('review_reasons', []))
        if annotation['stale']: reasons.append(TEXT_STALE)
        if annotation['context_stale']: reasons.append(CONTEXT_STALE)
        record['review_reasons'] = list(dict.fromkeys(reasons))
        priorities = list(dict.fromkeys(priority_reasons(record) +
            ([TEXT_STALE] if annotation['stale'] else []) + ([CONTEXT_STALE] if annotation['context_stale'] else [])))
        return record, priorities

    def _index(self):
        self.records, self.by_id, self.priorities, self.contexts = [], {}, {}, {}
        self._tokens, self._passages, self._record_positions = {}, {}, {}
        for passage in self.data['passages']:
            self._passages[passage['id']] = passage
            forms = [t['form'] for t in passage['tokens']]
            for i, token in enumerate(passage['tokens']):
                record, priorities = self._indexed_record(passage, token)
                key = identity(record)
                if key in self.by_id:
                    raise ValueError('Duplicate word identifier in the working edition.')
                self._record_positions[key] = len(self.records)
                self.records.append(record); self.by_id[key] = record
                self._tokens[key] = passage, token
                self.priorities[key] = priorities
                self.contexts[key] = review.context(forms, i)

    def _index_annotations(self, keys):
        """An annotation choice changes neither word identities nor their contexts."""
        for key in dict.fromkeys(keys):
            passage, token = self._tokens[key]
            record, priorities = self._indexed_record(passage, token)
            if identity(record) != key:
                raise ValueError('The annotation edit changed the word identifier.')
            self.records[self._record_positions[key]] = record
            self.by_id[key] = record
            self.priorities[key] = priorities

    def _check(self, expected=None):
        if expected is not None and expected != self.revision:
            raise Conflict('The working edition changed. Reload the text before saving.')
        if not self.path.is_file() or _digest(self.path.read_bytes()) != self.revision:
            raise Conflict('The working edition changed in another tab. Your edit was not saved.')
        for name, digest in self.data.get('legacy_hashes', {}).items():
            path = self.folder / name
            if not path.is_file() or _digest(path.read_bytes()) != digest:
                raise Conflict('Previous results or the review sheet were modified outside the working edition. Both versions were preserved. Open the updated sheet to reconcile changes.')

    def _commit(self, expected=None, *, annotation_keys=None):
        # The rollback also covers failing to take the lock: callers change self.data before
        # committing, and a rejected edit must not be written by the next successful save.
        try:
            with _file_lock(self.folder):
                self._check(expected)
                # Validate before publishing. Only text/analysis edits require
                # rebuilding every word and copying all provider evidence.
                if annotation_keys is None:
                    self._index()
                else:
                    self._index_annotations(annotation_keys)
                self.data['sequence'] += 1
                digest = atomic_json(self.path, self.data)
                self.revision = digest or _digest(self.path.read_bytes())
        except BaseException:
            # A failed write/conflict must not leave an uncommitted edit in a cached model.
            # atomic_json replaces the file in one step, so this read never sees a partial write.
            content = self.path.read_bytes()
            self.data, self.revision = json.loads(content), _digest(content)
            self._index()
            raise

    def values(self, record):
        r = self.by_id[identity(record)]
        return r['lemma'], r['pos']

    def checked(self, record):
        return bool(self.by_id[identity(record)]['reviewed'])

    def expansion(self, record):
        return deepcopy(self.by_id[identity(record)]['expansion'])

    def manual_crasis(self, record):
        return deepcopy(self.by_id[identity(record)].get('manual_crasis'))

    def visible(self, query='', filter_name='uncertain'):
        return Workspace.visible(self, query, filter_name)

    def passage_for(self, record):
        return self.passage(self._tokens[identity(record)][0]['id'])

    def passage(self, passage_id):
        if passage_id not in self._passages:
            raise ValueError('Passage not found.')
        result = deepcopy(self._passages[passage_id])
        result['revision'] = self.revision
        result['annotation_stale'] = any(t['annotation']['stale'] or t['annotation']['context_stale'] for t in result['tokens'])
        for token in result['tokens']:
            token.update(deepcopy(token['annotation']))
        return result

    def _event(self, passage, kind, before, reason='', **extra):
        if kind=='annotation':
            uid=extra.get('token_uid')
            prior=next((t['annotation'] for t in before['tokens'] if t['uid']==uid),None)
            after=next((t['annotation'] for t in passage['tokens'] if t['uid']==uid),None)
            before={'text':before['text'],'annotation':deepcopy(prior),'token_uid':uid}
            final={'text':passage['text'],'annotation':deepcopy(after),'token_uid':uid}
        else:
            final={'text': passage['text'], 'tokens': deepcopy(passage['tokens']), 'text_reviewed': passage['text_reviewed']}
        event = {'id': _uid(), 'time': _now(), 'kind': kind, 'reason': reason,
                 'old_text': before['text'], 'new_text': passage['text'],
                 'before': before, 'after': final, **extra}
        passage['history'].append(event)
        self.data['history'].append({'id': event['id'], 'time': event['time'], 'kind': kind, 'passage_id': passage['id']})

    @staticmethod
    def _snapshot(passage):
        return {k: deepcopy(passage[k]) for k in ('text','tokens','text_reviewed')}

    def edit(self, passage_id, text, reason='', expected_revision=None, text_reviewed=True):
        self._check(expected_revision)
        if (not isinstance(text, str) or len(text) > 50000 or
                any(c in text for c in ('\ue000','\ue001','\ue002')) or
                any((ord(c) < 32 and c not in '\t\n\r') or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE,0xFFFF) for c in text)):
            raise ValueError('Enter up to 50,000 characters without control characters.')
        if not isinstance(reason,str) or len(reason)>4000:
            raise ValueError('Notes must be up to 4,000 characters.')
        passage = self._passages.get(passage_id)
        if passage is None: raise ValueError('Passage not found.')
        if text == passage['text'] and bool(text_reviewed)==passage['text_reviewed']:
            return self.passage(passage_id)
        before = self._snapshot(passage)
        spans = token_spans(text)
        old = passage['tokens']
        source = next((s for s in self.data['sources'] if s['id'] == passage['source_id']), {})
        if source.get('kind') == 'oga':
            # An unchanged imported word keeps its supplied token boundary, even
            # when the generic tokenizer would split an apostrophe inside it.
            blocks = SequenceMatcher(None, passage['text'], text, autojunk=False).get_matching_blocks()
            for token in old:
                block = next((b for b in blocks if b.a <= token['start'] and token['end'] <= b.a + b.size), None)
                if block is None:
                    continue
                start, end = token['start'] + block.b - block.a, token['end'] + block.b - block.a
                overlap = [i for i, s in enumerate(spans) if s['start'] < end and s['end'] > start]
                if overlap and spans[overlap[0]]['start'] == start and spans[overlap[-1]]['end'] == end:
                    spans[overlap[0]:overlap[-1]+1] = [{'form': token['form'], 'start': start, 'end': end}]
        retained = _stable_tokens(old,spans,passage['text'],text)
        tokens=[]; used=set()
        for index,span in enumerate(spans):
            prior=retained.get(index)
            if prior is not None and prior['uid'] not in used:
                token=deepcopy(prior); token.update(span); used.add(token['uid'])
                if text != passage['text']: token['annotation']['context_stale']=True
            else:
                number=passage['next_token']; passage['next_token']+=1
                record=self._blank_record(passage['work'],passage['citation'],number,span['form'])
                token=dict(span,uid=_uid(),token=number,record=record,annotation=_annotation(record),prior_annotations=[])
                token['annotation']['stale']=True
                # Preserve nearby historical choices as references, never as active annotations.
                token['prior_annotations']=[{'uid':t['uid'],'form':t['form'],'annotation':deepcopy(t['annotation'])}
                    for t in old if t['uid'] not in {p['uid'] for p in retained.values()}]
            tokens.append(token)
        passage.update(text=text,tokens=tokens,text_reviewed=bool(text_reviewed))
        self._event(passage,'text',before,reason)
        self._commit(expected_revision)
        return self.passage(passage_id)

    def undo(self, passage_id, revision_id, expected_revision=None):
        self._check(expected_revision)
        passage=self._passages.get(passage_id)
        if passage is None: raise ValueError('Passage not found.')
        event=next((e for e in passage['history'] if e['id']==revision_id),None)
        if event is None or event['kind'] not in ('text','undo'):
            raise ValueError('No reversible text edit was found.')
        before=self._snapshot(passage)
        state=deepcopy(event['before'])
        current={t['uid']:t for t in passage['tokens']}
        for token in state['tokens']:
            latest=current.get(token['uid'])
            if latest and latest['form']==token['form'] and latest.get('manual_version',0)>token.get('manual_version',0):
                token['prior_annotations'].append(deepcopy(token['annotation']))
                token['annotation']=deepcopy(latest['annotation'])
                token['annotation']['context_stale']=True
                token['manual_version']=latest['manual_version']
        passage.update(state)
        # next_token is intentionally never rolled back: identifiers are never reused.
        self._event(passage,'undo',before,'Undo text edit',undo_of=revision_id)
        self._commit(expected_revision)
        return self.passage(passage_id)

    def choose(self, record, lemmas, tags, all_matches=False, crasis=None):
        self._check()
        current=self.by_id[identity(record)]
        count=2 if crasis is not None else len(current['working_machine'].get('machine_expansion',current['working_machine'].get('expansion',[current['form']])))
        if len(lemmas)!=count or len(tags)!=count or any(tag not in POS_NAMES for tag in tags):
            raise ValueError('Provide a lemma and part of speech for each word in the expansion.')
        if any(not isinstance(l,str) or '+' in l or '?' in l or any(c.isspace() for c in l.strip()) for l in lemmas):
            raise ValueError('Each lemma must be a single word without + or ?.')
        if any(not l.strip() and old for l,old in zip(lemmas,current['lemma'].split('+'))):
            raise ValueError('An existing lemma cannot be cleared with an empty choice.')
        manual=deepcopy(crasis)
        if manual:
            review.ManualCrasis(manual['cut'],manual['forms']).validate(current['form'],'+'.join(lemmas),'+'.join(tags))
        targets=[r for r in self.records if identity(r)==identity(current) or (all_matches and
                  review.key(r['form'])==review.key(current['form']) and r['working_machine'].get('lemma')==current['working_machine'].get('lemma') and
                  not self.checked(r))]
        for target in targets:
            passage,token=self._tokens[identity(target)]
            prior=deepcopy(token['annotation'])
            before={'text':passage['text'],'tokens':[{'uid':token['uid'],'annotation':prior}]}
            token['prior_annotations'].append(prior)
            token['annotation'].update(lemma='+'.join(l.strip() for l in lemmas),pos='+'.join(tags),
                expansion=deepcopy(manual['forms'] if manual else token['record'].get('machine_expansion',token['record'].get('expansion',[token['form']]))),
                manual_crasis=manual,reviewed=identity(target)==identity(current),stale=False,context_stale=False)
            token['annotation']['manual']=True
            token['manual_version']=self.data['sequence']+1
            self._event(passage,'annotation',before,'Manual lemma / POS review',token_uid=token['uid'])
        self._commit(annotation_keys=[identity(target) for target in targets])

    def apply_analysis(self, passage_id, records, expected_revision=None):
        self.apply_analyses([{'passage_id':passage_id,'records':records}],expected_revision)
        return self.passage(passage_id)

    def apply_analyses(self, updates, expected_revision=None, metadata=None, *, headwords_only=False):
        """Commit a fully computed batch, never a partly completed/cancelled analysis."""
        self._check(expected_revision)
        checked=[]; seen=set()
        for update in updates:
            passage_id,records=update['passage_id'],update['records']
            passage=self._passages.get(passage_id)
            if passage is None or passage_id in seen: raise ValueError('Passage not found or listed twice.')
            seen.add(passage_id)
            if len(records)!=len(passage['tokens']) or any(review.key(r['form'])!=review.key(t['form']) for r,t in zip(records,passage['tokens'])):
                raise ValueError('The new analysis does not match the words of the current spelling.')
            checked.append((passage,records))
        run_id = _uid() if checked else None
        if checked:
            self.data.setdefault('analysis_runs', []).append({'id':run_id,'time':_now(),'metadata':deepcopy(metadata or {})})
        for passage,records in checked:
            before=self._snapshot(passage)
            for token,record in zip(passage['tokens'],records):
                previous=deepcopy(token['annotation'])
                token['record']=deepcopy(record)
                token['record'].update(work=passage['work'],citation=passage['citation'],token=token['token'],form=token['form'])
                token['record']['analysis_run'] = run_id
                token['prior_annotations'].append(previous)
                if previous.get('manual') or previous['reviewed']:
                    # Manual choices stay active for reconciliation, but changed context is not silently certified.
                    token['annotation']=previous
                else:
                    token['annotation']=_annotation(token['record'])
                    if headwords_only:
                        token['annotation']['stale'] = previous['stale']
                        token['annotation']['context_stale'] = previous['context_stale']
            self._event(passage,'headwords' if headwords_only else 'analysis',before,
                        'Diogenes / LSJ headword standardization' if headwords_only else 'New automatic analysis',analysis_run=run_id)
        if checked: self._commit(expected_revision)
        return {'revision':self.revision,'passages':[self.passage(p['id']) for p,_ in checked]}

    def export_data(self):
        data=deepcopy(self.data)
        data['revision']=self.revision
        for passage in data['passages']:
            passage['annotation_stale']=any(t['annotation']['stale'] or t['annotation']['context_stale'] for t in passage['tokens'])
            for token in passage['tokens']:
                token.update(deepcopy(token['annotation']))
                token['reviewed']=token['reviewed'] and not token['stale'] and not token['context_stale']
        return data
