"""Author and title of a work from its TLG number (catalog.tsv, built by tools/build_catalog.py
from the Perseus and First1KGreek catalogs), for file names and the report."""
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import cache

CATALOG = os.path.join(os.path.dirname(__file__), 'catalog.tsv')
MAX_NAME = 60


@dataclass(frozen=True)
class Entry:
    ident: str          # tlg0017.tlg001
    author: str         # Isaeus
    title: str          # On The Estate of Cleonymus
    greek: str = ''     # Περὶ τοῦ Κλεωνύμου κλήρου

    @property
    def display(self):
        """Isaeus, On The Estate of Cleonymus (Περὶ τοῦ Κλεωνύμου κλήρου)"""
        head = ', '.join(x for x in (self.author, self.title) if x)
        return f'{head} ({self.greek})' if self.greek and self.greek != self.title else head

    @property
    def file_name(self):
        """Isaeus-On-The-Estate-of-Cleonymus: letters and digits only, no accents."""
        return slug(f'{self.author} {self.title}')


@cache
def entries():
    table = {}
    with open(CATALOG, encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('#') or not line.strip():
                continue
            ident, author, title, greek = (line.rstrip('\n').split('\t') + ['', '', ''])[:4]
            table[ident] = Entry(ident, author, title, greek)
    return table


def lookup(ident):
    return entries().get(ident) if ident else None


def slug(text, limit=MAX_NAME):
    plain = ''.join(c for c in unicodedata.normalize('NFD', text) if not unicodedata.combining(c))
    words = re.findall(r'\w+', plain.replace('_', ' '))
    out = ''
    for w in words:
        if out and len(out) + 1 + len(w) > limit:
            break
        out = f'{out}-{w}' if out else w[:limit]
    return out
