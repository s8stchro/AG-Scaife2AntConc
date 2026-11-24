"""Morpho of Logeion (https://logeion.uchicago.edu/morpho/): the lemmas of the forms of the Perseus
Greek texts, as edited by the Logeion team. Used only for the words that Diogenes cannot decide.

There is no published API: the site's own address is asked, one form at a time, and every answer
is kept in a local cache, so that a form is asked once. Morpho knows a form only as printed in
Perseus (grave kept: κελεὲ, not κελεέ; elision mark U+02BC); a form not found is asked again
with the grave its last syllable has in running text, then in lower case.
"""
import json
from contextlib import contextmanager
import os
import re
import ssl
import time
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import APP_NAME, __version__
from .diogenes import VOWELS
from .normalize import APOS_LIKE

URL = 'https://anastrophe.uchicago.edu/morpho-api/detail/'
SITE = 'https://logeion.uchicago.edu/morpho/'
NAME = 'Morpho, Logeion (University of Chicago)'
ELISION = chr(0x2BC)
ACUTE, GRAVE = chr(0x301), chr(0x300)
CACHE_VERSION = 1


@contextmanager
def cache_lock(path):
    """Keep worker and reviewer cache merges from replacing each other's replies."""
    with open(path+'.lock', 'a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell(): stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            deadline = time.monotonic()+5
            while True:
                try: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1); break
                except OSError:
                    if time.monotonic() >= deadline: raise
                    time.sleep(.05)
            try: yield
            finally:
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Reply(tuple):
    """Backward-compatible lemma tuple retaining the complete provider response."""
    def __new__(cls, lemmas, payload):
        obj = super().__new__(cls, lemmas)
        obj.payload = payload
        return obj


def graved(form):
    """κελεέ -> κελεὲ: an acute on the last syllable as a grave; None if there is none to change."""
    if form.endswith(ELISION):
        return None
    d = unicodedata.normalize('NFD', form)
    i = d.rfind(ACUTE)
    if i < 0 or any(c.lower() in VOWELS for c in d[i + 1:]):
        return None
    return unicodedata.normalize('NFC', d[:i] + GRAVE + d[i + 1:])


def queries(form):
    """The spellings to ask for a form, in order."""
    f = unicodedata.normalize('NFC', ''.join(ELISION if c in APOS_LIKE else c for c in form))
    out = [f] + [g for g in [graved(f)] if g]
    out += [x[0].lower() + x[1:] for x in out if x[:1].isupper()]
    return list(dict.fromkeys(out))


def lemmas_of(data):
    """The lemmas in an answer: each short definition starts with its headword (ἅλς2, salt …)."""
    out = []
    for s in data.get('shortdef') or ():
        if s.startswith('No short definition'):
            continue
        lemma = unicodedata.normalize('NFC', re.sub(r'\d+$', '', s.split(',')[0].strip()))
        if lemma and lemma not in out:
            out.append(lemma)
    return tuple(out)


_context = None      # None: the computer's own certificates; after a verification failure, certifi's


def _open(req, timeout):
    """urlopen, with one retry on a certificate that cannot be verified: Windows installs some root
    certificates only when a program first needs them, and Python cannot ask for that, so a valid
    certificate can fail (anastrophe.uchicago.edu, chain to emSign Root CA - G1, November 2026).
    The retry uses the certificates of the certifi package, and so do all later requests."""
    global _context
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_context)
    except urllib.error.URLError as e:
        if _context is not None or not isinstance(e.reason, ssl.SSLCertVerificationError):
            raise
        try:
            import certifi
        except ImportError:
            raise e from None
        _context = ssl.create_default_context(cafile=certifi.where())
        return urllib.request.urlopen(req, timeout=timeout, context=_context)


def fetch(query, timeout=30):
    """Morpho's lemmas for one spelling; None if Morpho answered with an error for this form (it answers
    some forms, e.g. ἐχαρίσω, with HTTP 500 and {"error": …}). OSError if there is no answer at all."""
    req = urllib.request.Request(URL + urllib.parse.quote(query) + '?dicos=all',
                                 headers={'User-Agent': f'{APP_NAME}/{__version__} (lemmatizer for teaching)'})
    try:
        with _open(req, timeout) as fh:
            data = json.load(fh)
    except urllib.error.HTTPError as e:
        try:
            data = json.load(e)                 # Morpho's own error message: the site is up
        except ValueError:
            raise e from None                   # a proxy's error page: Morpho is down
        if 'error' not in data:
            raise
    return None if 'error' in data else Reply(lemmas_of(data), data)


class Morpho:
    """Morpho's lemmas for forms: from the cache, or asked with `ask`.
    `progress(done, total)` is called while asking; `failed` says why Morpho stopped answering."""

    def __init__(self, cache_dir=None, online=True, delay=0.5, progress=None, fetch=fetch):
        cache_dir = cache_dir or os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/.cache'),
                                              'AG-Diogenes-SpaCy-txt2xml')
        self.path = os.path.join(cache_dir, f'morpho-v{CACHE_VERSION}.json')
        try:
            with open(self.path, encoding='utf-8') as fh:
                saved = json.load(fh)
                self.cache = saved['forms']
                self.records = saved.get('records', {})
        except (OSError, ValueError, KeyError):
            self.cache = {}
            self.records = {}
        self.online, self.delay, self.progress, self.fetch = online, delay, progress, fetch
        self.asked, self.failed = 0, None

    def lemmas(self, form):
        """The lemmas of the first spelling of the form that Morpho knows; () if none."""
        for q in queries(form):
            if self.cache.get(q):
                return tuple(self.cache[q])
        return ()

    def ask(self, forms):
        """Ask Morpho for the forms it has not answered yet; keep the answers."""
        todo = [f for f in dict.fromkeys(forms)
                if not self.lemmas(f) and any(q not in self.cache for q in queries(f))]
        if not todo or not self.online or self.failed:
            return
        try:
            for i, form in enumerate(todo, 1):
                for q in queries(form):
                    if q not in self.cache:
                        if self.asked:
                            time.sleep(self.delay)
                        self.asked += 1
                        found = self.fetch(q)
                        if found is None:
                            continue
                        self.cache[q] = list(found)
                        if isinstance(found, Reply):
                            self.records[q] = {'query': q, 'response': found.payload,
                                               'verification': 'not established'}
                    if self.cache[q]:
                        break
                if self.progress and (i % 50 == 0 or i == len(todo)):
                    self.progress(i, len(todo))
        except (OSError, ValueError) as e:        # no connection, timeout, not JSON
            self.failed = str(e) or e.__class__.__name__
        finally:
            self.save()

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with cache_lock(self.path):
            try:
                with open(self.path, encoding='utf-8') as stream: previous = json.load(stream)
            except (OSError, ValueError): previous = {}
            forms = dict(previous.get('forms', {})); forms.update(self.cache)
            records = dict(previous.get('records', {})); records.update(self.records)
            fd, pending = tempfile.mkstemp(prefix='.morpho-', suffix='.json', dir=os.path.dirname(self.path))
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write(json.dumps({'source': URL, 'forms': forms, 'records': records}, ensure_ascii=False))
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(pending, self.path)
                self.cache, self.records = forms, records
            finally:
                if os.path.exists(pending): os.remove(pending)

    def evidence(self, form):
        """References to preserved raw responses; a definition is not proof of manual verification."""
        return [{'query': q, 'verification': 'not established', 'raw_response_cache': self.path,
                 'response_fields': sorted(self.records[q]['response'])}
                for q in queries(form) if q in self.records]
