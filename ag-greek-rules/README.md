# AG Greek Rules

Version **0.5.0** adds `assess_headword`, portable policy
`grc.lemma.diogenes-headword` 1.0.0. A caller supplies verified, fingerprinted
Diogenes/LSJ entry records. The assessment returns a headword, entry identity and
trace, or an explicit abstention retaining the original proposal. It does not
select competing analyses. Manual choices and separate deictic -ι headwords stay
authoritative. The original sourced correspondence catalogue is
[`headword-mappings.json`](ag_greek_rules/headword-mappings.json); it is not a
redistributed lexicon. See the [headword contract](../RULES.md).

```python
from ag_greek_rules import assess_headword

result = assess_headword("τρία", "τρία", pos="NUM", entries=[
    {"id": "lsj:your-dictionary-sha256:entry-id", "headword": "τρεῖς", "verified": True}
])
assert result.headword == "τρεῖς"
print(result.as_dict())
```

The same request works through JSON Lines with `rule_id` set to
`grc.lemma.diogenes-headword`. Reference IDs distinguish homographs; a spelling
match alone does not choose between them. A supplied verified provider entry can
identify one homograph. Case-only matches without that identity abstain, preserving
proper/common distinctions. Linguistic accents remain significant. Vowel-length
typography is omitted in plain output; original source keys remain in evidence.

Version **0.4.0** adds `select_lemma`, portable provider-responsibility policy
`grc.selection.contextual-primary` 1.0.0. One distinct local lemma is retained;
context selects ambiguous/unseen forms; GreTa may help unresolved cases. Morpho
is additional evidence. The trace distinguishes selection and model-owned lookup
provenance without modifying providers. This is engineering policy, not a new
Greek grammatical rule or calibrated accuracy model. Existing linguistic rule
versions are unchanged. See the [contract](../RULES.md).

A small, independent package for making the project's additional linguistic
heuristics inspectable and reusable. It requires Python 3.10 or later and only
the standard library. It does not require Diogenes, spaCy, GreTa, Morpho, a GUI,
network access, or model weights.

This experimental release, version **0.5.0**, also provides the extracted **τίς/τις
accent scoring rule**, the **separate deictic headword policy** and
**corroborated part-of-speech consistency**.
The catalogue also documents seven existing backend rule
families. Those entries are explicitly marked `backend_only`: this release does
not implement their algorithms independently. The inventory is not exhaustive.
The crasis catalogue entry now documents the inflected-form evidence requirement
in backend 0.3.2.

Scaife2AntConc 0.4.2 calls this package for the deictic policy, after model
selection and before manual corrections. Its existing pronoun scoring code
remains unchanged; the portable extraction has a parity check against backend
0.3.1. Do not maintain divergent implementations under the same rule version.

## Deictic headwords

The owner selected separate suffixed headwords, following LSJ: `νυνί` remains
distinct from `νῦν`, while `οὑτοσί`, `τουτονί`, `τουτί`, etc. share the headword
`οὑτοσί`. Each remains one word; -ι is never emitted as a second token.

```python
from ag_greek_rules import assess_deictic

proposal = assess_deictic('τουτονὶ', 'οὗτος')
assert proposal.status == 'applied'
assert proposal.headword == 'οὑτοσί'
assert proposal.token_count == 1
print(proposal.as_dict())
```

This is a finite lexical normalisation policy, not a new ranking weight or a
complete import of LSJ. Both a documented surface and an existing selected lemma
within its family are required. Unknown/unaccented forms and forms containing
intercalated particles are outside scope. Unrelated selected lemmas cause
abstention; the proposal does not silently repair them. The complete inventory,
sources, examples and counterexamples are in the catalogue under stable rule
`grc.lemma.deictic-iota-headword`, behaviour version `1.0.0`.

The app retains original model/lexicon candidates and scores, records the
rule separately in `analysis-review.jsonl`, and shows an annotation in review.
Manual corrections take precedence. Re-export of pre-existing saved analyses
preserves their original decisions; it does not silently migrate their lemmata.
The base-lemma proposal was withdrawn before release and is not executable.

Other languages can use JSON Lines:

```json
{"rule_id":"grc.lemma.deictic-iota-headword","form":"τουτονὶ","candidate_lemma":"οὗτος"}
```

## Corroborated grammatical class

`assess_pos` compares supplied grammatical inventories for a form and its
selected headword. Matching explicit classes can repair an incompatible fallback
part of speech. Both inventories must be unambiguous. Verbal forms require an
explicit finite analysis throughout the form inventory. Participles, infinitives,
unknown classes and conflicting inventories abstain. The existing compatible
labels for auxiliaries and indeclinable words are retained.

The result records rule `grc.grammar.corroborated-pos` version `1.0.0`, input
classes, original POS, proposed POS, reason and whether inherited features must
be cleared. The Python API and JSON Lines interface accept the same fields.
The caller supplies `form`, `candidate_lemma`, `original_pos`, `form_analyses`
and `lemma_analyses`, with each analysis containing `pos` and optional UD `msd`.

The backend applies this after selecting a lemma and expansion. Raw OdyCy tags,
Morpheus candidates, GreTa outputs and scores remain available alongside the
rule trace and representative headword evidence. This policy also supplies a
missing class when both inventories corroborate it. Its source inventories
can be incomplete, and both can come from Morpheus. It is an experimental
consistency check with regression examples, with independent accuracy unmeasured.

For *Acharnians* 266, the selected `προσλέγω` has verbal dictionary evidence,
and both local analyses of `προσεῖπον` are finite verbs. The policy proposes
`VERB` and clears the adjective features inherited from OdyCy. The original
`ADJ` tag is retained in the provider evidence. See the catalogue for the exact
conditions, source references and counterexamples.

## Use from Python

From this directory, or after installing the wheel:

```python
from ag_greek_rules import assess_pronoun

proposal = assess_pronoun("τί", "τίς", following="δέ")
print(proposal.score_delta)  # 2.0
print(proposal.as_dict())    # complete, JSON-compatible rule trace

counterexample = assess_pronoun("τίς", "τίς", following="τινα")
assert counterexample.status == "abstained"
assert counterexample.score_delta == 0
```

The caller supplies an existing candidate lemma. The result is evidence about
that candidate, not a replacement lemma or an instruction to accept it. Preserve
the provider's raw prediction and score separately; record the returned trace
if the rule contributes to a final selection. The `expected_lemma` field describes
the rule's local expectation, not a sentence-level judgement.

`following` must be the immediately following word in the same uninterrupted
phrase. Pass `None` at punctuation/boundaries, or when no following word is
available. Omitting context can prevent the rule from detecting a chained
enclitic. The recognised enclitic list is finite. Contracted `τοῦ/τῷ` are outside
this rule. Original input strings are retained in the trace; case/Unicode
normalisation is internal to the spelling comparison. Candidate lemmas must use
the exact canonical spellings `τίς` and `τις`, as in the existing backend.

## Use from other languages

The command-line interface reads and writes UTF-8 JSON Lines:

```json
{"form":"τί","candidate_lemma":"τίς","following":"δέ"}
{"form":"τίς","candidate_lemma":"τίς","following":"τινα"}
```

Send these lines to `python -X utf8 -m ag_greek_rules` or `ag-greek-rules`.
There is one JSON response per non-empty line. Invalid requests return an
`error` response; the process returns status 1 if any request failed. Unsupported
rule identifiers are rejected rather than silently running another rule.

Use `python -X utf8 -m ag_greek_rules --catalog` for the machine-readable
catalogue, also available through `ag_greek_rules.catalog()`.

## What a rule record contains

Each catalogue entry has a stable identifier, its own behaviour version,
category, implementation availability, conditions, effects, exceptions, examples,
sources, parameters and validation status. References to the current backend
include its version, entry point and source-file SHA-256.

For the portable pronoun rule:

- ID: `grc.pronoun.interrogative-indefinite-accent`.
- Behaviour version: `1.0.0`; package version: `0.2.1`.
- Linguistic basis: [Smyth §§154, 181–183, 334](https://ccel.org/s/smyth/grammar/html/smyth_1f_uni.htm).
- Score contributions: **+2 / −2**. These magnitudes are engineering choices,
  not numbers supplied by the grammar and not calibrated probabilities.
- Status: experimental; regression and parity checks establish implementation
  consistency, not accuracy on an independent corpus.

The catalogue distinguishes input normalisation, candidate generation, linguistic
ranking evidence, engineering ranking policies and review policies. A review
policy can change a work list without changing any lemma. These categories must
not be presented as equally authoritative linguistic facts.

See [CONTRIBUTING.md](CONTRIBUTING.md) before adding or changing a rule.

## Reproduce checks

The independent tests need no third-party test runner:

```text
python -X utf8 -B -m unittest discover -s tests -p "test_*.py" -v
```

The optional `test_backend_parity.py` also needs the existing project's backend
on `PYTHONPATH` and its `beta-code` dependency. It compares scores over the full
listed spelling sets, Unicode/case variants and following-word contexts. It
does not load any linguistic models.

## Licensing and attribution

The original code, documentation and catalogue in this package are licensed
under the **MIT licence**, selected by the project owner; see [LICENSE](LICENSE).
Neither model weights nor training data are included, and their licences are unchanged.
The legacy crasis/elision tables are referenced, not redistributed in this
package; their entry-level provenance/licensing needs separate documentation.

For context: the original [bowphs/GreTa model](https://huggingface.co/bowphs/GreTa)
declares Apache-2.0. The trained pyaegean model used by the app is declared
CC BY-SA 4.0 in its [publisher's notice](https://github.com/ryanpavlicek/pyaegean/blob/main/NOTICE).
Those licences are separate from the MIT licence for this package's original rule code.

For reproducible attribution, record the package version, rule identifier and
rule behaviour version, together with the grammar reference and any rule
parameters changed by the caller. Do not imply endorsement by the grammar's
author or the providers whose predictions are being ranked.
