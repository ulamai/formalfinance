from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
import json
import os
import time

from . import __version__
from .baseline_compare import compare_with_baseline
from .certificate import issue_certificate
from .evidence import run_validation
from .models import Filing
from .pilot_readiness import build_readiness_report
from .profiles import list_profiles, normalize_profile_name
from .rulebook import build_global_rulebook, build_rulebook
from .store import RunStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_api_keys(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {token.strip() for token in raw.split(",") if token.strip()}


@dataclass(frozen=True)
class ServiceConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    db_path: str = ".formalfinance/runs.sqlite3"
    api_keys: tuple[str, ...] = ()

    @classmethod
    def from_args(
        cls,
        *,
        host: str,
        port: int,
        db_path: str,
        api_keys_raw: str | None,
    ) -> "ServiceConfig":
        env_keys = _split_api_keys(os.getenv("FORMALFINANCE_API_KEYS"))
        arg_keys = _split_api_keys(api_keys_raw)
        merged = sorted(env_keys | arg_keys)
        return cls(host=host, port=port, db_path=db_path, api_keys=tuple(merged))


class FormalFinanceService:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.store = RunStore(config.db_path)

    def _is_authorized(self, headers: dict[str, str]) -> bool:
        if not self.config.api_keys:
            return True
        x_api_key = (headers.get("x-api-key") or "").strip()
        if x_api_key and x_api_key in self.config.api_keys:
            return True
        auth = (headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            if token in self.config.api_keys:
                return True
        return False

    def _log_run(
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
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self.store.log_run(
            endpoint=endpoint,
            tenant_id=tenant_id,
            profile=profile,
            status=status,
            error_count=error_count,
            warning_count=warning_count,
            input_digest=input_digest,
            latency_ms=latency_ms,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            metadata_json=json.dumps(metadata or {}, sort_keys=True),
        )

    def handle(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, list[str]],
        headers: dict[str, str],
        payload: Any,
        request_bytes: int,
    ) -> tuple[int, dict[str, Any]]:
        started = time.perf_counter()
        tenant_id = None
        if isinstance(payload, dict):
            tenant_id = str(payload.get("tenant_id") or "").strip() or None

        if path == "/v1/healthz" and method == "GET":
            return 200, {
                "status": "ok",
                "service": "formalfinance",
                "version": __version__,
                "timestamp": _utc_now(),
            }

        if not self._is_authorized(headers):
            return 401, {"error": "unauthorized", "message": "Missing or invalid API key."}

        if path == "/v1/profiles" and method == "GET":
            profiles = list_profiles()
            return 200, {
                "profiles": [
                    {
                        "name": p.name,
                        "description": p.description,
                        "rule_count": len(p.rules),
                    }
                    for p in profiles
                ]
            }

        if path == "/v1/rulebook" and method == "GET":
            profile = (query.get("profile", ["all"])[0] or "all").strip()
            if profile == "all":
                return 200, build_global_rulebook()
            normalized = normalize_profile_name(profile)
            return 200, build_rulebook(normalized)

        if path == "/v1/runs" and method == "GET":
            limit = int((query.get("limit", ["100"])[0] or "100").strip())
            requested_tenant = (query.get("tenant_id", [""])[0] or "").strip() or None
            records = self.store.list_runs(limit=limit, tenant_id=requested_tenant)
            return 200, {"runs": [record.as_dict() for record in records]}

        if path == "/v1/pilot-readiness" and method in {"GET", "POST"}:
            params = payload if isinstance(payload, dict) else {}
            report = build_readiness_report(
                min_rules=int(params.get("min_rules", 30)),
                max_rules=int(params.get("max_rules", 50)),
                min_filings=int(params.get("min_filings", 50)),
                max_filings=int(params.get("max_filings", 100)),
                user_agent=str(params.get("user_agent", "")).strip() or None,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            response_bytes = len(json.dumps(report))
            run_id = self._log_run(
                endpoint=path,
                tenant_id=tenant_id,
                profile=None,
                status="ready" if report["summary"]["ready"] else "not_ready",
                error_count=report["summary"]["failed_checks"],
                warning_count=report["summary"]["warning_checks"],
                input_digest=None,
                latency_ms=latency_ms,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                metadata={"total_checks": report["summary"]["total_checks"]},
            )
            return 200, {"run_id": run_id, "report": report}

        if path == "/v1/compare-baseline" and method == "POST":
            if not isinstance(payload, dict):
                return 400, {"error": "invalid_request", "message": "JSON object payload is required."}
            formal_report = payload.get("formal_report")
            baseline_report = payload.get("baseline_report")
            if not isinstance(formal_report, dict):
                return 400, {"error": "invalid_request", "message": "`formal_report` object is required."}
            comparison = compare_with_baseline(formal_report, baseline_report).as_dict()
            latency_ms = int((time.perf_counter() - started) * 1000)
            response_bytes = len(json.dumps(comparison))
            run_id = self._log_run(
                endpoint=path,
                tenant_id=tenant_id,
                profile=None,
                status="agreement" if comparison["metrics"]["status_agreement"] else "disagreement",
                error_count=len(comparison["formal_only_error_ids"]),
                warning_count=len(comparison["baseline_only_error_ids"]),
                input_digest=None,
                latency_ms=latency_ms,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                metadata={"meets_95pct_target": comparison["metrics"]["meets_95pct_target"]},
            )
            return 200, {"run_id": run_id, "comparison": comparison}

        if path in {"/v1/validate", "/v1/certify"} and method == "POST":
            if not isinstance(payload, dict):
                return 400, {"error": "invalid_request", "message": "JSON object payload is required."}
            filing_obj = payload.get("filing")
            profile = normalize_profile_name(str(payload.get("profile") or "ixbrl-gating"))
            if not isinstance(filing_obj, dict):
                return 400, {"error": "invalid_request", "message": "`filing` object is required."}
            try:
                filing = Filing.from_dict(filing_obj)
            except Exception as exc:
                return 400, {"error": "invalid_filing", "message": str(exc)}
            report, result = run_validation(filing, profile, trace_path=None)
            response: dict[str, Any] = {"report": report}
            if path == "/v1/certify":
                if report["status"] == "clean":
                    response["certificate"] = issue_certificate(profile, result)
                else:
                    response["certificate"] = None
                    response["certificate_status"] = "not_issued"
            latency_ms = int((time.perf_counter() - started) * 1000)
            response_bytes = len(json.dumps(response))
            run_id = self._log_run(
                endpoint=path,
                tenant_id=tenant_id,
                profile=profile,
                status=report["status"],
                error_count=report["summary"]["error_count"],
                warning_count=report["summary"]["warning_count"],
                input_digest=report["input_digest"],
                latency_ms=latency_ms,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                metadata={"risk_score": report["summary"]["risk_score"]},
            )
            return 200, {"run_id": run_id, **response}

        return 404, {"error": "not_found", "message": f"No route for {method} {path}."}


class _Handler(BaseHTTPRequestHandler):
    service: FormalFinanceService

    server_version = f"FormalFinance/{__version__}"

    def _headers_dict(self) -> dict[str, str]:
        return {key.lower(): value for key, value in self.headers.items()}

    def _read_json_body(self) -> tuple[Any, int, str | None]:
        raw_len = int(self.headers.get("Content-Length", "0") or "0")
        if raw_len <= 0:
            return None, 0, None
        raw = self.rfile.read(raw_len)
        if not raw:
            return None, 0, None
        try:
            return json.loads(raw.decode("utf-8")), len(raw), None
        except json.JSONDecodeError as exc:
            return None, len(raw), str(exc)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self) -> None:
        method = self.command.upper()
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if method in {"POST", "PUT", "PATCH"}:
            payload, request_bytes, decode_error = self._read_json_body()
            if decode_error:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_json", "message": decode_error},
                )
                return
        else:
            payload = None
            request_bytes = 0

        try:
            status, response = self.service.handle(
                method=method,
                path=path,
                query=query,
                headers=self._headers_dict(),
                payload=payload,
                request_bytes=request_bytes,
            )
            self._send_json(status, response)
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "internal_error", "message": str(exc)},
            )

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def log_message(self, format: str, *args: Any) -> None:
        del format
        del args


def create_server(config: ServiceConfig) -> ThreadingHTTPServer:
    service = FormalFinanceService(config=config)
    handler_cls = type("FormalFinanceHandler", (_Handler,), {})
    handler_cls.service = service
    return ThreadingHTTPServer((config.host, int(config.port)), handler_cls)


def run_server(config: ServiceConfig) -> None:
    server = create_server(config)
    try:
        server.serve_forever()
    finally:
        server.server_close()
