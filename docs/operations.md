# Operations Guide

## Deployment Modes

- CLI-only batch mode
- API service mode (`formalfinance serve`)
- Containerized mode via Docker

## Recommended Service Flags

```bash
formalfinance serve \
  --host 0.0.0.0 \
  --port 8080 \
  --db-path /data/runs.sqlite3 \
  --api-keys "$FORMALFINANCE_API_KEYS" \
  --cert-signing-secret "$FORMALFINANCE_CERT_SIGNING_SECRET" \
  --cert-signing-key-id "$FORMALFINANCE_CERT_SIGNING_KEY_ID" \
  --max-request-bytes 2000000 \
  --rate-limit-per-minute 120 \
  --allowlist-cidrs "10.0.0.0/8,192.168.0.0/16"
```

## Database Migrations

- Apply/init migrations:
  - `formalfinance db-migrate --db-path /data/runs.sqlite3`
- Check migration status:
  - `formalfinance db-status --db-path /data/runs.sqlite3`

Migration strategy:

- SQLite is default for single-node deployments and pilot scale.
- For managed DB migration, keep API contract stable and migrate `runs` + `schema_migrations` first.
- Preserve `run_id` and `created_at` values during migration for audit continuity.

## Monitoring And Alerting

Poll and alert on:

- `GET /v1/healthz` availability
- `GET /v1/metrics`:
  - `total_runs` growth
  - latency p95/p99
  - status error/risk rates

Minimum alerts:

- health endpoint unavailable for 5 minutes
- p95 latency exceeds 5s for 15 minutes
- repeated 429 rate-limit responses

## Backup

- Snapshot SQLite file daily at minimum.
- Retain at least 30 days for incident replay.
- Encrypt backups at rest and in transit.

## Security Checklist

- Set `FORMALFINANCE_API_KEYS` (or reverse proxy auth).
- Set `FORMALFINANCE_CERT_SIGNING_SECRET` when signed certificates are required.
- Restrict ingress with `--allowlist-cidrs`.
- Configure `--max-request-bytes` and `--rate-limit-per-minute`.
- Keep LLM disabled by default unless needed.
