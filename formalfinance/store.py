from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sqlite3
import uuid


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    created_at: str
    endpoint: str
    tenant_id: str | None
    profile: str | None
    status: str
    error_count: int | None
    warning_count: int | None
    input_digest: str | None
    latency_ms: int
    request_bytes: int
    response_bytes: int
    metadata_json: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "endpoint": self.endpoint,
            "tenant_id": self.tenant_id,
            "profile": self.profile,
            "status": self.status,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "input_digest": self.input_digest,
            "latency_ms": self.latency_ms,
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
            "metadata_json": self.metadata_json,
        }


class RunStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def migrate(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["version"])
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }

            if 1 not in applied:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        endpoint TEXT NOT NULL,
                        tenant_id TEXT,
                        profile TEXT,
                        status TEXT NOT NULL,
                        error_count INTEGER,
                        warning_count INTEGER,
                        input_digest TEXT,
                        latency_ms INTEGER NOT NULL,
                        request_bytes INTEGER NOT NULL,
                        response_bytes INTEGER NOT NULL,
                        metadata_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs(tenant_id)"
                )
                conn.execute(
                    """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES(?, ?, ?)
                    """,
                    (1, "initial_runs_table", _utc_now()),
                )

            if 2 not in applied:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runs_endpoint ON runs(endpoint)"
                )
                conn.execute(
                    """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES(?, ?, ?)
                    """,
                    (2, "runs_endpoint_index", _utc_now()),
                )

            if 3 not in applied:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS support_events (
                        event_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        level TEXT NOT NULL,
                        message TEXT NOT NULL,
                        metadata_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES(?, ?, ?)
                    """,
                    (3, "support_events_table", _utc_now()),
                )

            # Backward compatibility if DB was created before migration tracking.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    tenant_id TEXT,
                    profile TEXT,
                    status TEXT NOT NULL,
                    error_count INTEGER,
                    warning_count INTEGER,
                    input_digest TEXT,
                    latency_ms INTEGER NOT NULL,
                    request_bytes INTEGER NOT NULL,
                    response_bytes INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs(tenant_id)"
            )
            conn.commit()

    def migration_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
        return {
            "schema_version": "formalfinance.db_migrations.v0",
            "db_path": str(self.db_path),
            "applied_migrations": [dict(row) for row in rows],
            "latest_version": max([int(row["version"]) for row in rows], default=0),
        }

    def next_run_id(self) -> str:
        return str(uuid.uuid4())

    def log_run(
        self,
        *,
        endpoint: str,
        tenant_id: str | None,
        profile: str | None,
        status: str,
        error_count: int | None,
        warning_count: int | None,
        input_digest: str | None,
        latency_ms: int,
        request_bytes: int,
        response_bytes: int,
        metadata_json: str = "{}",
        run_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        actual_run_id = run_id or self.next_run_id()
        created = created_at or _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, created_at, endpoint, tenant_id, profile, status,
                    error_count, warning_count, input_digest, latency_ms,
                    request_bytes, response_bytes, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actual_run_id,
                    created,
                    endpoint,
                    tenant_id,
                    profile,
                    status,
                    error_count,
                    warning_count,
                    input_digest,
                    int(latency_ms),
                    int(request_bytes),
                    int(response_bytes),
                    metadata_json,
                ),
            )
            conn.commit()
        return actual_run_id

    def list_runs(self, limit: int = 100, tenant_id: str | None = None) -> list[RunRecord]:
        limit = max(1, min(int(limit), 1000))
        sql = (
            "SELECT run_id, created_at, endpoint, tenant_id, profile, status, "
            "error_count, warning_count, input_digest, latency_ms, request_bytes, response_bytes, metadata_json "
            "FROM runs "
        )
        params: list[Any] = []
        if tenant_id:
            sql += "WHERE tenant_id = ? "
            params.append(tenant_id)
        sql += "ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [RunRecord(**dict(row)) for row in rows]

    def metrics(self) -> dict[str, Any]:
        with self._connect() as conn:
            total_runs = int(conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"])
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM runs GROUP BY status"
            ).fetchall()
            endpoint_rows = conn.execute(
                "SELECT endpoint, COUNT(*) AS c FROM runs GROUP BY endpoint ORDER BY c DESC"
            ).fetchall()
            latencies = [
                int(row["latency_ms"])
                for row in conn.execute(
                    "SELECT latency_ms FROM runs ORDER BY created_at DESC LIMIT 5000"
                ).fetchall()
            ]
        status_counts = {str(row["status"]): int(row["c"]) for row in status_rows}
        endpoint_counts = {str(row["endpoint"]): int(row["c"]) for row in endpoint_rows}
        latencies_sorted = sorted(latencies)

        def percentile(values: list[int], p: float) -> float:
            if not values:
                return 0.0
            idx = int(round((len(values) - 1) * p))
            return float(values[max(0, min(idx, len(values) - 1))])

        return {
            "schema_version": "formalfinance.service_metrics.v0",
            "total_runs": total_runs,
            "status_counts": status_counts,
            "endpoint_counts": endpoint_counts,
            "latency_ms": {
                "p50": percentile(latencies_sorted, 0.50),
                "p95": percentile(latencies_sorted, 0.95),
                "p99": percentile(latencies_sorted, 0.99),
            },
        }
