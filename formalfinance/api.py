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
from .llm import LLMConfig, generate_advisory
from .models import Filing
from .pilot_readiness import build_readiness_report
from .proof import build_proof_bundle, replay_proof_bundle
from .profiles import list_profiles, normalize_profile_name
from .rulebook import build_global_rulebook, build_rulebook
from .sec_accession_ingest import ingest_accession_to_filing
from .security import CIDRAllowlist, InMemoryRateLimiter
from .store import RunStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_api_keys(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {token.strip() for token in raw.split(",") if token.strip()}


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _split_csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class ServiceConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    db_path: str = ".formalfinance/runs.sqlite3"
    api_keys: tuple[str, ...] = ()
    llm_default: LLMConfig = LLMConfig()
    max_request_bytes: int = 2_000_000
    rate_limit_per_minute: int = 120
    allowlist_cidrs: tuple[str, ...] = ()
    cert_signing_secret: str | None = None
    cert_signing_key_id: str | None = None

    @classmethod
    def from_args(
        cls,
        *,
        host: str,
        port: int,
        db_path: str,
        api_keys_raw: str | None,
        llm_enabled: bool | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_timeout_seconds: int | None = None,
        llm_max_findings: int | None = None,
        max_request_bytes: int | None = None,
        rate_limit_per_minute: int | None = None,
        allowlist_cidrs_raw: str | None = None,
        cert_signing_secret: str | None = None,
        cert_signing_key_id: str | None = None,
    ) -> "ServiceConfig":
        env_keys = _split_api_keys(os.getenv("FORMALFINANCE_API_KEYS"))
        arg_keys = _split_api_keys(api_keys_raw)
        merged = sorted(env_keys | arg_keys)
        env_llm = LLMConfig.from_env()
        merged_llm = LLMConfig(
            enabled=env_llm.enabled if llm_enabled is None else bool(llm_enabled),
            provider=(llm_provider or env_llm.provider or "none").strip().lower(),
            model=(llm_model or env_llm.model or None),
            base_url=(llm_base_url or env_llm.base_url or None),
            api_key=(llm_api_key or env_llm.api_key or None),
            timeout_seconds=max(3, int(llm_timeout_seconds or env_llm.timeout_seconds)),
            max_findings=max(1, int(llm_max_findings or env_llm.max_findings)),
        )
        allowlist = _split_csv(allowlist_cidrs_raw) or _split_csv(os.getenv("FORMALFINANCE_ALLOWLIST_CIDRS"))
        effective_signing_secret = (
            cert_signing_secret
            if cert_signing_secret is not None
            else os.getenv("FORMALFINANCE_CERT_SIGNING_SECRET")
        )
        effective_signing_key_id = (
            cert_signing_key_id
            if cert_signing_key_id is not None
            else os.getenv("FORMALFINANCE_CERT_SIGNING_KEY_ID")
        )
        return cls(
            host=host,
            port=port,
            db_path=db_path,
            api_keys=tuple(merged),
            llm_default=merged_llm,
            max_request_bytes=max(
                1024,
                _int_or_default(max_request_bytes, _int_or_default(os.getenv("FORMALFINANCE_MAX_REQUEST_BYTES"), 2_000_000)),
            ),
            rate_limit_per_minute=max(
                1,
                _int_or_default(rate_limit_per_minute, _int_or_default(os.getenv("FORMALFINANCE_RATE_LIMIT_PER_MINUTE"), 120)),
            ),
            allowlist_cidrs=tuple(allowlist),
            cert_signing_secret=(effective_signing_secret or "").strip() or None,
            cert_signing_key_id=(effective_signing_key_id or "").strip() or None,
        )


class FormalFinanceService:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.store = RunStore(config.db_path)
        self.rate_limiter = InMemoryRateLimiter(rate_per_minute=config.rate_limit_per_minute)
        self.allowlist = CIDRAllowlist(networks=config.allowlist_cidrs)

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

    def _rate_limit_key(self, headers: dict[str, str], remote_addr: str) -> str:
        x_api_key = (headers.get("x-api-key") or "").strip()
        if x_api_key:
            return f"key:{x_api_key}"
        auth = (headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return f"bearer:{auth.split(' ', 1)[1].strip()}"
        return f"ip:{remote_addr}"

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

    def _resolve_llm_config(self, payload: Any) -> LLMConfig:
        request_llm = None
        if isinstance(payload, dict):
            request_llm = payload.get("llm")
        return self.config.llm_default.with_overrides(request_llm)

    def _resolve_certificate_signing(self, payload: Any) -> tuple[str | None, str | None]:
        secret = self.config.cert_signing_secret
        key_id = self.config.cert_signing_key_id
        if not isinstance(payload, dict):
            return secret, key_id
        request_signing = payload.get("certificate_signing")
        if not isinstance(request_signing, dict):
            return secret, key_id
        if request_signing.get("enabled") is False:
            return None, None
        request_secret = str(request_signing.get("secret") or "").strip()
        request_key_id = str(request_signing.get("key_id") or "").strip()
        if request_secret:
            secret = request_secret
        if request_key_id:
            key_id = request_key_id
        return secret, key_id

    def handle(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, list[str]],
        headers: dict[str, str],
        payload: Any,
        request_bytes: int,
        remote_addr: str,
    ) -> tuple[int, dict[str, Any]]:
        started = time.perf_counter()
        tenant_id = None
        if isinstance(payload, dict):
            tenant_id = str(payload.get("tenant_id") or "").strip() or None

        if not self.allowlist.allows(remote_addr):
            return 403, {"error": "forbidden", "message": "Client IP is not in allowlist."}

        if not self.rate_limiter.allow(self._rate_limit_key(headers, remote_addr)):
            return 429, {"error": "rate_limited", "message": "Rate limit exceeded."}

        if path == "/v1/healthz" and method == "GET":
            return 200, {
                "status": "ok",
                "service": "formalfinance",
                "version": __version__,
                "timestamp": _utc_now(),
                "llm_default_enabled": self.config.llm_default.enabled,
                "llm_default_provider": self.config.llm_default.provider,
                "certificate_signing_enabled": bool(self.config.cert_signing_secret),
                "certificate_signing_key_id": self.config.cert_signing_key_id,
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

        if path == "/v1/metrics" and method == "GET":
            return 200, self.store.metrics()

        if path == "/v1/migrations" and method == "GET":
            return 200, self.store.migration_status()

        if path == "/v1/ingest-accession" and method == "POST":
            if not isinstance(payload, dict):
                return 400, {"error": "invalid_request", "message": "JSON object payload is required."}
            cik = payload.get("cik")
            accession = payload.get("accession")
            if not cik or not accession:
                return 400, {"error": "invalid_request", "message": "`cik` and `accession` are required."}
            user_agent = str(payload.get("user_agent") or os.getenv("FORMALFINANCE_USER_AGENT") or "").strip()
            if not user_agent:
                return 400, {"error": "invalid_request", "message": "SEC user_agent is required."}
            filing, ingest_meta = ingest_accession_to_filing(
                cik=str(cik),
                accession=str(accession),
                user_agent=user_agent,
                timeout_seconds=int(payload.get("timeout_seconds") or 30),
                include_companyfacts=bool(payload.get("include_companyfacts", True)),
                max_scan_docs=int(payload.get("max_scan_docs") or 25),
                max_doc_scan_bytes=int(payload.get("max_doc_scan_bytes") or 1_000_000),
            )
            response = {
                "filing": filing.canonical_object(),
                "ingestion_metadata": ingest_meta.as_dict(),
            }
            latency_ms = int((time.perf_counter() - started) * 1000)
            response_bytes = len(json.dumps(response))
            run_id = self._log_run(
                endpoint=path,
                tenant_id=tenant_id,
                profile=None,
                status="ok",
                error_count=0,
                warning_count=0,
                input_digest=filing.input_digest(),
                latency_ms=latency_ms,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                metadata={"cik": filing.cik, "accession": filing.accession},
            )
            return 200, {"run_id": run_id, **response}

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

        if path == "/v1/replay-proof" and method == "POST":
            if not isinstance(payload, dict):
                return 400, {"error": "invalid_request", "message": "JSON object payload is required."}
            proof = payload.get("proof")
            if not isinstance(proof, dict):
                return 400, {"error": "invalid_request", "message": "`proof` object is required."}
            report = payload.get("report")
            certificate = payload.get("certificate")
            if report is not None and not isinstance(report, dict):
                return 400, {"error": "invalid_request", "message": "`report` must be an object when provided."}
            if certificate is not None and not isinstance(certificate, dict):
                return 400, {"error": "invalid_request", "message": "`certificate` must be an object when provided."}
            signing_secret = str(payload.get("signing_secret") or "").strip() or self.config.cert_signing_secret
            lean = payload.get("lean")
            lean_cfg = lean if isinstance(lean, dict) else {}
            replay = replay_proof_bundle(
                proof,
                report=report,
                certificate=certificate,
                signing_secret=signing_secret,
                require_certificate_signature=bool(payload.get("require_certificate_signature", False)),
                run_lean=bool(lean_cfg.get("enabled", False)),
                lean_bin=str(lean_cfg.get("bin") or "lean"),
                lean_timeout_seconds=int(lean_cfg.get("timeout_seconds") or 20),
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            response_bytes = len(json.dumps(replay))
            run_id = self._log_run(
                endpoint=path,
                tenant_id=tenant_id,
                profile=str(proof.get("profile") or "") or None,
                status="verified" if replay["verified"] else "failed",
                error_count=0 if replay["verified"] else 1,
                warning_count=0,
                input_digest=str(proof.get("input_digest") or "") or None,
                latency_ms=latency_ms,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                metadata={"check_count": len(replay.get("checks", [])), "lean_enabled": bool(lean_cfg.get("enabled", False))},
            )
            return 200, {"run_id": run_id, "replay": replay}

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
            llm_config = self._resolve_llm_config(payload)
            advisory = generate_advisory(report, llm_config)
            response: dict[str, Any] = {"report": report}
            response["advisory"] = advisory
            include_proof = bool(payload.get("include_proof", False))
            certificate_payload = None
            if path == "/v1/certify":
                if report["status"] == "clean":
                    signing_secret, key_id = self._resolve_certificate_signing(payload)
                    certificate_payload = issue_certificate(
                        profile,
                        result,
                        signing_secret=signing_secret,
                        key_id=key_id,
                    )
                    response["certificate"] = certificate_payload
                else:
                    response["certificate"] = None
                    response["certificate_status"] = "not_issued"
            if include_proof:
                response["proof"] = build_proof_bundle(
                    filing=filing,
                    profile=profile,
                    report=report,
                    result=result,
                    certificate=certificate_payload,
                )
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
                metadata={
                    "risk_score": report["summary"]["risk_score"],
                    "llm_enabled": advisory.get("enabled"),
                    "llm_provider": advisory.get("provider"),
                    "llm_status": advisory.get("status"),
                    "include_proof": include_proof,
                    "certificate_signed": bool(isinstance(certificate_payload, dict) and certificate_payload.get("signature")),
                },
            )
            return 200, {"run_id": run_id, **response}

        return 404, {"error": "not_found", "message": f"No route for {method} {path}."}


class _Handler(BaseHTTPRequestHandler):
    service: FormalFinanceService

    server_version = f"FormalFinance/{__version__}"

    def _headers_dict(self) -> dict[str, str]:
        return {key.lower(): value for key, value in self.headers.items()}

    def _read_json_body(self, max_bytes: int) -> tuple[Any, int, str | None]:
        raw_len = int(self.headers.get("Content-Length", "0") or "0")
        if raw_len <= 0:
            return None, 0, None
        if raw_len > max_bytes:
            return None, 0, f"Request body too large ({raw_len} > {max_bytes})."
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
            payload, request_bytes, decode_error = self._read_json_body(
                max_bytes=int(self.service.config.max_request_bytes)
            )
            if decode_error:
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if decode_error.startswith("Request body too large") else HTTPStatus.BAD_REQUEST
                self._send_json(
                    status,
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
                remote_addr=self.client_address[0],
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
