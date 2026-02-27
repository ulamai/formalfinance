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
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
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
