# FormalFinance Roadmap

## Phase 0 (this repo now)

- Normalized filing schema for contexts/facts/dimensions
- Deterministic rule engine
- Pre-submission risk report and clean certificate output
- JSONL traces for replayable evidence packs

## Phase 1: EDGAR/iXBRL pre-flight

- Inline XBRL structural checks against EDGAR-oriented constraints
- HTML/iXBRL attachment gatekeeping rules
- Custom taxonomy namespace/prefix/label/relationship checks
- Submission suspension risk scoring

## Phase 2: Proof-carrying conformance

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
