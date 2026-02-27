# FormalFinance Roadmap

## Phase 0 (completed)

- Normalized filing schema for contexts/facts/dimensions
- Deterministic rule engine
- Pre-submission risk report and clean certificate output
- JSONL traces for replayable evidence packs

## Phase 1 (current): EDGAR/iXBRL pre-flight foundation

- SEC companyfacts fetch + normalization to canonical filing schema
- Accession-based raw filing package ingestion (`cik + accession -> ixbrl + taxonomy + facts`)
- SEC recent-filing discovery tooling for controlled 50–100 filing pilots
- Structural preflight checks (context/date semantics, QName shape, unit/duplicate consistency)
- Inline XBRL document/attachment gating checks (HTML extension, active content, external references)
- Inline XBRL submission suspension risk detection from package-level XBRL errors
- Taxonomy validation module (namespace/prefix, labels, relationship endpoint integrity, calculation cycle detection)
- Accounting checks (balance-sheet equation, period-type heuristics)
- Evidence-pack generation (report, trace, summary, manifest, clean certificate)
- Baseline discrepancy comparison metrics for validator agreement analysis
- HTTP API service with API-key auth and run-history persistence for hosted deployments
- Service hardening controls (rate limits, request-size guardrails, CIDR allowlist)
- Rulebook metadata generation for governance and customer audit mapping
- Optional LLM advisory integration (default off) with provider connectors (Ollama/OpenAI-compatible)
- Remediation workflow artifacts (`triage.json`, HTML summary, triage update commands)
- Operational endpoints and tooling (`/v1/metrics`, `/v1/migrations`, `db-migrate`, `db-status`)
- Signed certificates (HS256) with verification workflow
- Replayable proof bundles (`proof.json`) and deterministic proof replay endpoint/CLI
- Optional Lean-backed arithmetic claim replay (`replay-proof --lean-check`)

## Phase 2: EDGAR rulebook and stronger proof-carrying conformance

- Lean rulebook for a scoped subset of EDGAR/XBRL constraints
- Expand Lean replay from arithmetic claims to broader EDGAR/XBRL rule classes
- Rule-to-fact provenance minimization for each pass/fail outcome

## Phase 3: Dataset verification and issuer integrations

- SEC Financial Statement Data Set consistency proofs
- Diff proofs isolating minimal inconsistent fact sets
- Integrations with EDGARization/XBRL filing workflows as last-mile verifier

## Architecture notes

- Keep public SEC and ecosystem datasets as untrusted inputs.
- Treat evidence output as compliance conformance, not investment guidance.
- Use strict input sanitization before any LLM-facing workflow touches filing HTML/text.
