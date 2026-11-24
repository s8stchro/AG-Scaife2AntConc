# Reusable Ancient Greek rules

Release 0.7.6 (app 0.7.6, core 0.4.6, rules 0.8.1; 24 November 2026), owner request:
`grc.expansion.crasis` 1.2.0 restores ὦ + vocative beyond the crasis table. Before, only listed
forms (ὦνθρωπε, ὦγαθέ, ὦπίτριπτε…) were split; ὦναίσχυντε, ὦλεξίκακε, ὦταῖρε, ὦργεῖοι were one
unknown word and received invented lemmata. Now a ὦ/ὤ-initial form with no analysis of its own is
tried as ὦ + the word with ἀ/ἁ/ἐ/ἑ/ὀ/ὁ (and the existing contraction rules, elision included:
ὦταῖρ’ → ὦ ἑταῖρε); a restoration is kept only if Diogenes analyses it as vocative or nominative.
The restoration-evidence requirement is unchanged: ὦλεκτρυόν, ὦδελφίδιον and ὦχαρνηίδαι stay
unrestored because Diogenes lacks those forms. Aristophanes project (dry run): about 18 forms newly
restored, all correct; ὦμος, ὦτα, ὦσι, ὤμοι are unaffected.

Release 0.7.5 (app 0.7.5, core 0.4.5, rules 0.8.0; 24 November 2026), owner decision ("no lemma,
review"): `grc.selection.generated-lemma-check` 1.0.0. A form that Diogenes does not know took the
contextual model's proposal unchecked, so low-confidence edit scripts produced invented lemmata
(Isaeus 1 Ποσειδίππῳ → Ποσειδεώπ, Ποσειδέπαος). After selection and before headword
standardization, for single-word forms with an empty local inventory:
1. A proposal with log p ≤ −1.0 whose own stem does not begin the form takes the single reliable
   lemma of the same text whose stem (citation ending removed, ≥ 5 letters) begins the form with
   ≤ 4 further letters and the same initial case. Reliable: local analyses, manual choices, and
   generated lemmata with log p > −0.5. Ποσειδίππῳ → Ποσείδιππος (from Ποσείδιππον).
2. Otherwise a proposal that cannot be a headword (two accents, initial vowel without breathing,
   impossible final letter, non-letter) is removed; the word is written NOLEMMA.
Both are urgent review observations (`grc.review.lemma-priority` 1.5.0); the model's proposal stays
in the evidence. The model's next candidates are deliberately not used: in a trial they were often
unrelated real words. Isaeus 1: 2 replaced, 1 removed (εἰσεκάλεσεν). Aristophanes project (dry run):
18 replaced, 128 removed of 961 generated lemmata; about 5 replacements take a wrong real word with
the same stem (γύναικο → γύναιον), hence the review flag. Many removals are ὦ fused with a vocative
(ὦναίσχυντε), which needs a crasis restoration (not yet implemented).

Release 0.7.4 (app 0.7.4, core 0.4.4, rules 0.7.0; 24 November 2026), owner decision (option A):
`grc.grammar.proper-noun` 1.0.0. GreBERTa joint has no PROPN label (its AGDT-style training data
tag names as `n`) and Morpheus gives no class for nouns, so every name was NOUN. After headword
standardization, a machine NOUN whose lemma and written form both begin with a capital becomes
PROPN (in a crasis, the restored component decides: κἀνακρέων → CCONJ+PROPN). A capitalised lemma
on a lower-case form stays NOUN and is an urgent review observation (`grc.review.lemma-priority`
1.4.0): usually a wrongly capitalised lemma (μυρρίνας → Μυρρίνη, χοάνην → Χοάνη), sometimes a
personification (πειθώ). Ethnic adjectives, adverbs and verbs keep their class; manual and reviewed
choices are untouched. The workbench's Standardize action applies the rule to existing projects
without re-running the models. Isaeus 1: 49 NOUN → PROPN, no lemma changes. Aristophanes project
(dry run): 2,787 → PROPN, 53 flagged.

Release 0.7.3 (app 0.7.3, core 0.4.3, rules 0.6.1; 24 November 2026) ships the 20 November update
below together with the AntConc lemma export (`antconc-lemmata/`) and these fixes. No selection rule
changed:
- Split verse lines (`<l part="M|F">` without `@n`) continue the line they share a number with, as one
  cited line. Before, both halves carried the same citation with restarted word numbers, so the results
  folder failed to open ("non-unique occurrence identifiers").
- A working-text save that cannot take the file lock is rolled back; before, the rejected edit stayed in
  memory and was written by the next save.
- GreBERTa adapter 6: overlapping windows of over-long sentences are run only with windows of the same
  subword and word length, never padded together (padding shifted log p by up to 0.0155); results now
  equal single-window runs exactly. The cache key changes, so those sentences are recomputed once.
- Lunate sigma before an elision sign stays medial (`ϲ’` → `σ’`, elided σέ).
- An export saved before `antconc-lemmata` existed gains lemma files derived from its own tagged
  files, so both folders keep the same names and lines.
- A streamed request body over the size limit is answered 413, not 400; reopening the app renews the
  idle timer before the browser connects.
- Version records: rules catalogue reference corrected (no 0.5.3 release exists); provenance labels name
  ag-greek-rules 0.6.1.

GreBERTa decoding, rules & philological update (rules 0.6.0, `grc.selection.contextual-primary` 1.3.0,
`grc.pronoun.interrogative-indefinite-accent` 1.1.0, `grc.lemma.deictic-iota-headword` 1.1.0,
`grc.lemma.diogenes-headword` 1.1.0, `grc.review.lemma-priority` 1.3.0, GreBERTa adapter 5 (6 since 0.7.3),
20 November 2026; owner-approved):
- Explicit `["keep"]` (script 0) handling in `greberta.py`: `pyaegean`'s `compose_lemma_detail`
  discarded identity edits (`applied == probe`), dropping valid neural predictions on nominatives,
  particles and unseen headwords and firing false urgent lookup-fallback warnings. `["keep"]` is
  now recognised as a resolved neural `edit_script` prediction.
- `canonical_lemma` is cleaned and retained only when `canonical_resolved` is true, preventing
  unresolved `identity_fallback` strings from matching as `context_lookup`.
- Top-$k$ contextual edit-script decoding (`TOP_K = 10` distinct clean lemmas aggregated by
  `lemma_key` via log-sum-exp): in `grc.selection.contextual-primary` 1.3.0, when Diogenes offers
  two or more lemmata and the top-1 contextual proposal is outside the inventory, the UPOS-conditioned
  `canonical_lemma` is checked in the local inventory first (`context_outside_inventory_lookup`),
  then ranked `top_k` contextual edit scripts (`context_topk_inventory_disambiguation`) before GreTa.
  When top-1 is already an attested headword (`known_lemma`), deep-tail `top_k` candidates lagging
  top-1 by more than `TOPK_KNOWN_HEADWORD_MAX_GAP = 2.5` nats are rejected so attested headwords
  missing from a form's local table (e.g. *Acharnians* 263–276 `Φαλῆς`) are retained for review.
  Raw top-1 and `top_k` evidence stay intact.
- Overlapping GreBERTa context windows (`adapter 5`) with farthest-boundary token ownership replace
  zero-overlap binary halving on >256-subword sentences; unexpanded elisions pass U+0313 to GreBERTa;
  and `working_analysis.py` expands changed single-line verse passages to their enclosing sentence
  boundaries within the work while committing updates only to pending passages.
- `grc.pronoun.interrogative-indefinite-accent` 1.1.0 adds Attic `ἄττα`, dual/plural `εἰμί`/`φημί`
  enclitics, elided enclitic detection (`γ’`, `τ’`, `μ’`, `ποτ’`, `ἐστ’`), and orthotonic accent
  filtering (`νῦν`, `εἶμι`, `πῶς`/`ποῦ`/`πότε`); integrated into `Contextual` as a non-overriding
  review observation alongside `grc.review.lemma-priority` 1.3.0 (top-1/top-2 `top_k` margin and
  out-of-inventory fallback priority).
- `grc.lemma.deictic-iota-headword` 1.1.0 completes missing Attic paradigms (`αὑταιί`, `τουτουσί`,
  `τονδί`, `τοσουτί`, `τοιουτοσί`, `ἐνθαδί`, `δευρί`, etc.), and `grc.lemma.diogenes-headword` 1.1.0
  attaches verified `lsj:...` identities to separate deictic headwords without altering headword choice.
- Elision, crasis and `diogenes._join()` fixes: stop de-aspiration before rough breathing (Smyth §75:
  `θ→τ`, `φ→π`, `χ→κ`, `χθ→κτ`, `φθ→πτ`), expanded `CONTRACTED` vowels (`θοὔδωρ`, `τὠνδρός`, `κᾆπεν`),
  double elision (`’στ’`, `’φ’`, `’π’`), medial crasis `ω/η/ου` (`ἐγᾦμαι`, `ποὔστιν`, `μἠγώ`), and
  preverb joining (`συσσιτέω`, `ἐνρίπτω`, recessive accent on `κεῖμαι`/`ἧμαι`/`οἶδα`/`φημί`, and
  simplex lookalikes `ἐν-ἔννυμι`, `ὑπό-ὑφαίνω`, `κατά-καθαίρω`, `μετά-μεθύω`).
- Shared-context elision/medial-crasis tie-breaking and empty `script_scores` fix in
  `contextual.py`: readings that tie on `mean(script_log_probability) + prior` (such as
  shared-context elision restorations `ἄγα` vs `ἄγε`) break ties by contextual lemma and
  contextual POS/MSD fit (`context_match`, `grammar_fit`, recorded in evidence), and unencodable
  tokens receive `-100.0` instead of `0.0`.
- `lemma_evidence` for `grc.grammar.corroborated-pos` now queries all candidate/selected lemmata
  (using a new SQLite `lemmas` index and precomputed `pos`/`msd` in `analyses.py`) and filters
  form analyses to the selected lemma.
- Phase 1 engineering fixes: PUA `GAP_MARK` (`\ue003`) separates TEI `<gap>` from literal `!`
  exclamations in Scaife/WorkingText (retaining TLG `'!'` mode in `tokenize(..., tlg_gap=True)`),
  Greek punctuation `;`/`·` excluded from `accents.py`/`reader.py` word regexes, antilabe
  `<l part="M|F">` citation propagation, `working_analysis.py` gap-token span safety,
  `working_export.py` `manual_crasis` and stem suffixing fixes, LSJ vowel-length `headword_match`
  fix, single-pass GreBERTa bundle SHA-256 verification, SQLite cache closing, ZIP download
  cleanup, and `app.js` stale-export / draft-timestamp / `Content-Disposition` fixes.

Release 0.7.2 (core 0.4.2, rules 0.5.1, 19 November 2026) adopts the owner's
local-inventory constraint, `grc.selection.contextual-primary` 1.1.0. When Diogenes
offers two or more lemmata, the contextual (neural-first) proposal must be one of them.
Otherwise the model's canonical lookup, then GreTa, may supply one of those lemmata.
Failing both, the proposal is kept if it is an attested Diogenes headword (queued for
review, since it lacks a local analysis of the form); otherwise the word is left without
a lemma and queued for review. The raw proposal
stays in the evidence, and a review observation names the fallback. Unique local
lemmata, forms without an inventory and unresolved context are unchanged.
Development evidence only. Isaeus 1: 29 out-of-inventory strings removed, e.g. six
invented lemmata for Κλεωνύμου; 1 abstention. *Acharnians* frozen replay: 167 of 7,212
words change, 4 abstain; reviewed sample 134 → 140 of 177 (+7, −1: ἄγ' → ἄγη from the
canonical lookup).
Release 0.7.2 also names every output after its input file (see the app README), and no
longer consults Morpho during analysis (owner decision): since 0.7.0 it had no selection vote,
so automatic analysis loses only unverified evidence and its network dependency. The reviewer's
on-demand Morpho lookup is unchanged; old projects keep their stored Morpho evidence.
Engineering fix in core 0.4.2: when Diogenes lists a common noun beside a capitalised name
(πόλις/Πόλις, νόμος/Νόμος) without POS, the spelling selected by the selection rule is kept;
previously the identifier that sorted first, always the capital, won. GreTa proposals are
case-blind, so for them a PROPN tag or a capitalised written form keeps the name (Πολύαρχος).
Saved evidence: Isaeus 1, 21 lemmata corrected; *Acharnians*, 137. Eleven capitalised lemmata
remain where Diogenes offers only a capitalised entry; they are left for review.
Speed (decision-preserving, core 0.4.2): GreBERTa runs unpadded batches of up to 64 sentences
of identical subword and word length (*Acharnians* 557 s → 226 s; all 7,212 words identical,
|Δ log p| ≤ 3·10⁻⁸). Padded batches were faster (×5.6) but shifted log-probabilities by up to
0.011, which flipped one restoration (855 τἀγορᾷ: τῇ → τῷ ἀγορᾷ); they were rejected.
GreBERTa/GreTa outputs are cached on disk under the exact model/runtime/adapter identity
(warm re-run 0.1 s). `tools/parity_greberta.py` and
`tools/parity_pipeline.py` reproduce the checks.
Replay tool: `ag-scaife2antconc/tools/replay_selection.py`.

Release 0.7.1 adopts the owner-approved Diogenes/Morpheus + LSJ headword
standard, portable rule `grc.lemma.diogenes-headword` 1.0.0 in rules 0.5.0.
This post-selection policy preserves provider proposals and manual/deictic choices;
it does not rerank lexical analyses. Verified snapshot-qualified entry identities
and a sourced correspondence catalogue establish names. Uncovered, ambiguous and
provisional associations remain labelled for review. The explicit model-free
**Τυποποίηση λημμάτων** action also works on retained OGA annotations.
See the headword contract.
Review policy `grc.review.lemma-priority` 1.2.0 adds these unresolved naming and
association observations; its ranking margins and lexical selection are unchanged.

Release 0.7.0 calls `grc.selection.contextual-primary` 1.0.0 from rules 0.4.0,
implementing the owner's 8 November provider responsibilities. See the
implementation report for source priority,
lookup provenance, abstentions and validation. No word-specific exception is
added. Existing linguistic rule versions and legacy snapshot decisions remain.

The app's additional decisions are being documented as a separate, reusable
layer. See [AG Greek Rules](ag-greek-rules/README.md) for the first independent
implementation, its Python/JSON Lines interfaces and the rule catalogue.

The first portable rule is the existing τίς/τις accent scoring heuristic. Its
catalogue record distinguishes the grammatical distinction from the chosen
score weights, documents the chained-enclitic exception and names the source
backend version. Other existing rule families are catalogued with explicit
`backend_only` status; their entries do not imply that they have already been
ported.

Package 0.2.0 adds the owner-approved separate deictic headword convention:
`νυνί`, `οὑτοσί`, etc., with one word per occurrence. Scaife2AntConc 0.4.2 calls
this portable rule after provider selection and before manual corrections.
The earlier base-lemma proposal was withdrawn before release. The finite
inventory, conditions, abstentions and sources are recorded under
`grc.lemma.deictic-iota-headword` 1.0.0. Provider evidence and scores remain raw.

`grc.expansion.crasis` 1.1.0 (backend 0.3.2, catalogue 0.2.1) requires evidence
for restored inflected components. Generated variants are tested against the
local morphology. Explicit inflected restorations remain eligible. Without
support for a split, the written token and its whole-word evidence are retained
and the split is flagged for review. This applies wherever the crasis engine
is used. Coverage depends on the morphology and restoration inventory.
Smyth §§62–69 and 173 describe crasis and its accents. The owner approved the
evidence requirement after reviewing *Acharnians* 82. The rule has regression
checks, with independent accuracy still unmeasured.

New linguistic rules, altered exceptions and altered weights require discussion
with the owner before implementation. Technical extraction must preserve the
existing decisions and be verified by parity checks. Preserve raw model outputs
and record rule contributions separately. No independent accuracy claim follows
from passing the project's regression examples.

The new package's original code, documentation and catalogue use the MIT licence,
as selected by the owner. Model licences and the provenance/licensing of legacy
tables remain separate matters.

`grc.grammar.corroborated-pos` 1.0.0 was approved on 2026-11-07 following
*Acharnians* 266. It compares the local form's explicit grammatical classes
with those recorded for the selected dictionary headword. A matching single
class can replace incompatible fallback grammar, with inherited features
cleared. Finite verbal evidence is required for verbal replacements. Unknown,
mixed, conflicting and nonfinite verbal evidence abstains, and the established
compatible functional labels are retained. The portable assessment is in
ag-greek-rules 0.3.0 and the backend integration in version 0.3.3.

The policy acts after lemma and expansion selection. Its source classes,
original POS, proposed POS and reason are recorded as a rule contribution.
The provider tags, scores, candidate analyses and representative raw headword
evidence remain inspectable. This uses two views of Morpheus, whose inventory
can be incomplete. Independent accuracy is unmeasured. Existing snapshots and
OGA imports retain their recorded decisions on re-export.
