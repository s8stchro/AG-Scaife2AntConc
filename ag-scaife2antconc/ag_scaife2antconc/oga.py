"""Read OGA's published token and lemma annotations without local inference."""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from ag_diogenes_spacy_txt2xml.normalize import is_greek_letter
from ag_diogenes_spacy_txt2xml.source import Line, Milestone
from ag_diogenes_spacy_txt2xml.tokenize import Token, Word

from .reader import Work


RELEASE = 'https://zenodo.org/records/14206061'
EDITION = 'https://zenodo.org/records/15199546'
DATA = 'https://raw.githubusercontent.com/mgbilby/oga_src/v0.2.0/data/'
MAX_BYTES = 32 * 1024 * 1024
FILENAME = r'[A-Za-z0-9][A-Za-z0-9_.-]*\.conllu'
TOKEN_ID = re.compile(r'[te]_\d+')
POS = {'a': 'ADJ', 'd': 'ADV', 'g': 'PART', 'i': 'INTJ', 'l': 'DET', 'm': 'NUM',
       'n': 'NOUN', 'p': 'PRON', 'r': 'ADP', 'v': 'VERB'}


def is_oga(path):
    return Path(path).suffix.lower() == '.conllu'


def download_url(link):
    url = urlsplit(link.strip())
    match = None
    if url.scheme == 'https' and url.netloc == 'github.com':
        match = re.fullmatch(rf'/mgbilby/oga_src/(?:blob|raw)/(?:v0\.2\.0|main)/data/({FILENAME})', url.path)
    elif url.scheme == 'https' and url.netloc == 'raw.githubusercontent.com':
        match = re.fullmatch(rf'/mgbilby/oga_src/(?:v0\.2\.0|main)/data/({FILENAME})', url.path)
    if not match:
        raise ValueError('Paste the link to a .conllu file from the oga_src repository.')
    filename = match[1]
    return filename, DATA + filename


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(link):
    filename, url = download_url(link)
    try:
        with build_opener(NoRedirect()).open(url, timeout=20) as response:
            if int(response.headers.get('Content-Length') or 0) > MAX_BYTES:
                raise ValueError('The OGA file exceeds 32 MB.')
            data = response.read(MAX_BYTES + 1)
    except (URLError, OSError) as error:
        raise ValueError('Could not download from oga_src 0.2.0. '
                         'Check the link or add the .conllu file from your computer.') from error
    return filename, data


@dataclass
class OgaWork(Work):
    tokens: list = field(default_factory=list)


def read(path):
    filename = Path(path).name
    work = OgaWork(str(path))
    edition = re.match(r'(tlg\d+\.tlg\d+(?:\.[^_]+)?)_oga', filename)
    if edition:
        work.urn = 'urn:cts:greekLit:' + edition[1]
    seen_ids = set()
    citations = Counter()
    last_citation = None
    sentence = 1
    with open(path, encoding='utf-8-sig') as stream:
        for number, line in enumerate(stream, 1):
            row = line.rstrip('\r\n')
            if not row.strip():
                sentence += 1
                continue
            if row.startswith('#'):
                if not work.title and row.startswith('# source = '):
                    work.title = row.removeprefix('# source = ').rpartition('_')[0] or filename
                continue
            fields = row.split('\t')
            if len(fields) != 10:
                raise ValueError(f'Invalid CoNLL-U row in "{filename}" (line {number}).')
            _, form, lemma, tag, *_columns, misc = fields
            token_id, *notes = misc.split('|')
            if not TOKEN_ID.fullmatch(token_id):
                raise ValueError(f'Missing OGA token ID in "{filename}" (line {number}).')
            if token_id in seen_ids:
                raise ValueError(f'Duplicate OGA token ID in "{filename}" (line {number}).')
            seen_ids.add(token_id)
            if token_id.startswith('e_') or tag == 'u' or not any(is_greek_letter(c) for c in form):
                continue
            ref = next((item[4:] for item in notes if item.startswith('ref=')), '')
            citation = (ref.rpartition('_')[2] or ref) if ref else f'§ {sentence}'
            if citation != last_citation:
                citations[citation] += 1
                label = citation if citations[citation] == 1 else f'{citation} [{citations[citation]}]'
                milestone = Milestone('line', label)
                work.lines.append(Line(None, label, '', [milestone]))
                work.tokens.append(Token('ms', [milestone]))
                last_citation = citation
            if any(c.isspace() for c in form):
                raise ValueError(f'Invalid word form in "{filename}" (line {number}).')
            # A missing or unrepresentable lemma stays open for manual review.
            imported = lemma if lemma not in ('', '_') and '+' not in lemma and not any(c.isspace() for c in lemma) else None
            evidence = {'token': token_id, 'reference': ref, 'form': form, 'lemma': lemma,
                        'tag': tag, 'row': row}
            reading = {'kind': 'oga', 'expansion': [form], 'components': [], 'oga': evidence}
            work.tokens.append(Word([('t', form)], lemma=imported, pos=POS.get(tag, 'X'), resp='oga',
                                    evidence=[reading]))
    if not work.lines:
        raise ValueError(f'No Greek OGA words were found in "{filename}".')
    return work


class Imported:
    provider = 'oga'
    heads = frozenset()

    def __call__(self, tokens, words):
        return Counter('oga' if word.lemma else 'no lemma' for word in words)

    def description(self):
        return 'OGA lemmata and token boundaries as supplied in oga_src CoNLL-U.'

    def sources(self):
        return [('oga', 'Opera Graeca Adnotata, annotations in oga_src CoNLL-U', f'{RELEASE} · {EDITION}')]
