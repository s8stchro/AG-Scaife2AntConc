# Editable texts and revisions — 0.6.0

Version 0.7.0 adds **Νέες / διορθωμένες** and **Όλο το κείμενο** choices under
**Αυτόματη ανάλυση κειμένου**. Both use the current GreBERTa pipeline and retain
manual choices and history. Whole-text analysis uses sentence context across
passages within each work; changed-only analysis uses passage context. Installing
the update does not reanalyse existing work.

The browser workspace can open Perseus XML or OGA for correction before automatic annotation. Existing reviewed projects can become editable through **Διόρθωση κειμένου** or **Κείμενο και ιστορικό**. Imported source bytes and existing result files are retained.

## Working with a text

Choose **Άνοιγμα για διόρθωση κειμένου** after selecting input files and a new project folder. In an existing project, choose a word and **Διόρθωση κειμένου**, or use **Κείμενο και ιστορικό** to find a verse/paragraph, including an empty one.

Edit the passage, optionally add a reason, and save it. The original reading is shown alongside the current reading. Text review has its own checkbox, separate from lemma/POS confirmation. Text drafts stay in the browser until saved. History offers restoration of the reading before a chosen text edit; restoration is itself a recorded revision.

Changed or inserted words have no active lemma and POS X until analysed or edited. Unchanged words retain their identity and earlier choices, with a context-change flag where needed. An ambiguous match between repeated identical words does not silently transfer a neighbour's annotation: previous choices remain available for comparison. Insertions, deletions, word joins/splits and punctuation changes are recorded as text edits. Crasis expansion remains an annotation of the written word.

**Αυτόματη ανάλυση κειμένου** analyses passages needing annotation using the existing provider pipeline. Manual choices remain available and authoritative for reconciliation. This release does not adopt new provider weights or linguistic selection rules.

## Exports and project copies

**Ενημέρωση αρχείων** exports the current text and its current annotations without running models. AntConc and XML are generated from one revision. Changed words cannot inherit an invalid old lemma in the export. Existing AntConc apostrophe/cleaning conventions remain export conventions; the working text retains the characters entered by the editor.

XML keeps the imported TEI tree when corrections can be mapped safely to its text nodes. Headers, speakers, notes and apparatus are retained. Current lemma/POS, component analyses and review status are linked in a `standOff` annotation layer. Obsolete inline source annotations are explicitly historical. A correction that cannot safely be applied across existing markup produces an explicitly identified derived TEI with a visible warning. OGA-derived XML is also identified as derived; it cannot reconstruct the original Perseus apparatus.

**Έργο με ιστορικό** downloads a self-contained project copy: working text, source archives, imported annotations and complete history. Extract it and open its folder in the app. A portable copy does not depend on legacy result sheets remaining at their original location. Exported review/evidence files are snapshots; annotation edits in the editable workspace are stored in its canonical project, rather than the original legacy sheet.

## Storage and safeguards

- `working-text.json` is the canonical, schema-versioned project. It contains passage text, stable word IDs, linked annotation states, original provider evidence, text-review status and revision events.
- `.working-text/sources/` contains immutable copies of the imported files, verified by SHA-256. `.working-text/legacy/` preserves the imported review and evidence files when migrating a result folder.
- Original legacy files are not rewritten by migration or working-text exports. If they are changed externally after migration, the app stops a conflicting operation and retains both versions; reconciliation must be explicit.
- Unchanged words retain UUIDs and legacy occurrence identities. New numeric occurrence IDs are never reused or renumbered after insertions/deletions.
- Annotations refer to a text revision. Historical choices remain historical rather than becoming evidence for a changed spelling.
- Saves are atomic, guarded by revision checks and a cross-process write lock. A stale browser tab cannot overwrite a newer edit.
- Exports are immutable under `working-exports/<revision>/`. `working-exports/current.json` identifies the completed export for the current revision. Old exports remain available on disk, while downloads ask for an updated export after edits.

Character positions in the working JSON and XML annotation layer are zero-based Unicode code-point offsets into the exact passage transcription, with an exclusive end. They are not byte positions or browser UTF-16 offsets. Original source references and the exact source bytes are retained separately.

The editor changes passage text, not arbitrary XML tags or critical-apparatus structures. Native corrected CoNLL-U export and merging external spreadsheet edits into a migrated canonical project are not part of this release.
