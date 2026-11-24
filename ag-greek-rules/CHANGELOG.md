# Changes

## 0.8.1

Catalogue: `grc.expansion.crasis` 1.2.0 (backend), owner request of 24 November 2026. ὦ fused with a
vocative that the crasis table does not list (ὦναίσχυντε, ὦταῖρε, ὦργεῖοι) is restored as ὦ + the
vocative when the written form has no analysis of its own and Diogenes analyses the restored word as
vocative or nominative. Real words beginning with ὦ (ὦμος, ὦτα, ὦσι) are unaffected.

## 0.8.0

New `grc.selection.generated-lemma-check` 1.0.0, owner decision of 24 November 2026. For a form
without any local analysis the contextual model's generated lemma is checked. A low-confidence
proposal (log p <= -1.0) that does not fit the form is replaced by the one reliable lemma of the
same text whose stem begins the form (Isaeus 1 Ποσειδίππῳ: Ποσειδεώπ -> Ποσείδιππος). Otherwise a
proposal that cannot be a Greek headword (two accents, initial vowel without breathing, impossible
final letter, non-letter) is removed: no lemma. Both are review observations
(`grc.review.lemma-priority` 1.5.0). Trial on an Aristophanes project: 18 replaced, 128 removed of
961 generated lemmata. `headword_problems()` is exported for other callers.

## 0.7.0

New `grc.grammar.proper-noun` 1.0.0, owner decision of 24 November 2026 (option A). A NOUN whose
selected lemma and written form both begin with a capital is PROPN. A capitalised lemma on a
lower-case form abstains (kept NOUN) and is reported for review, since it is usually a wrongly
capitalised lemma. Adjectives, adverbs and verbs keep their class; manual choices are untouched.
Motivated by the contextual tagger having no PROPN label: in an Aristophanes project 2,826
capitalised NOUN lemmata stood against 2 manual PROPN. Backend review policy
`grc.review.lemma-priority` 1.4.0 treats the abstention as an urgent observation.

## 0.6.1

Catalogue only: the implementation reference of `grc.selection.contextual-primary` names
project version 0.6.0 (it named an unreleased 0.5.3). No rule behaviour changed.

## 0.6.0

Owner decisions of 20 November 2026.

- `grc.selection.contextual-primary` 1.3.0. On an ambiguous form whose contextual top-1 lies outside
  the local inventory, check the model-owned canonical lemma first, then the contextual model's ranked
  top-k edit-script candidates (`context_topk_inventory_disambiguation`), then GreTa, then the
  attested-headword rescue. A top-k candidate may not replace an attested top-1 headword when it trails
  by more than 2.5 nats (`TOPK_KNOWN_HEADWORD_MAX_GAP`). Version 1.2.0 (top-k before canonical lookup)
  was superseded on the same day.
- `grc.pronoun.interrogative-indefinite-accent` 1.1.0. τίς/τί abstain before a possible enclitic only
  when that word could really be enclitic: orthotonic words (νῦν, πότε, εἶμι) no longer count, elided
  enclitics (γ’, τ’, μ’, σ’, ποτ’, ἐστ’) do; more forms of εἰμί and φημί; ἄττα added to the indefinites.
- `grc.lemma.deictic-iota-headword` 1.1.0. More deictic forms of οὗτος, ὅδε, ἐκεῖνος and τοσοῦτος;
  new families τοιοῦτος, τηλικοῦτος, ἐνθάδε, ἐνταῦθα, δεῦρο and ἐντεῦθεν.
- `grc.lemma.diogenes-headword` 1.1.0. A preserved deictic headword keeps the identifier of its
  exactly matching verified entry.

## 0.5.1

`grc.selection.contextual-primary` 1.1.0, owner decision of 19 November 2026.
When the local inventory has two or more lemmata, a contextual proposal must be one of them.
Case and grave/acute are ignored in this comparison. Otherwise the model-owned canonical lookup,
then GreTa, may supply one of those lemmata. Failing both, the proposal is kept if it is an attested headword of the local morphology as a whole (optional `known_lemma` argument); otherwise the policy abstains. The trace now
records `context_lemma` and `canonical_lemma` beside the selection. Unique local lemmata, forms
without an inventory and unresolved context keep their 1.0.0 behaviour. Prompted by Isaeus 1,
where the neural-first branch exported 29 strings outside an available inventory (e.g. six
invented lemmata for Κλεωνύμου). That sample is development evidence, not a validation.

## 0.5.0

Added owner-approved headword convention `grc.lemma.diogenes-headword` 1.0.0,
with Python and JSON Lines assessments and an original, sourced correspondence
catalogue. Naming is separate from lexical selection. Verified LSJ entry identities,
provisional links, ambiguous homographs, uncovered proposals, manual choices and
deictic exceptions are explicit. No dictionaries or model data enter the MIT package.

Backend review policy `grc.review.lemma-priority` 1.2.0 identifies unresolved
headword mappings and provisional dictionary associations. Selection weights,
decoder ordering, restoration inventories and previous linguistic rules retain
their existing behaviour versions.

## 0.4.0

Added `grc.selection.contextual-primary` 1.0.0 for the owner's 8 November 2026
provider responsibilities. Unique local lemmas, contextual disambiguation and
unresolved GreTa assistance remain distinct; Morpho does not vote. Raw neural and
lookup evidence remains separate from selection. Prior linguistic rule versions
and the original MIT licence are unchanged.

Backend review policy `grc.review.lemma-priority` is now 1.1.0: unresolved
context, shortened context and ambiguous model-lookup fallbacks are identified
explicitly. Its uncalibrated close-score margin is unchanged. Policy metadata
remains separate from raw provider evidence.

## 0.3.0

Added portable rule `grc.grammar.corroborated-pos` behaviour version `1.0.0`
after owner approval on 2026-11-07. It proposes a corroborated grammatical
class using explicit form and headword evidence. Unknown, mixed and nonfinite
verbal analyses abstain. Existing compatible functional labels are retained.
The trace records the decision separately from provider outputs and ranking.

The prior pronoun and deictic behaviours retain their rule versions.
