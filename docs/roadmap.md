# FormalFinance Roadmap

## Phase 0 (completed)

- Normalized filing schema for contexts/facts/dimensions
- Deterministic rule engine
- Pre-submission risk report and clean certificate output
- JSONL traces for replayable evidence packs

## Phase 1 (current): EDGAR/iXBRL pre-flight foundation

- SEC companyfacts fetch + normalization to canonical filing schema
- Structural preflight checks (context/date semantics, QName shape, unit/duplicate consistency)
- Inline XBRL document/attachment gating checks (HTML extension, active content, external references)
- Inline XBRL submission suspension risk detection from package-level XBRL errors
- Taxonomy validation module (namespace/prefix, labels, relationship endpoint integrity, calculation cycle detection)
- Accounting checks (balance-sheet equation, period-type heuristics)
- Evidence-pack generation (report, trace, summary, manifest, clean certificate)

## Phase 2: EDGAR rulebook and proof-carrying conformance

- Lean rulebook for a scoped subset of EDGAR/XBRL constraints
- Proof object export from checker and replay verification
- Rule-to-fact provenance chain for each pass/fail outcome

## Phase 3: Dataset verification and issuer integrations

- SEC Financial Statement Data Set consistency proofs
- Diff proofs isolating minimal inconsistent fact sets
- Integrations with EDGARization/XBRL filing workflows as last-mile verifier

## Architecture notes

- Keep public SEC and ecosystem datasets as untrusted inputs.
- Treat evidence output as compliance conformance, not investment guidance.
- Use strict input sanitization before any LLM-facing workflow touches filing HTML/text.
