"""Pinned GreBERTa joint inference, with neural/lookup decisions kept explicit.

The pyaegean preprocessing, edit-tree decoder and UD conversion are used directly.
Its full linguistic pipeline and automatic downloads are not invoked. No PyTorch
or spaCy is needed. This adapter's neural-first decoding is recorded in receipts.
"""
from dataclasses import asdict, dataclass
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unicodedata
import urllib.request

from ag_greek_rules.selection import lemma_key
from .greta import clean, digest
from .inference_cache import Cache, text_key

URL = 'https://github.com/ryanpavlicek/pyaegean/releases/download/grc-joint-v3/grc-joint.tar.gz'
SHA256 = 'f646d34a08dbf612abbe076c27188f077c2289da0b7bbbc7116bfe807112b06e'
MANIFEST_SHA256 = 'db17b79df0a88d927870a708ecd666f3afdfd5cd890302854cab700b581ade5b'
FILES = ('model.onnx', 'tokenizer.json', 'labels.json', 'lemma-scripts.json', 'lemma-lookup.json', 'manifest.json')
RUNTIME = '0.61.0'
NAME = 'GreBERTa joint / pyaegean grc-joint-v3 (CPU ONNX; neural-first)'
LICENSE = 'CC BY-SA 4.0; AGDT CC BY-SA 3.0, Gorman and Pedalion CC BY-SA 4.0'
ADAPTER_VERSION = '6'   # bump when _compose/_decode_row/_predict change what is stored for an input
TOP_K = 10              # distinct clean contextual edit-script candidates kept per token
TOP_K_SCRIPTS = 32      # raw edit scripts inspected before deduplicating/aggregating by lemma_key
BATCH = 64              # sentences per session.run; only sentences of identical subword and word length share a
                        # batch, so nothing is padded and outputs equal single calls (|Δ log p| ~ 3e-8)
CHUNK = 2048            # sentences read ahead, so that equal lengths can be grouped


def _logsumexp(a, b):
    m = max(a, b)
    return m + math.log(math.exp(a - m) + math.exp(b - m))


def _verified_receipt(path):
    """Fast receipt check before ModelBundleManifest.load verifies all bundle file digests once."""
    try:
        root = Path(path)
        receipt = json.loads((root/'model-info.json').read_text(encoding='utf-8'))
        return (receipt['archive_sha256'] == SHA256 and
                digest(root/'manifest.json') == MANIFEST_SHA256 and
                set(receipt['sha256']) == set(FILES) and
                all((root/name).is_file() for name in FILES))
    except (OSError, ValueError, KeyError, TypeError):
        return False


class JointError(RuntimeError):
    pass


def default_path():
    return os.environ.get('AG_GREBERTA_MODEL') or str(Path(
        os.environ.get('LOCALAPPDATA') or Path.home()/'.cache')/'AG-Scaife2AntConc'/'GreBERTa-joint-v3')


def verified(path):
    try:
        root = Path(path)
        receipt = json.loads((root/'model-info.json').read_text(encoding='utf-8'))
        return (receipt['archive_sha256'] == SHA256 and
                digest(root/'manifest.json') == MANIFEST_SHA256 and
                set(receipt['sha256']) == set(FILES) and
                all(digest(root/name) == receipt['sha256'][name] for name in FILES))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare(path=None, archive_path=None):
    target = Path(path or default_path()).resolve()
    if verified(target):
        return str(target)
    marker = target/'.preparing-ag-greberta'
    if target.exists() and any(target.iterdir()):
        own_partial = marker.is_file() and marker.read_text(encoding='utf-8') == 'grc-joint-v3'
        if not own_partial or any(p.name not in (*FILES, 'model-info.json', marker.name) for p in target.iterdir()):
            raise JointError(f'Ο φάκελος GreBERTa δεν είναι έγκυρος ή κενός: {target}. Επιλέξτε νέο φάκελο.')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='greberta-', dir=target.parent) as temporary:
        temporary = Path(temporary)
        archive = Path(archive_path) if archive_path else temporary/'joint.tar.gz'
        if not archive_path:
            with urllib.request.urlopen(URL, timeout=60) as response, archive.open('wb') as out:
                shutil.copyfileobj(response, out)
        if digest(archive) != SHA256:
            raise JointError('Η λήψη GreBERTa δεν πέρασε τον έλεγχο SHA-256.')
        extracted = temporary/'model'; extracted.mkdir()
        with tarfile.open(archive, 'r:gz') as bundle:
            for name in FILES:
                matches = [m for m in bundle.getmembers() if m.isfile() and not m.issym() and Path(m.name).name == name]
                if len(matches) != 1:
                    raise JointError(f'Λείπει ή επαναλαμβάνεται το {name} στο μοντέλο GreBERTa.')
                with bundle.extractfile(matches[0]) as source, (extracted/name).open('wb') as out:
                    shutil.copyfileobj(source, out)
        if digest(extracted/'manifest.json') != MANIFEST_SHA256:
            raise JointError('Το μοντέλο GreBERTa έχει μη αναμενόμενο manifest.')
        receipt = {'name': NAME, 'url': URL, 'archive_sha256': SHA256, 'license': LICENSE,
                   'runtime': 'pyaegean=='+RUNTIME, 'lemma_composition': 'neural-first',
                   'sha256': {name: digest(extracted/name) for name in FILES}}
        (extracted/'model-info.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        # Windows can keep a newly extracted directory open for scanning. Publish
        # files in the dedicated destination, with the verification receipt last.
        # An interrupted copy is never activated and can be resumed via its marker.
        target.mkdir(exist_ok=True)
        marker.write_text('grc-joint-v3', encoding='utf-8')
        for name in (*FILES, 'model-info.json'):
            shutil.copyfile(extracted/name, target/name)
        marker.unlink()
    return str(target)


@dataclass(frozen=True)
class Prediction:
    lemma: str | None = None
    pos: str | None = None
    msd: str | None = None
    xpos: str | None = None
    resolved: bool = False
    lemma_source: str = 'unresolved'
    neural_lemma: str | None = None
    canonical_lemma: str | None = None
    canonical_source: str | None = None
    script_id: int | None = None
    script_log_probability: float | None = None
    context_id: str | None = None
    context_scope: str = 'complete sentence'
    top_k: tuple[dict, ...] = ()

    def as_dict(self):
        data = asdict(self)
        data['top_k'] = list(self.top_k)
        return data


class Joint:
    def __init__(self, path=None, *, threads=4, progress=None, cache_dir=None):
        self.path = Path(path or default_path())
        self.name, self.progress = NAME, progress
        self.cache = {}
        if not _verified_receipt(self.path):
            raise JointError(f'Δεν βρέθηκε έγκυρο GreBERTa joint στο {self.path}. Τρέξτε --prepare-context ή την εγκατάσταση.')
        try:
            if version('pyaegean') != RUNTIME:
                raise JointError('Απαιτείται pyaegean=='+RUNTIME+'. Επαναλάβετε την εγκατάσταση.')
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
            from aegean.greek.neural_contract import ModelBundleManifest
            from aegean.greek import neural_preprocessing as prep
            from aegean.greek.lemmatizer import apply_tree
            from aegean.greek.udfeats import feats_from_xpos
        except ImportError as error:
            raise JointError('Το GreBERTa χρειάζεται pyaegean, onnxruntime και tokenizers. Επαναλάβετε την εγκατάσταση.') from error
        self.np, self.prep = np, prep
        self.apply_tree, self.feats = apply_tree, feats_from_xpos
        try:
            self.manifest = ModelBundleManifest.load(self.path, asset_sha256=SHA256, asset_sha256_enforced=True)
        except Exception as error:
            raise JointError(f'Δεν βρέθηκε έγκυρο GreBERTa joint στο {self.path}. Τρέξτε --prepare-context ή την εγκατάσταση.') from error
        prep.validate_manifest_contract(self.manifest)
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(threads, os.cpu_count() or 1)
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(self.path/'model.onnx'), options, providers=['CPUExecutionProvider'])
        self.tokenizer = Tokenizer.from_file(str(self.path/'tokenizer.json'))
        labels = json.loads((self.path/'labels.json').read_text(encoding='utf-8'))['maps']
        self.labels = {head: {i: label for label, i in mapping.items()} for head, mapping in labels.items()}
        self.trees = [json.loads(s) for s in json.loads((self.path/'lemma-scripts.json').read_text(encoding='utf-8'))]
        self.lookup = json.loads((self.path/'lemma-lookup.json').read_text(encoding='utf-8'))
        tok_version = version('tokenizers')
        self.receipt = {'provider': 'greberta', 'model': NAME, 'archive_sha256': SHA256,
                        'manifest_sha256': MANIFEST_SHA256, 'runtime': 'pyaegean=='+RUNTIME,
                        'onnxruntime': ort.__version__, 'tokenizers': tok_version,
                        'lemma_composition': 'neural-first (keep-aware; top-k recorded)',
                        'execution': f'CPU, unpadded batches of up to {BATCH} sentences of equal subword and word length, four threads maximum',
                        'normalization': 'NFC', 'tokenization': 'pretokenized; stable source boundaries'}
        self.pad_id = self.tokenizer.token_to_id('<pad>')
        self.pad_id = 1 if self.pad_id is None else self.pad_id
        self.disk = Cache('greberta', {'model': SHA256, 'manifest': MANIFEST_SHA256, 'runtime': RUNTIME,
                                       'onnxruntime': ort.__version__, 'tokenizers': tok_version,
                                       'adapter': ADAPTER_VERSION}, cache_dir)
        self.receipt['inference_cache'] = 'persistent, exact model/runtime/adapter identity' if self.disk.enabled else 'off'

    def close(self):
        self.disk.close()

    def _compose(self, form, pos, script, mode):
        return self.prep.compose_lemma_detail(form, pos, script, lookup_form_upos=self.lookup['form_upos'],
            lookup_form=self.lookup['form'], lookup_lower=self.lookup['form_lower'], trees=self.trees,
            apply_edit_script=self.apply_tree, mode=mode)

    def _apply_script(self, probe, script_id):
        if not (0 <= script_id < len(self.trees)):
            return None
        tree = self.trees[script_id]
        if tree == ['keep']:
            return clean(probe)
        applied = self.apply_tree(tree, probe)
        if not applied or applied == '_':
            return None
        return clean(applied)

    def _align(self, words):
        words = self.prep.normalize_tokens(words)
        return words, self.prep.align_pretokenized(self.tokenizer, words, self.manifest.max_subwords)

    def _predict(self, words):
        words, alignment = self._align(words)
        kept = list(alignment.kept_indices)
        if kept == list(range(len(words))):
            if not words:
                return []
            return self._run([(words, alignment)])[0]
        # Preserve every word on overlong inputs using overlapping complete-word
        # windows with farthest-boundary token ownership (preserving local syntax
        # on both sides instead of zero-overlap halving).
        if len(words) <= 1:
            return [Prediction(context_scope='unencodable token')]
        n = len(words)
        windows = []
        start = 0
        while start < n:
            _, win_align = self._align(words[start:])
            win_kept = list(win_align.kept_indices)
            if not win_kept or win_kept[0] != 0:
                windows.append((start, start + 1, None))
                start += 1
                continue
            k = 0
            while k < len(win_kept) and win_kept[k] == k:
                k += 1
            end = start + k
            slice_words, slice_align = self._align(words[start:end])
            if list(slice_align.kept_indices) != list(range(end - start)):
                # Fallback if re-tokenising the trimmed slice differed slightly.
                end = start + max(1, len(slice_align.kept_indices))
                slice_words, slice_align = self._align(words[start:end])
            windows.append((start, end, (slice_words, slice_align)))
            if end >= n:
                break
            start += max(1, (end - start) // 2)
        runnable = [item for _, _, item in windows if item is not None]
        decoded = iter(self._run_unpadded(runnable))
        resolved_windows = []
        for start, end, item in windows:
            if item is None:
                resolved_windows.append((start, end, [Prediction(context_scope='unencodable token')]))
            else:
                resolved_windows.append((start, end, next(decoded)))
        out = []
        for i in range(n):
            best_key, best_pred = None, None
            for start, end, preds in resolved_windows:
                if not (start <= i < end):
                    continue
                left_dist = math.inf if start == 0 else (i - start)
                right_dist = math.inf if end == n else ((end - 1) - i)
                key = (min(left_dist, right_dist), end - start, -start)
                if best_key is None or key > best_key:
                    best_key, best_pred = key, preds[i - start]
            if best_pred is None:
                out.append(Prediction(context_scope='unencodable token'))
                continue
            data = best_pred.as_dict()
            data['top_k'] = tuple(data.get('top_k') or ())
            if best_pred.context_scope != 'unencodable token':
                data['context_scope'] = 'overlapping subword-limit window'
            out.append(Prediction(**data))
        return out

    def _run_unpadded(self, items):
        """_run for aligned sentences of any lengths, in input order: only items of equal subword
        and word counts share a session.run, so nothing is padded (padding shifts log p)."""
        by_shape = {}
        for index, (_, a) in enumerate(items):
            by_shape.setdefault((len(a.input_ids), len(a.first_subword_positions)), []).append(index)
        out = [None] * len(items)
        for indices in by_shape.values():
            for start in range(0, len(indices), BATCH):
                group = indices[start:start + BATCH]
                for index, predictions in zip(group, self._run([items[i] for i in group])):
                    out[index] = predictions
        return out

    def _run(self, items):
        """One session.run for several aligned sentences, padded to the longest; masks hide the padding."""
        np = self.np
        width = max(len(a.input_ids) for _, a in items)
        count = max(len(a.first_subword_positions) for _, a in items)
        ids = np.full((len(items), width), self.pad_id, dtype=np.int64)
        mask = np.zeros((len(items), width), dtype=np.int64)
        positions = np.zeros((len(items), count), dtype=np.int64)
        for row, (_, a) in enumerate(items):
            ids[row, :len(a.input_ids)] = a.input_ids
            mask[row, :len(a.attention_mask)] = a.attention_mask
            positions[row, :len(a.first_subword_positions)] = a.first_subword_positions
        heads = ['upos', *('x'+str(i) for i in range(9)), 'lemma']
        out = dict(zip(heads, self.session.run(heads, {'input_ids': ids, 'attention_mask': mask, 'word_pos': positions})))
        return [self._decode_row(words, a, out, row) for row, (words, a) in enumerate(items)]

    def _decode_row(self, words, alignment, out, row):
        np = self.np
        context_id = hashlib.sha256(json.dumps(words, ensure_ascii=False).encode()).hexdigest()
        result = []
        for wi, sp in enumerate(alignment.first_subword_positions):
            pos = self.labels['upos'][int(out['upos'][row, sp].argmax())]
            xpos = ''.join(self.labels['x'+str(i)][int(out['x'+str(i)][row, sp].argmax())] for i in range(9))
            scores = out['lemma'][row, wi].astype(np.float64)
            script = int(scores.argmax())
            maximum = float(scores[script])
            log_norm = maximum + math.log(float(np.exp(scores - maximum).sum()))
            logp = maximum - log_norm
            word = unicodedata.normalize('NFC', words[wi])
            probe = self.prep._strip_editorial_apparatus(word)
            # pyaegean's compose_lemma_detail drops applied == probe, discarding valid ["keep"]
            # (script 0) neural predictions. Recognise ["keep"] explicitly as a neural edit.
            keep_lemma = clean(probe) if 0 <= script < len(self.trees) and self.trees[script] == ['keep'] else None
            if keep_lemma:
                lemma, source = keep_lemma, 'edit_script'
                raw = keep_lemma
            else:
                lemma, resolved, source = self._compose(words[wi], pos, script, 'neural-first')
                raw, raw_resolved, _ = self._compose(words[wi], pos, script, 'neural-only')
                lemma = clean(lemma) if resolved else None
                raw = clean(raw) if raw_resolved else None
            canonical, canonical_resolved, canonical_source = self._compose(words[wi], pos, script, 'canonical')
            canonical = clean(canonical) if canonical_resolved else None
            k_raw = min(TOP_K_SCRIPTS, len(scores))
            top_idx = np.argpartition(scores, -k_raw)[-k_raw:]
            top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]
            agg = {}
            for sid in top_idx:
                sid = int(sid)
                cand = self._apply_script(probe, sid)
                lp = float(scores[sid] - log_norm)
                if not cand or not math.isfinite(lp):
                    continue
                key = lemma_key(cand)
                if key not in agg:
                    agg[key] = [cand, sid, lp]
                else:
                    agg[key][2] = _logsumexp(agg[key][2], lp)
            ranked = sorted(agg.values(), key=lambda item: -item[2])[:TOP_K]
            top_k = tuple({'lemma': c, 'script_id': sid, 'log_probability': lp} for c, sid, lp in ranked)
            result.append(Prediction(lemma, pos, self.feats(xpos).replace('_', '') or None, xpos,
                bool(lemma), source, raw, canonical, canonical_source,
                script, logp if math.isfinite(logp) else None, context_id, top_k=top_k))
        return result

    def _fill(self, keys):
        """Compute or load every sentence in `keys` not yet in memory."""
        missing = [k for k in dict.fromkeys(keys) if k not in self.cache]
        stored = self.disk.get_many(text_key(k) for k in missing) if missing else {}
        fresh, batchable = {}, []
        for key in missing:
            saved = stored.get(text_key(key))
            if saved is not None:
                self.cache[key] = tuple(
                    Prediction(**{**p, 'top_k': tuple(p.get('top_k') or ())}) for p in saved)
                continue
            words, alignment = self._align(list(key))
            if not words or list(alignment.kept_indices) != list(range(len(words))):
                fresh[key] = tuple(self._predict(list(key)))
            else:
                batchable.append((key, words, alignment))
        by_length = {}
        for item in batchable:
            # Equal subword AND word counts: neither input is padded (word_pos padding shifts log p).
            by_length.setdefault((len(item[2].input_ids), len(item[2].first_subword_positions)), []).append(item)
        groups = [same[start:start+BATCH] for same in by_length.values() for start in range(0, len(same), BATCH)]
        for group in groups:
            for (key, _, _), predictions in zip(group, self._run([(w, a) for _, w, a in group])):
                fresh[key] = tuple(predictions)
        self.cache.update(fresh)
        self.disk.put_many({text_key(k): [p.as_dict() for p in v] for k, v in fresh.items()})

    def pipe(self, sentences):
        pending, i = [], 0
        def flush():
            nonlocal i
            self._fill(pending)
            for key in pending:
                i += 1
                if self.progress and i % 25 == 0: self.progress(i)
                yield list(self.cache[key])
            pending.clear()
        for words in sentences:
            pending.append(tuple(unicodedata.normalize('NFC', word) for word in words))
            if len(pending) >= CHUNK:
                yield from flush()
        yield from flush()
