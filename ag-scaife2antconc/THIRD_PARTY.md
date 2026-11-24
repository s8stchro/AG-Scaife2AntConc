# Trained GreTa model used by version 0.2

The application requires the **pyaegean grc-lemma-neural-v1** fine-tuning of
**GreTa** (Riemenschneider and Frank, 2023). This is **not** Celano's OGA checkpoint,
and is not the unfine-tuned `bowphs/GreTa` base model.

- Release: https://github.com/ryanpavlicek/pyaegean/releases/tag/grc-lemma-neural-v1
- Author and training/inference documentation: Ryan Pavlicek,
  https://github.com/ryanpavlicek/pyaegean/blob/main/src/aegean/greek/neural_lemmatizer.py
- Archive SHA-256: `38126872e7a5be6389054062d4789ce5b6fc7e84327b07c2b93649a6f0f1a228`
- Model licence: **CC BY-SA 4.0**, derived from AGDT (CC BY-SA 3.0),
  Pedalion and Gorman (CC BY-SA 4.0), as documented in the release manifest.
- Base-model research: Riemenschneider and Frank, *Exploring Large Language Models
  for Classical Philology*, https://aclanthology.org/2023.acl-long.846/.

The installer fetches the weights separately and verifies their checksum. It retains
their identity, licence, source URL and file checksums in `model-info.json`.
The application performs neural inference directly and does **not** use the release's
gold lookup table. There is no claim that its own test texts were held out from the
model's training; those texts are functional checks, not an accuracy benchmark.

This GreTa checkpoint consumes an **inflected word form**, not a sentence. Through
0.6.2, OdyCy supplied contextual grammar. Release 0.7.0 instead uses GreBERTa joint
for contextual disambiguation, POS and morphology; OdyCy is not loaded. Full
Morpheus evidence comes from the user's Diogenes installation.
Morpho suggestions have unknown manual-verification status unless independently established;
complete provider responses are retained in the optional Morpho cache.

## GreBERTa joint, added in 0.7.0

- Model: pyaegean `grc-joint-v3`, GreBERTa encoder with a joint lemma-edit head.
- Release: https://github.com/ryanpavlicek/pyaegean/releases/tag/grc-joint-v3
- Archive SHA-256: `f646d34a08dbf612abbe076c27188f077c2289da0b7bbbc7116bfe807112b06e`.
- Runtime: `pyaegean==0.61.0`, Apache-2.0; its upstream licence and NOTICE remain in its wheel.
- Recorded model terms: CC BY-SA 4.0, with AGDT CC BY-SA 3.0 and Gorman/Pedalion CC BY-SA 4.0 provenance.

The app uses the pinned preprocessing, neural-first edit/lookup composition and
UD morphology conversion, with CPU ONNX inference. It records the raw neural
lemma, model-owned canonical lookup result, actual composition branch and asset
receipt separately. It does not load the toolkit's full linguistic ensemble.
Model-owned lookups are not independent gold or dictionary verification.
These model/runtime terms are separate from the MIT original reusable rules.
