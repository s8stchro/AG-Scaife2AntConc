"""Data-only analysis snapshots: corrections can be exported without loading the models again."""
import copy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from ag_diogenes_spacy_txt2xml import __version__ as CORE_VERSION
from ag_diogenes_spacy_txt2xml.tokenize import Word
from . import __version__
from .pipeline import Options, SetupError

SCHEMA = 1
STATE_FIELDS = ('lemma', 'pos', 'msd', 'resp', 'crasis', 'crasis_msd', 'expansion',
                'lemma_id', 'evidence', 'review_reasons', 'reviewed')


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def signature(tokens):
    plain = []
    for token in tokens:
        data = asdict(token)
        if isinstance(token, Word):
            data = {k: data[k] for k in ('pieces', 'lang', 'space')}
            if token.evidence and token.evidence[0].get('kind') == 'oga':
                data['oga'] = token.evidence[0]['oga']
        plain.append(data)
    return hashlib.sha256(json.dumps(plain, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def options_data(opts):
    return dict(asdict(opts), keep=sorted(opts.keep), corrections=None, replace=False, session=True)


class Capture:
    """Wrap the real lemmatizer. Snapshot before manual corrections mutate any word."""
    def __init__(self, engine, paths, opts):
        self.engine, self.paths, self.opts = engine, [str(Path(p).resolve()) for p in paths], opts
        self.entries = []

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def __call__(self, tokens, words):
        fingerprint = signature(tokens)
        stats = self.engine(tokens, words)
        states = [{name: copy.deepcopy(getattr(w, name)) for name in STATE_FIELDS} for w in words]
        self.entries.append({'signature': fingerprint, 'states': states, 'stats': dict(stats)})
        return stats

    def save_session(self, path, evidence_path):
        provider = getattr(self.engine, 'provider', None)
        metadata = {'dio': '' if provider == 'oga' else self.engine.dio,
                    'context': '' if provider == 'oga' else self.engine.spacy.name,
                    'greta': '' if provider == 'oga' else self.engine.greta.name,
                    'provider': provider, 'description': self.engine.description(),
                    'sources': self.engine.sources()}
        write_snapshot(path, self.paths, self.opts, self.entries, metadata, evidence_path)


def write_snapshot(path, paths, opts, entries, metadata, evidence_path):
    payload = {'schema': SCHEMA, 'app_version': __version__, 'core_version': CORE_VERSION,
               'paths': paths, 'options': options_data(opts), 'entries': entries,
               'metadata': metadata, 'evidence_sha256': file_digest(evidence_path)}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')


class Saved:
    replaying = True

    def __init__(self, payload):
        from collections import Counter
        self.Counter = Counter
        self.paths, self.entries, self.metadata = payload['paths'], payload['entries'], payload['metadata']
        values = dict(payload['options'])
        values['keep'] = frozenset(values['keep'])
        self.opts = Options(**values)
        self.index = 0
        self.heads = set()  # current source labels do not need legacy OdyCy headword checks
        self.provider = self.metadata.get('provider')
        self.dio = self.metadata['dio']
        self.spacy = SimpleNamespace(name=self.metadata['context'])
        self.greta = SimpleNamespace(name=self.metadata['greta'])
        self.morpho = None  # replay never sends requests or reports new Morpho requests

    @classmethod
    def load(cls, folder):
        try:
            folder = Path(folder)
            payload = json.loads((folder / 'analysis-session.json').read_text(encoding='utf-8'))
            if (payload['schema'] != SCHEMA or payload['app_version'] not in ('0.3.0', '0.3.1', '0.3.2', '0.4.0', '0.4.1', '0.4.2', '0.4.3', '0.4.4', '0.5.0', '0.5.1', '0.5.2', '0.6.0', '0.6.1', '0.6.2', '0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', __version__) or
                    payload['core_version'] not in ('0.3.0', '0.3.1', '0.3.2', '0.3.3', '0.3.4', '0.4.0', '0.4.1', '0.4.2', '0.4.3', '0.4.4', '0.4.5', CORE_VERSION) or
                    payload['evidence_sha256'] != file_digest(folder / 'analysis-review.jsonl')):
                raise ValueError('snapshot/version mismatch')
            saved = cls(payload)
            if not saved.paths or not saved.entries or not all(isinstance(p, str) for p in saved.paths):
                raise ValueError('empty snapshot')
            for entry in saved.entries:
                if len(entry['signature']) != 64 or not isinstance(entry['states'], list):
                    raise ValueError('invalid snapshot')
                for state in entry['states']:
                    if set(state) != set(STATE_FIELDS):
                        raise ValueError('invalid word state')
            if not all(isinstance(saved.metadata[k], str) for k in ('dio', 'context', 'greta', 'description')):
                raise ValueError('invalid metadata')
            if not isinstance(saved.metadata['sources'], list):
                raise ValueError('invalid source metadata')
            return saved
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise SetupError('No compatible saved analyses were found. '
                             'Corrections can still be saved; re-exporting requires re-analysing the original XML files.') from error

    def reset(self):
        self.index = 0

    def __call__(self, tokens, words):
        if self.index >= len(self.entries):
            raise SetupError('The input texts changed. A fresh analysis is required.')
        entry = self.entries[self.index]
        if entry['signature'] != signature(tokens) or len(entry['states']) != len(words):
            raise SetupError('The text or cleansing options changed. A fresh analysis is required.')
        self.index += 1
        for word, state in zip(words, entry['states']):
            for name in STATE_FIELDS:
                setattr(word, name, copy.deepcopy(state[name]))
        return self.Counter(entry['stats'])

    def description(self):
        return self.metadata['description'] + ' Re-exported from saved analyses; no new neural inference.'

    def sources(self):
        return self.metadata['sources']

    def save_session(self, path, evidence_path):
        if self.index != len(self.entries):
            raise SetupError('The number of texts changed. A fresh analysis is required.')
        write_snapshot(path, self.paths, self.opts, self.entries, self.metadata, evidence_path)

    def export_options(self, folder):
        return replace(self.opts, corrections=str(Path(folder) / 'lemma-review.tsv'), session=True)
