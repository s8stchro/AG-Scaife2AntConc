# Local browser workbench

Version 0.7.0 uses GreBERTa joint for contextual disambiguation and exposes
Diogenes dictionary-link quality and separate GreTa comparison in the evidence
panel. **Morpho: [form]** performs a reviewer lookup without saving a choice.
Analysis offers changed passages or the whole text; both preserve manual review.
Existing snapshots keep their original providers until explicit reanalysis.

The regular launcher now opens the local browser interface at
`http://127.0.0.1:8766`. Start AG-Scaife2AntConc from Windows search, then use the
browser page. A bookmark works while the local service is running. The launcher
reuses the existing instance instead of starting a second engine. It does not
add a Windows startup service or require an online account.

The original native interface remains available through
`ag-scaife2antconc-desktop`. The text-processing command line is unchanged.

Version 0.4.1 bundles Gentium Plus for polytonic Greek in the review field and
Greek text. Browser spelling correction and automatic capitalization are disabled
in the lemma input. See [FONTS.md](FONTS.md) for provenance and the font licence.

Version 0.4.2 uses separate suffixed headwords for recognised deictic forms:
`νυνὶ → νυνί`, `τουτονί → οὑτοσί`. The -ι suffix does not add a word to the count.
The review pane and `analysis-review.jsonl` identify the portable rule's
contribution separately from provider outputs. Search for `deictic` or `-ι`
in the **Όλες** review view to find annotated forms. Raw candidates and scores
are preserved, and explicit manual choices still take precedence.

Existing saved analyses retain their old decisions on re-export. The new
convention applies to new analyses; it does not modify reviewed result folders.
The documented inventory is finite, not a generic operation on words ending in ι.

Version 0.4.3 requires evidence for inflected crasis restorations. If a split
cannot be supported, review shows the original word and flags the unresolved
crasis. The engine still considers available analyses of the whole word.

## Working with texts and reviews

Under **Νέα ανάλυση**, paste a work's `.conllu` link from the
[oga_src 0.2.0 catalogue](https://github.com/mgbilby/oga_src/tree/v0.2.0/data)
or add a downloaded OGA `.conllu` file. Choose a new output folder, then use
**Εισαγωγή και έλεγχος** to review lemmata or **Εισαγωγή και εξαγωγή** to go
straight to the AntConc files. The app keeps the OGA lemmata and token boundaries
and records the original annotations with each occurrence. It imports Greek
words present in the text and omits punctuation and inferred words. Review opens
on all occurrences. Missing lemmata also appear under **Αμφίβολες**. Saved
corrections can be exported from the same project without running the local models.

In review, **Ορισμός κράσης** lets you choose a boundary in the written word
and enter the two restored forms, lemmata and parts of speech. Use the existing
scope controls to apply it to this occurrence or matching occurrences with the
same written form and automatic lemma. **Ακύρωση ορισμού κράσης** returns to
the fields present before an unsaved split, including any lemma edits. This also
works after recovering a browser draft. For a saved split,
**Αναίρεση ορισμού κράσης** restores the original analysis for the selected
scope when saved. **Ενημέρωση αρχείων**
includes the saved split in the AntConc and TEI outputs available for the project.

- Upload or drag XML into the page, or use the optional local file chooser to
  retain its original location. Uploaded XML copies are persistent, not temporary
  browser uploads; saved analysis sessions need those source files for re-export.
- Choose an output folder. Quick export uses the existing recommended options.
  Philological review exposes the existing cleansing preview and optional choices.
- Open existing result folders or select recent projects to resume. Existing
  0.3.x saved analyses and correction sheets are supported. No linguistic rules
  or ranking weights have changed for this interface release.
- Save/Confirm advances to the next occurrence in the current filter/search;
  it does not wrap. Browser drafts and the last review location are recoverable
  in the same browser profile. Saved choices are written to `lemma-review.tsv`.
- Updating files launches a separate export job from saved machine analyses,
  without another model run. Missing/changed source XML or incompatible stored
  analyses are reported rather than silently substituted.
- Choose **Quit** to close the workbench window. With an unsaved lemma or
  passage edit, choose **Save & quit**, **Keep draft & quit**, or **Stay**.
  A draft can be recovered in the same browser profile. If the browser keeps
  the tab open, close it using its ×.
- Other open workbench windows stay connected, including minimized windows.
  Quitting the last window lets an active analysis or export finish in the
  background, then stops the local service. Reopen the launcher to check the
  job or resume review. Use **Cancel** in the job banner to request cancellation.
- Closing the window with its × also lets the job finish. The normal launcher
  stops the service after the last window closes and the idle timeout expires.
- Downloads include the AntConc texts as ZIP, or the complete result package.
  The optional local bridge can also open the result folder in Windows.

## Data and preservation

The existing result folder is authoritative. The review model and transactional
pipeline are the same ones used by the native interface. A stale revision from
another tab is rejected before it can overwrite a newer saved choice. If the
review sheet or analysis file changes externally during a job, that job refuses
to publish over the changed files.

Browser state, uploaded XML, job requests, logs and authentication state are kept
in `%LOCALAPPDATA%\AG-Scaife2AntConc-Workspace`, separate from the installed
program directory. Upgrading or uninstalling the program does not remove this
workspace directory. Its uploaded XML files can be sources for saved sessions;
do not delete them while those sessions are still needed. Normal output folders
remain wherever the user selected them.

An interrupted process is marked as interrupted, not successfully completed.
Completed choices remain on disk; a terminated analysis is not automatically
resumed from its last neural batch. Worker logs in the workspace aid diagnosis.

## Structure for a later hosted deployment

- `web_service.py`: workbench operations and project/review coordination.
- `web_storage.py`: local persistence adapter.
- `web_worker.py`: independent analysis/export process and cancellation checkpoints.
- `web_api.py`: ASGI endpoints and browser session protections.
- `web_assets`: offline HTML/CSS/JavaScript, without a CDN or frontend build step.
- `web.py` and `local_dialog.py`: local-only launch, folder selection and integration.

The launcher binds **127.0.0.1 only**. The API checks the configured host, session,
origin and anti-forgery token. The experimental local identity and filesystem
permissions represent one local user; this is not a multi-user hosted service.
Hosting later requires per-user authentication, owned-project storage, managed
job workers and deployment limits. Disable/replace the local bridge and arbitrary
local-path selection in such an adapter. Retain the existing analysis engine and
reusable rule interfaces rather than changing their linguistic behaviour.

For local diagnostics, `ag-scaife2antconc-web --no-browser` runs the same service,
and `ag-scaife2antconc-web --stop` requests a clean stop when no job is active.
`--state` and `--port` allow separate test workspaces. The default port remains
fixed so normal bookmarks are stable; a different program occupying that port
is reported, never mistaken for this application.
