"""Required GreTa lemma evidence: a pinned, task-fine-tuned, CPU ONNX model.

This is pyaegean's GreTa derivative, not the unfine-tuned bowphs/GreTa checkpoint and not
Celano's OGA checkpoint. Its input is one inflected word (no sentence-context claim).
We run the neural model directly; the release's gold lookup is deliberately not used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import unicodedata
import urllib.request

URL = ('https://github.com/ryanpavlicek/pyaegean/releases/download/'
       'grc-lemma-neural-v1/grc-lemma-neural.tar.gz')
SHA256 = '38126872e7a5be6389054062d4789ce5b6fc7e84327b07c2b93649a6f0f1a228'
FILES = ('encoder_model.onnx', 'decoder_model.onnx', 'tokenizer.json')
NAME = 'GreTa / pyaegean grc-lemma-neural-v1 (int8, word-form input)'
ADAPTER_VERSION = '1'   # bump when generation/cleaning changes what is stored for a form
LICENSE = 'CC BY-SA 4.0; AGDT CC BY-SA 3.0, Pedalion and Gorman CC BY-SA 4.0'


class GretaError(RuntimeError):
    pass


def default_path():
    return os.environ.get('AG_GRETA_MODEL') or os.path.join(
        os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/.cache'), 'AG-Scaife2AntConc', 'GreTa-v1')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verified(path):
    path = Path(path)
    try:
        info = json.loads((path / 'model-info.json').read_text(encoding='utf-8'))
        return (info.get('archive_sha256') == SHA256 and info.get('fine_tuned') is True and
                all(digest(path / name) == info['sha256'][name] for name in FILES))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare(path=None, log=print, archive_path=None):
    """Download and verify only the pinned trained model. Extract three regular files safely."""
    target = Path(path or default_path()).resolve()
    if verified(target):
        return str(target)
    if target.exists() and any(p.name not in FILES + ('model-info.json',) or not p.is_file() for p in target.iterdir()):
        raise GretaError(f'Ο φάκελος {target} περιέχει άλλα αρχεία. Επιλέξτε νέο φάκελο μόνο για το GreTa.')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='greta-download-', dir=target.parent) as temp:
        archive = Path(archive_path) if archive_path else Path(temp) / 'model.tar.gz'
        if not archive_path:
            log('Λήψη του εκπαιδευμένου GreTa (~232 MB)…')
            req = urllib.request.Request(URL, headers={'User-Agent': 'AG-Scaife2AntConc/0.2'})
            with urllib.request.urlopen(req, timeout=60) as response, archive.open('wb') as out:
                shutil.copyfileobj(response, out)
        if digest(archive) != SHA256:
            raise GretaError('Το αρχείο GreTa δεν πέρασε τον έλεγχο SHA-256. Επαναλάβετε τη λήψη.')
        extracted = Path(temp) / 'model'
        extracted.mkdir()
        with tarfile.open(archive, 'r:gz') as tar:
            for name in FILES:
                matches = [m for m in tar.getmembers() if Path(m.name).name == name and m.isfile()]
                if len(matches) != 1:
                    raise GretaError(f'Λείπει ή επαναλαμβάνεται το {name} στο αρχείο GreTa.')
                with tar.extractfile(matches[0]) as source, (extracted / name).open('wb') as out:
                    shutil.copyfileobj(source, out)
        info = {'name': NAME, 'url': URL, 'archive_sha256': SHA256, 'license': LICENSE,
                'input': 'word-form', 'fine_tuned': True,
                'sha256': {name: digest(extracted / name) for name in FILES}}
        (extracted / 'model-info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
        previous = None
        if target.exists():
            # Only a dedicated directory of known model files reaches this branch. Keep it
            # until the verified replacement is in place; do not overwrite arbitrary folders.
            previous = Path(tempfile.mkdtemp(prefix='greta-previous-', dir=target.parent))
            previous.rmdir()
            os.replace(target, previous)
        try:
            os.replace(extracted, target)
        except OSError:
            if previous is not None:
                os.replace(previous, target)
            raise
        if previous is not None:
            for old in previous.iterdir():
                old.unlink()
            previous.rmdir()
    return str(target)


def clean(text):
    text = re.sub(r'\d+$', '', unicodedata.normalize('NFC', text.strip()))
    if text.endswith('σ'):
        text = text[:-1] + 'ς'
    if not text or any(c.isspace() for c in text) or not all(c.isalpha() or unicodedata.combining(c) for c in text):
        return None
    return text if any('\u0370' <= c <= '\u03ff' or '\u1f00' <= c <= '\u1fff' for c in text) else None


class Greta:
    """Neural generation and teacher-forced candidate scores. Scores are not calibrated confidence."""

    def __init__(self, path=None, batch_size=8, progress=None, cache_dir=None):
        self.path = Path(path or default_path())
        self.name = NAME
        self.batch_size = batch_size
        self.progress = progress
        self.predictions, self.scores = {}, {}
        self.skipped = set()
        if not verified(self.path):
            raise GretaError(f'Δεν βρέθηκε έγκυρο εκπαιδευμένο GreTa στο {self.path}. '
                             'Τρέξτε --prepare-greta ή ξανατρέξτε την εγκατάσταση.')
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:
            raise GretaError('Το GreTa χρειάζεται onnxruntime και tokenizers. Ξανατρέξτε την εγκατάσταση.') from error
        self.np = np
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(4, os.cpu_count() or 1)
        options.log_severity_level = 3
        self.encoder = ort.InferenceSession(str(self.path / FILES[0]), options, providers=['CPUExecutionProvider'])
        self.decoder = ort.InferenceSession(str(self.path / FILES[1]), options, providers=['CPUExecutionProvider'])
        self.decoder_inputs = {v.name for v in self.decoder.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(self.path / 'tokenizer.json'))
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        if isinstance(self.encoder.get_inputs()[0].shape[0], int):
            self.batch_size = 1
        from .inference_cache import Cache
        self.disk = Cache('greta', {'model': SHA256, 'onnxruntime': ort.__version__,
                                    'adapter': ADAPTER_VERSION, 'batch_size': self.batch_size}, cache_dir)

    def _encode(self, forms):
        np = self.np
        encoded = [self.tokenizer.encode(form).ids for form in forms]
        width = max(map(len, encoded))
        ids = np.zeros((len(forms), width), dtype=np.int64)
        mask = np.zeros_like(ids)
        for i, row in enumerate(encoded):
            ids[i, :len(row)], mask[i, :len(row)] = row, 1
        return self.encoder.run(None, {'input_ids': ids, 'attention_mask': mask})[0], mask

    def _decode(self, ids, hidden, mask):
        feed = {'input_ids': ids, 'encoder_hidden_states': hidden, 'encoder_attention_mask': mask}
        return self.decoder.run(None, {k: v for k, v in feed.items() if k in self.decoder_inputs})[0]

    def predict_many(self, forms):
        from .inference_cache import text_key
        forms = list(dict.fromkeys(unicodedata.normalize('NFC', f) for f in forms))
        unknown = [f for f in forms if f not in self.predictions]
        stored = self.disk.get_many(text_key(f) for f in unknown) if unknown else {}
        for form in unknown:
            saved = stored.get(text_key(form))
            if saved is not None:
                self.predictions[form] = saved['lemma']
                if saved['skipped']:
                    self.skipped.add(form)
        new = [f for f in unknown if f not in self.predictions]
        pending = []
        for form in forms:
            if form not in self.predictions:
                if len(self.tokenizer.encode(form).ids) > 32:
                    self.predictions[form] = None
                    self.skipped.add(form)
                else:
                    pending.append(form)
        np = self.np
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start:start + self.batch_size]
            hidden, mask = self._encode(batch)
            ids = np.zeros((len(batch), 1), dtype=np.int64)
            finished = np.zeros(len(batch), dtype=bool)
            generated = [[] for _ in batch]
            for _ in range(32):
                nxt = self._decode(ids, hidden, mask)[:, -1].argmax(axis=-1)
                for i, token in enumerate(nxt):
                    if not finished[i] and token != 1:
                        generated[i].append(int(token))
                finished |= nxt == 1
                ids = np.concatenate([ids, np.where(finished, 1, nxt)[:, None]], axis=1)
                if finished.all():
                    break
            for i, form in enumerate(batch):
                self.predictions[form] = clean(self.tokenizer.decode(generated[i], skip_special_tokens=True)) if finished[i] else None
                if not finished[i]:
                    self.skipped.add(form)
            if self.progress and (start // self.batch_size % 40 == 0 or start + len(batch) == len(pending)):
                self.progress('generation', start + len(batch), len(pending))
        self.disk.put_many({text_key(f): {'lemma': self.predictions[f], 'skipped': f in self.skipped} for f in new})
        return {f: self.predictions[f] for f in forms}

    def score_many(self, pairs):
        """Mean token log probability for each (expanded form, candidate lemma), including EOS."""
        pairs = list(dict.fromkeys((unicodedata.normalize('NFC', f), unicodedata.normalize('NFC', l))
                                   for f, l in pairs if f and l))
        pending = [p for p in pairs if p not in self.scores]
        np = self.np
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start:start + self.batch_size]
            valid = []
            for pair in batch:
                target = self.tokenizer.encode(pair[1]).ids
                if len(target) > 32 or len(self.tokenizer.encode(pair[0]).ids) > 32:
                    self.scores[pair] = float('-inf')
                    continue
                valid.append((pair, target))
            if not valid:
                continue
            hidden, mask = self._encode([p[0] for p, _ in valid])
            width = max(len(target) for _, target in valid)
            ids = np.zeros((len(valid), width), dtype=np.int64)
            for i, (_, target) in enumerate(valid):
                ids[i, 1:len(target)] = target[:-1]
            logits = self._decode(ids, hidden, mask).astype(np.float64)
            for i, (pair, target) in enumerate(valid):
                row = logits[i, :len(target)]
                maximum = row.max(axis=-1)
                logsum = maximum + np.log(np.exp(row - maximum[:, None]).sum(axis=-1))
                self.scores[pair] = float((row[np.arange(len(target)), target] - logsum).mean())
            if self.progress and (start // self.batch_size % 40 == 0 or start + len(batch) == len(pending)):
                self.progress('scoring', start + len(batch), len(pending))
        return {p: self.scores[p] for p in pairs}

    def close(self):
        self.disk.close()


def main():
    import argparse
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Prepare the pinned, fine-tuned GreTa ONNX lemmatizer.')
    parser.add_argument('--output', help='model directory (default: application cache)')
    parser.add_argument('--archive', help='release tar.gz for offline preparation')
    args = parser.parse_args()
    try:
        print(prepare(args.output, archive_path=args.archive))
    except (OSError, GretaError) as error:
        sys.exit(str(error))


if __name__ == '__main__':
    main()
