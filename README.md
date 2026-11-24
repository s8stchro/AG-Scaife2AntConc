# AG-Scaife2AntConc

Lemmatisation and annotation of Ancient Greek texts for corpus work in AntConc.

The application reads Scaife/Perseus TEI (or OGA CoNLL-U) texts, cleans them, and selects a lemma, part
of speech and morphology for every word. Full Morpheus analyses come from a local **Diogenes**
installation; the contextual **GreBERTa joint** model chooses among them, with **GreTa** as a
form-based comparator. Every decision keeps its evidence and the traced rule that made it. A browser
workbench lets you correct the text and review doubtful lemmata, then exports:

- `antconc-tagged/` – one line per citation unit, each word as `form_POS_lemma`
  (AntConc *simple_word_pos_headword_indexer*);
- `antconc-lemmata/` – the same lines with each word replaced by its lemma;
- `xml/` – TEI with `<w lemma pos msd>` and the revision history;
- `lemma-review.tsv`, `analysis-review.jsonl`, `report.txt` – review sheet, full evidence, run report.

## Repository layout

| Folder | Package | Role |
| --- | --- | --- |
| [`ag-scaife2antconc`](ag-scaife2antconc) | `ag_scaife2antconc` | Application: reader, pipeline, web workbench, desktop window, exports, installer |
| [`ag-diogenes-spacy-txt2xml`](ag-diogenes-spacy-txt2xml) | `ag_diogenes_spacy_txt2xml` | Core engine: Diogenes/Morpheus store, normalisation, tokenisation, crasis/elision restoration, GreBERTa and GreTa adapters, contextual selection, TEI |
| [`ag-greek-rules`](ag-greek-rules) | `ag_greek_rules` | Portable rule assessments with stable IDs and versions, and a machine-readable catalogue (`catalog.json`) |

The core engine's name is historical: spaCy/OdyCy were retired in release 0.7.0.

All linguistic decision rules, their versions and the reasons for each change are listed in
[`RULES.md`](RULES.md) and in [`ag-greek-rules/CHANGELOG.md`](ag-greek-rules/CHANGELOG.md).

## Requirements and installation

- Windows, with [Diogenes](https://d.iogen.es/d/) installed for automatic analysis.
- The installer (`ag-scaife2antconc/install.ps1`) uses [uv](https://docs.astral.sh/uv/) to set up
  Python 3.12 and the dependencies in `%LOCALAPPDATA%\AG-Scaife2AntConc`, and downloads the GreBERTa
  and GreTa model archives, verifying their published SHA-256 checksums.
- `Start-Workbench.bat` runs the application straight from this source tree, using that environment.

See [`ag-scaife2antconc/README.md`](ag-scaife2antconc/README.md) for use (in Greek).

## Tests

Each package has a `tests/` folder (pytest). For example, with the application environment:

```powershell
$env:PYTHONPATH = "ag-scaife2antconc;ag-diogenes-spacy-txt2xml;ag-greek-rules"
python -m pytest ag-greek-rules/tests ag-diogenes-spacy-txt2xml/tests ag-scaife2antconc/tests
```

Tests use small fixtures; tests that need the real models or a Diogenes installation are skipped
unless explicitly enabled.

## Licences

- Code in all three packages: MIT (see each package's `LICENSE`).
- Not redistributed here: Diogenes, Morpheus tables and LSJ are read from the user's own Diogenes
  installation; GreBERTa joint and GreTa (CC BY-SA 4.0, trained on AGDT, Gorman and Pedalion data) are
  downloaded at installation. Details: [`ag-scaife2antconc/THIRD_PARTY.md`](ag-scaife2antconc/THIRD_PARTY.md).
- Bundled font: Gentium Plus, SIL Open Font License (`web_assets/GentiumPlus-OFL.txt`).
- Test texts in `ag-scaife2antconc/tests/data` come from the Perseus Digital Library, CC BY-SA 4.0
  (sources: [`SOURCES.md`](ag-scaife2antconc/tests/data/SOURCES.md)).
