"""Build ag_scaife2antconc/catalog.tsv from the CTS catalog files (__cts__.xml) of
PerseusDL/canonical-greekLit and OpenGreekAndLatin/First1KGreek, the two collections of
Greek texts in the Scaife Viewer.

    python tools/build_catalog.py

Columns: work (tlgNNNN.tlgNNN), author, title (English or Latin), Greek title (if any).
Where both collections describe a work, canonical-greekLit wins. Metadata: CC BY-SA 4.0.
"""
import concurrent.futures
import json
import os
import urllib.request
import xml.etree.ElementTree as ET

REPOS = ['PerseusDL/canonical-greekLit', 'OpenGreekAndLatin/First1KGreek']
TI = '{http://chs.harvard.edu/xmlns/cts}'
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'
OUT = os.path.join(os.path.dirname(__file__), '..', 'ag_scaife2antconc', 'catalog.tsv')


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'ag-scaife2antconc-catalog'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def pick(el, tag, langs=('eng', 'lat', 'grc')):
    """Text of the child <tag> in the first available language."""
    found = {c.get(XML_LANG): ' '.join(''.join(c.itertext()).split()) for c in el.iter(TI + tag)}
    return next((found[l] for l in langs if found.get(l)), next(iter(found.values()), ''))


def catalog_files(repo):
    branch = json.loads(get(f'https://api.github.com/repos/{repo}'))['default_branch']
    tree = json.loads(get(f'https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1'))
    paths = [t['path'] for t in tree['tree'] if t['path'].startswith('data/') and t['path'].endswith('__cts__.xml')]
    return [f'https://raw.githubusercontent.com/{repo}/{branch}/{p}' for p in paths]


def parse(data):
    root = ET.fromstring(data)
    urn = root.get('urn', '')
    if root.tag == TI + 'textgroup':
        return 'group', urn.rsplit(':', 1)[-1], pick(root, 'groupname', ('lat', 'eng', 'grc')), ''
    if root.tag == TI + 'work':
        greek = next((' '.join(''.join(l.itertext()).split()) for e in root.iter(TI + 'edition')
                      for l in e.iter(TI + 'label') if l.get(XML_LANG) == 'grc'), '')
        return 'work', urn.rsplit(':', 1)[-1], pick(root, 'title'), greek
    return None


def main():
    groups, works = {}, {}
    for repo in REPOS:
        urls = catalog_files(repo)
        print(f'{repo}: {len(urls)} catalog files')
        with concurrent.futures.ThreadPoolExecutor(16) as pool:
            for url, data in zip(urls, pool.map(lambda u: get(u), urls)):
                try:
                    item = parse(data)
                except ET.ParseError:
                    print('  not XML:', url)
                    continue
                if item and item[1]:
                    kind, ident, name, greek = item
                    target = groups if kind == 'group' else works
                    target.setdefault(ident, (name, greek))        # first repo wins
    rows = []
    for ident, (title, greek) in sorted(works.items()):
        author = groups.get(ident.split('.')[0], ('', ''))[0]
        rows.append('\t'.join([ident, author, title, greek]))
    with open(OUT, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('# work\tauthor\ttitle\tGreek title. From the __cts__.xml files of ' + ' and '.join(REPOS)
                 + ' (CC BY-SA 4.0); built by tools/build_catalog.py\n')
        fh.write('\n'.join(rows) + '\n')
    print(f'{len(rows)} works, {len(groups)} authors -> {os.path.normpath(OUT)}')


if __name__ == '__main__':
    main()
