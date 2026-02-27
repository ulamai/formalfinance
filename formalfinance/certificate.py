from __future__ import annotations

from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
import hmac
import json

from . import __version__
from .engine import ValidationResult


def _stable_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _unsigned_certificate(certificate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in certificate.items() if key != "signature"}


def _sign_hs256(payload_bytes: bytes, signing_secret: str) -> str:
    raw = hmac.new(signing_secret.encode("utf-8"), payload_bytes, sha256).digest()
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _default_key_id(signing_secret: str) -> str:
    digest = sha256(signing_secret.encode("utf-8")).hexdigest()[:16]
    return f"hmac-sha256:{digest}"


def sign_certificate(
    certificate: dict[str, Any],
    *,
    signing_secret: str,
    key_id: str | None = None,
) -> dict[str, Any]:
    if not signing_secret:
        raise ValueError("Certificate signing requires a non-empty signing secret.")
    unsigned = _unsigned_certificate(certificate)
    payload_bytes = _stable_json_bytes(unsigned)
    payload_digest = sha256(payload_bytes).hexdigest()
    signature_value = _sign_hs256(payload_bytes, signing_secret)
    signed = dict(unsigned)
    signed["signature"] = {
        "schema_version": "formalfinance.certificate_signature.v0",
        "algorithm": "HS256",
        "key_id": key_id or _default_key_id(signing_secret),
        "payload_digest": payload_digest,
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "value": signature_value,
    }
    return signed


def verify_certificate(
    certificate: dict[str, Any],
    *,
    signing_secret: str | None = None,
    require_signature: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def _check(check_id: str, passed: bool, message: str) -> None:
        checks.append({"check_id": check_id, "passed": bool(passed), "message": message})

    _check(
        "certificate_schema",
        str(certificate.get("schema_version") or "") == "formalfinance.certificate.v0",
        "Certificate schema version is recognized.",
    )
    _check("certificate_verdict", str(certificate.get("verdict") or "") == "clean", "Certificate verdict is clean.")
    _check("input_digest_present", bool(str(certificate.get("input_digest") or "")), "Input digest is present.")
    _check("rules_digest_present", bool(str(certificate.get("rules_digest") or "")), "Rules digest is present.")

    signature = certificate.get("signature")
    unsigned = _unsigned_certificate(certificate)
    payload_bytes = _stable_json_bytes(unsigned)
    payload_digest = sha256(payload_bytes).hexdigest()

    if not isinstance(signature, dict):
        if require_signature:
            _check(
                "signature_present",
                False,
                "Required signature block is missing from certificate.",
            )
        else:
            _check("signature_optional", True, "Certificate is unsigned and signature is optional.")
    else:
        _check(
            "signature_schema",
            str(signature.get("schema_version") or "") == "formalfinance.certificate_signature.v0",
            "Signature schema version is recognized.",
        )
        _check(
            "signature_algorithm",
            str(signature.get("algorithm") or "") == "HS256",
            "Signature algorithm is supported (HS256).",
        )
        _check(
            "signature_payload_digest",
            hmac.compare_digest(str(signature.get("payload_digest") or ""), payload_digest),
            "Signature payload digest matches certificate payload digest.",
        )
        if not signing_secret:
            _check(
                "signature_secret_provided",
                False,
                "Certificate contains a signature but no signing secret was provided for verification.",
            )
        else:
            expected_value = _sign_hs256(payload_bytes, signing_secret)
            _check(
                "signature_value_match",
                hmac.compare_digest(expected_value, str(signature.get("value") or "")),
                "Signature value matches certificate payload and signing secret.",
            )

    verified = all(item["passed"] for item in checks)
    return {
        "schema_version": "formalfinance.certificate_verification.v0",
        "verified": verified,
        "certificate_profile": certificate.get("profile"),
        "certificate_tool_version": certificate.get("tool_version"),
        "checks": checks,
    }


def issue_certificate(
    profile: str,
    result: ValidationResult,
    *,
    signing_secret: str | None = None,
    key_id: str | None = None,
) -> dict[str, Any]:
    if result.status != "clean":
        raise ValueError("Cannot issue certificate when validation status is not clean.")

    rules_digest = sha256(",".join(result.executed_rules).encode("utf-8")).hexdigest()
    certificate: dict[str, Any] = {
        "schema_version": "formalfinance.certificate.v0",
        "tool_version": __version__,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "input_digest": result.input_digest,
        "rules_digest": rules_digest,
        "verdict": "clean",
    }
    if signing_secret:
        return sign_certificate(certificate, signing_secret=signing_secret, key_id=key_id)
    return certificate
