# Contributing rule changes

The application owner has requested that new linguistic decisions be discussed
before implementation. Extracting existing behaviour without changing decisions
is different from adding a rule, changing an exception, or changing a weight.

1. Describe the linguistic problem and discuss the proposed decision with the
   project owner before implementing a behaviour change.
2. Identify the rule with a stable ID. Give each behaviour its own version;
   changing forms, exceptions, weights, scope or boundary treatment requires a
   new behaviour version and a changelog entry.
3. State the category: input normalisation, candidate generation, linguistic
   ranking evidence, engineering ranking policy, review policy, or explicit
   lexicographic normalisation policy (headword convention, separate from ranking).
4. Cite the exact grammar section or lexical entry. Explain what the source
   supports and what is an implementation choice. Record unknown provenance
   rather than inventing an attribution.
5. State prerequisites and input conventions, supported dialect/genre or their
   unknown coverage, scope, exceptions and conditions requiring abstention.
6. Include positive examples, counterexamples and boundary cases. Keep those
   regression cases distinct from an independent accuracy evaluation. A rule
   suggested by a failure in Acharnians is not independently validated by passing
   that same Acharnians example.
7. Return evidence and a trace; do not mutate provider predictions. A caller's
   final selection should retain raw proposals, provider scores, every applied
   rule ID/version and its contribution.
8. Keep weights/configuration separate from the linguistic claim and document
   uncalibrated margins as such. Preserve alternative analyses.
9. Check ownership/licensing for original code, tables, examples and third-party
   assets separately before sharing a release.

This first package contains a portable extraction and a partial catalogue, not
an independently validated new lemmatiser. Future ports must have parity checks
against their declared source version before the app changes implementations.
