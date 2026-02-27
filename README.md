# FormalFinance

FormalFinance is a proof-oriented validation layer for SEC filing workflows.

It mirrors the UlamaI pattern for finance reporting:

- typed fact model for XBRL/iXBRL-like inputs
- deterministic rule execution over filing facts
- JSONL traces for replayable audit evidence
- report + certificate artifacts for pre-submission gating

## What this version covers

- Canonical filing schema (`contexts` + `facts` + source provenance)
- SEC `companyfacts` ingestion and normalization
- Three validation profiles:
  - `ixbrl-gating`: structural + DEI gating checks
  - `fsd-consistency`: adds accounting consistency rules
  - `companyfacts-consistency`: tuned for SEC companyfacts-derived filings
- Rule classes covering:
  - context/date semantics
  - concept QName validation
  - numeric unit/decimals/finiteness checks
  - duplicate and unit consistency checks
  - DEI metadata consistency checks
  - balance-sheet equation validation
  - period-type heuristics and sanity warnings
- Evidence-pack generation:
  - `report.json`
  - `trace.jsonl`
  - `summary.md`
  - `manifest.json`
  - `certificate.json` when clean

## Quick start

```bash
python3 -m formalfinance.cli profiles
python3 -m formalfinance.cli validate examples/filing_clean.json --profile ixbrl-gating
python3 -m formalfinance.cli evidence-pack examples/filing_risky.json --profile fsd-consistency --output-dir /tmp/formalfinance-pack
```

## SEC companyfacts workflow

```bash
# 1) Fetch (requires SEC-compliant User-Agent)
python3 -m formalfinance.cli fetch-companyfacts 320193 \
  --user-agent "FormalFinance/0.2.0 contact@example.com" \
  --output /tmp/apple.companyfacts.json

# 2) Normalize to FormalFinance canonical filing
python3 -m formalfinance.cli normalize-companyfacts /tmp/apple.companyfacts.json \
  --form 10-K \
  --output /tmp/apple.filing.json \
  --selection /tmp/apple.selection.json

# 3) Validate / build evidence
python3 -m formalfinance.cli validate /tmp/apple.filing.json --profile companyfacts-consistency
python3 -m formalfinance.cli evidence-pack /tmp/apple.filing.json --profile companyfacts-consistency --output-dir /tmp/apple-pack
```

## Canonical filing JSON shape

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
      "decimals": 0,
      "source": {
        "accn": "0000123456-26-000001",
        "form": "10-K"
      }
    }
  ]
}
```

## Exit codes

- `0`: clean
- `1`: review (warnings only)
- `2`: risk (errors present)

## Roadmap

Next phases are in [`docs/roadmap.md`](/Users/blackfrog/Projects/formal-finance/docs/roadmap.md).
