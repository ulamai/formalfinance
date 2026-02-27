# Service Expectations

## Scope

FormalFinance validates SEC filing conformance checks and emits audit artifacts.
It does not provide investment advice.

## Service Objectives (Target)

- API availability target: 99.5% monthly (single-region deployment)
- P95 API latency target:
  - `POST /v1/validate`: under 5 seconds for typical filings
  - `POST /v1/certify`: under 6 seconds for typical filings

## Support Targets

- P1 (service down): first response within 1 hour
- P2 (major degradation): first response within 4 hours
- P3 (normal issues): first response within 1 business day

## Change Management

- API changes are versioned under `/v1`.
- Schema changes are tracked in `schema_migrations`.
- Rule changes should include updated rulebook metadata and release notes.

## Data Retention (Default Policy)

- Run metadata retention: configurable by tenant policy.
- Evidence packs and certificates: tenant-controlled retention policy.

## Security Responsibilities

- Operator: deployment hardening, key management, backup, monitoring.
- User: input data quality, access control to generated artifacts.
