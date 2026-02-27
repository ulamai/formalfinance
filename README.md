# FormalFinance

FormalFinance is a proof-oriented validation layer for SEC filing workflows.

It mirrors the UlamaI pattern for finance reporting:

- typed fact model for XBRL/iXBRL-like inputs
- deterministic rule execution over filing facts
- JSONL traces for replayable audit evidence
- report + certificate artifacts for pre-submission gating

## What this MVP covers

- Canonical filing schema (`contexts` + `facts`)
- Two validation profiles:
  - `ixbrl-gating`: structural and key concept checks
  - `fsd-consistency`: adds balance-sheet arithmetic consistency
- JSON risk report generation
- JSONL trace logging per rule and finding
- Clean-run certificate issuance

## Quick start

```bash
python3 -m formalfinance.cli validate examples/filing_clean.json --profile ixbrl-gating
python3 -m formalfinance.cli validate examples/filing_risky.json --profile fsd-consistency --trace /tmp/formalfinance.trace.jsonl
python3 -m formalfinance.cli certify examples/filing_clean.json --profile fsd-consistency --certificate /tmp/formalfinance.cert.json
```

## Filing JSON shape

```json
{
  "accession": "0000123456-26-000001",
  "cik": "0000123456",
  "entity": "Example Corp",
  "period_end": "2025-12-31",
  "taxonomy": "us-gaap-2025",
  "contexts": {
    "c2025": { "period_type": "instant", "instant": "2025-12-31" }
  },
  "facts": [
    {
      "id": "f-assets",
      "concept": "us-gaap:Assets",
      "context_id": "c2025",
      "value": 1000,
      "unit": "USD",
      "decimals": 0
    }
  ]
}
```

## Current scope vs target vision

This repository is intentionally focused on the structured-fact core you outlined:

- iXBRL gating and suspension risk checks
- custom taxonomy conformance checks (seeded through rule architecture)
- financial statement dataset consistency checks
- audit evidence packaging through trace logs + certificate artifacts

The next phases are documented in [`docs/roadmap.md`](/Users/blackfrog/Projects/formal-finance/docs/roadmap.md).
