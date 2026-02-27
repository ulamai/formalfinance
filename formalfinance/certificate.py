from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from . import __version__
from .engine import ValidationResult


def issue_certificate(profile: str, result: ValidationResult) -> dict:
    if result.status != "clean":
        raise ValueError("Cannot issue certificate when validation status is not clean.")

    rules_digest = sha256(",".join(result.executed_rules).encode("utf-8")).hexdigest()
    return {
        "schema_version": "formalfinance.certificate.v0",
        "tool_version": __version__,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "input_digest": result.input_digest,
        "rules_digest": rules_digest,
        "verdict": "clean",
    }
