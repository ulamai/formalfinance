# FormalFinance

FormalFinance is a proof-oriented validation layer for SEC filing workflows.

It mirrors the UlamAI pattern for finance reporting:

- typed fact model for XBRL/iXBRL-like inputs
- deterministic rule execution over filing facts
- JSONL traces for replayable audit evidence
- report + certificate artifacts for pre-submission gating

## What this version covers

- Canonical filing schema (`contexts` + `facts` + source provenance)
- SEC `companyfacts` ingestion and normalization
- Three validation profiles:
  - `ixbrl-gating`: structural + iXBRL preflight + taxonomy + DEI checks
  - `fsd-consistency`: adds accounting consistency rules on top of full preflight
  - `companyfacts-consistency`: tuned for SEC companyfacts-derived filings
- Pilot tooling:
  - readiness checker for sample/rule targets
  - recent SEC filing discovery for 50–100 filing pilot batches
  - baseline discrepancy comparison metrics
- Product service tooling:
  - authenticated HTTP API server
  - SQLite run history for operations/audit logs
  - rulebook metadata endpoint for governance mapping
  - request-size, rate-limit, and CIDR allowlist controls
  - migration + metrics endpoints for operations
- Rule classes covering:
  - iXBRL primary/attachment gating checks
  - iXBRL submission suspension risk detection from XBRL errors
  - disallowed HTML and external reference checks
  - taxonomy namespace/prefix consistency checks
  - taxonomy label and relationship integrity checks
  - taxonomy calculation-cycle detection
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
python3 -m formalfinance.cli ingest-accession 0000320193 0000320193-26-000073 --user-agent "FormalFinance/0.1.2 contact@example.com" --output /tmp/apple.ingested.json
python3 -m formalfinance.cli evidence-pack examples/filing_risky.json --profile fsd-consistency --output-dir /tmp/formalfinance-pack
python3 -m formalfinance.cli pilot-readiness
```

## Accession Ingestion Workflow

```bash
# Build normalized filing directly from an SEC accession package
python3 -m formalfinance.cli ingest-accession 0000320193 0000320193-26-000073 \
  --user-agent "FormalFinance/0.1.2 contact@example.com" \
  --metadata /tmp/apple.ingest.meta.json \
  --output /tmp/apple.ingested.filing.json

# Validate immediately
python3 -m formalfinance.cli validate /tmp/apple.ingested.filing.json --profile ixbrl-gating
```

## SEC companyfacts workflow

```bash
# 1) Fetch (requires SEC-compliant User-Agent)
python3 -m formalfinance.cli fetch-companyfacts 320193 \
  --user-agent "FormalFinance/0.1.2 contact@example.com" \
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

## Pilot Readiness And Sampling

```bash
# Check pilot prerequisites (rule count window, scope coverage, baseline tooling)
python3 -m formalfinance.cli pilot-readiness --min-rules 30 --max-rules 50 --min-filings 50 --max-filings 100

# Discover candidate filings for a pilot batch (requires SEC User-Agent)
python3 -m formalfinance.cli discover-recent-filings \
  --forms 10-K,10-Q \
  --max-filings 100 \
  --cik-limit 250 \
  --filed-on-or-after 2025-10-01 \
  --user-agent "FormalFinance/0.1.2 contact@example.com" \
  --output /tmp/formalfinance.pilot.filings.json
```

## Baseline Comparison

Use `compare-baseline` to score agreement between FormalFinance and another validator.

```bash
python3 -m formalfinance.cli compare-baseline \
  /tmp/formalfinance.report.json \
  /tmp/baseline.report.json \
  --output /tmp/formalfinance.baseline.compare.json
```

Accepted baseline JSON shapes:

```json
{
  "findings": [
    { "code": "ixbrl.primary_document_constraints", "severity": "error" },
    { "code": "taxonomy.relationship_target_exists", "severity": "warning" }
  ]
}
```

Run baseline parity package over a manifest:

```bash
python3 -m formalfinance.cli benchmark-baseline examples/benchmark_manifest.json \
  --pass-rate 0.95 \
  --output /tmp/formalfinance.benchmark.result.json
```

Manifest shape:

```json
{
  "cases": [
    {
      "id": "clean-fixture",
      "filing": "examples/filing_clean.json",
      "baseline_report": "examples/baseline_clean.json",
      "profile": "ixbrl-gating"
    }
  ]
}
```

Or:

```json
{
  "errors": [{ "id": "RULE-123" }],
  "warnings": [{ "id": "RULE-456" }]
}
```

## Run As A Service

```bash
# API keys can be set in env or passed via --api-keys
export FORMALFINANCE_API_KEYS="dev-key-1"
python3 -m formalfinance.cli serve --host 127.0.0.1 --port 8080 --db-path /tmp/formalfinance.runs.sqlite3
```

Optional LLM advisory layer (default is off):

```bash
# Default-off behavior: no LLM calls unless explicitly enabled.
# Enable globally for the service using env:
export FORMALFINANCE_LLM_ENABLED=1
export FORMALFINANCE_LLM_PROVIDER=ollama        # or openai-compatible
export FORMALFINANCE_LLM_MODEL=llama3.1:8b-instruct-q4_K_M
export FORMALFINANCE_LLM_BASE_URL=http://127.0.0.1:11434

# Or use CLI flags:
python3 -m formalfinance.cli serve \
  --host 127.0.0.1 --port 8080 \
  --api-keys dev-key-1 \
  --max-request-bytes 2000000 \
  --rate-limit-per-minute 120 \
  --allowlist-cidrs "127.0.0.1/32" \
  --llm-enabled \
  --llm-provider ollama \
  --llm-model llama3.1:8b-instruct-q4_K_M \
  --llm-base-url http://127.0.0.1:11434
```

Example API calls:

```bash
curl -s http://127.0.0.1:8080/v1/healthz

curl -s -H "X-API-Key: dev-key-1" http://127.0.0.1:8080/v1/profiles

curl -s -X POST \
  -H "X-API-Key: dev-key-1" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8080/v1/validate \
  -d @<(jq -n --argfile filing examples/filing_clean.json '{profile:"ixbrl-gating", filing:$filing, tenant_id:"demo"}')

curl -s -H "X-API-Key: dev-key-1" "http://127.0.0.1:8080/v1/runs?limit=20&tenant_id=demo"

# Per-request override (keeps default-off globally if desired)
curl -s -X POST \
  -H "X-API-Key: dev-key-1" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8080/v1/validate \
  -d @<(jq -n --argfile filing examples/filing_risky.json '{
        profile:"fsd-consistency",
        filing:$filing,
        llm:{enabled:true, provider:"ollama", model:"llama3.1:8b-instruct-q4_K_M", base_url:"http://127.0.0.1:11434"}
      }')
```

Service endpoints:

- `GET /v1/healthz`
- `GET /v1/profiles`
- `GET /v1/rulebook?profile=ixbrl-gating|fsd-consistency|companyfacts-consistency|all`
- `GET /v1/runs?limit=100&tenant_id=...`
- `GET /v1/metrics`
- `GET /v1/migrations`
- `POST /v1/ingest-accession`
- `POST /v1/validate`
- `POST /v1/certify`
- `POST /v1/compare-baseline`
- `POST /v1/pilot-readiness`

`/v1/validate` and `/v1/certify` return `advisory` in response:

- `status: disabled` by default
- `status: ok` when LLM suggestions are generated
- `status: error` when configured provider call fails

### Docker

```bash
docker build -t formalfinance:0.1.2 .
docker run --rm -p 8080:8080 \
  -e FORMALFINANCE_API_KEYS="dev-key-1" \
  -e FORMALFINANCE_RATE_LIMIT_PER_MINUTE="120" \
  -v "$PWD/.formalfinance-data:/data" \
  formalfinance:0.1.2
```

## Triage Workflow

`evidence-pack` now emits `triage.json` and `summary.html`.

```bash
# Initialize triage from any report
python3 -m formalfinance.cli triage-init /tmp/formalfinance.report.json --owner analyst@example.com --output /tmp/triage.json

# Update one finding
python3 -m formalfinance.cli triage-update /tmp/triage.json \
  --finding-id ixbrl.primary_document_constraints:0001 \
  --status in_progress \
  --assignee analyst@example.com \
  --note "Primary filing document replacement requested"
```

## Operations Docs

- API reference: [`docs/api/v1.md`](/Users/blackfrog/Projects/formal-finance/docs/api/v1.md)
- Operations/runbook: [`docs/operations.md`](/Users/blackfrog/Projects/formal-finance/docs/operations.md)
- Service expectations: [`docs/sla.md`](/Users/blackfrog/Projects/formal-finance/docs/sla.md)

DB migration commands:

```bash
python3 -m formalfinance.cli db-migrate --db-path /tmp/formalfinance.runs.sqlite3
python3 -m formalfinance.cli db-status --db-path /tmp/formalfinance.runs.sqlite3
```

## Canonical filing JSON shape

```json
{
  "accession": "0000123456-26-000001",
  "cik": "0000123456",
  "entity": "Example Corp",
  "period_end": "2025-12-31",
  "taxonomy": "us-gaap-2025",
  "ixbrl": {
    "primary_document": {
      "filename": "example-2025-10k.htm",
      "is_inline_xbrl": true,
      "contains_ix_header": true,
      "xbrl_errors": []
    },
    "attachments": []
  },
  "taxonomy_package": {
    "namespaces": [{ "prefix": "ff", "uri": "http://formalfinance.example/taxonomy/2025", "is_standard": false }],
    "elements": [{ "concept": "ff:AdjustedEbitda", "is_custom": true }],
    "labels": [{ "concept": "ff:AdjustedEbitda", "text": "Adjusted EBITDA" }],
    "relationships": []
  },
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
