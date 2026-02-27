from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
import hmac
import json
import subprocess

from .certificate import verify_certificate
from .engine import ValidationResult
from .models import Fact, Filing
from .rules import BalanceSheetEquationRule, _best_fact_by_context, _rounding_tolerance


def _stable_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _decimal_from_fact_value(fact: Fact) -> Decimal | None:
    if isinstance(fact.value, bool):
        return None
    if isinstance(fact.value, int):
        return Decimal(fact.value)
    if isinstance(fact.value, float):
        return Decimal(str(fact.value))
    if isinstance(fact.value, str):
        txt = fact.value.replace(",", "").strip()
        if not txt:
            return None
        try:
            return Decimal(txt)
        except InvalidOperation:
            return None
    return None


def _decimal_places(value: Decimal) -> int:
    normalized = value.normalize()
    exponent = int(normalized.as_tuple().exponent)
    return max(0, -exponent)


def _to_scaled_int(value: Decimal, scale: int) -> int:
    scaled = value * Decimal(scale)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(f"Value '{value}' cannot be represented exactly at scale {scale}.")
    return int(integral)


def _balance_sheet_claims(filing: Filing) -> list[dict[str, Any]]:
    rule = BalanceSheetEquationRule()
    assets = _best_fact_by_context(filing.facts, set(rule.assets_concepts))
    liabilities = _best_fact_by_context(filing.facts, set(rule.liabilities_concepts))
    equity = _best_fact_by_context(filing.facts, set(rule.equity_concepts))
    claims: list[dict[str, Any]] = []

    for context_id in sorted(set(assets) & set(liabilities) & set(equity)):
        assets_fact = assets[context_id]
        liabilities_fact = liabilities[context_id]
        equity_fact = equity[context_id]
        assets_val = _decimal_from_fact_value(assets_fact)
        liabilities_val = _decimal_from_fact_value(liabilities_fact)
        equity_val = _decimal_from_fact_value(equity_fact)
        if assets_val is None or liabilities_val is None or equity_val is None:
            continue

        tolerance = (
            Decimal(str(_rounding_tolerance(assets_fact.decimals)))
            + Decimal(str(_rounding_tolerance(liabilities_fact.decimals)))
            + Decimal(str(_rounding_tolerance(equity_fact.decimals)))
        )
        difference = abs(assets_val - (liabilities_val + equity_val))
        all_values = [assets_val, liabilities_val, equity_val, tolerance, difference]
        scale_power = max(_decimal_places(item) for item in all_values)
        scale = 10 ** scale_power if scale_power > 0 else 1

        claims.append(
            {
                "claim_id": f"acct.balance_sheet_equation:{context_id}",
                "rule_id": "acct.balance_sheet_equation",
                "context_id": context_id,
                "fact_ids": [assets_fact.id, liabilities_fact.id, equity_fact.id],
                "assets": str(assets_val),
                "liabilities": str(liabilities_val),
                "equity": str(equity_val),
                "difference": str(difference),
                "tolerance": str(tolerance),
                "within_tolerance": bool(difference <= tolerance),
                "lean": {
                    "scale": scale,
                    "assets_scaled": _to_scaled_int(assets_val, scale),
                    "liabilities_scaled": _to_scaled_int(liabilities_val, scale),
                    "equity_scaled": _to_scaled_int(equity_val, scale),
                    "difference_scaled": _to_scaled_int(difference, scale),
                    "tolerance_scaled": _to_scaled_int(tolerance, scale),
                },
            }
        )
    return claims


def build_proof_bundle(
    *,
    filing: Filing,
    profile: str,
    report: dict[str, Any],
    result: ValidationResult,
    certificate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    proof: dict[str, Any] = {
        "schema_version": "formalfinance.proof_bundle.v0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "status": report.get("status"),
        "input_digest": result.input_digest,
        "report_digest": sha256(_stable_json_bytes(report)).hexdigest(),
        "report_summary": dict(report.get("summary") or {}),
        "executed_rules": list(result.executed_rules),
        "findings_digest": sha256(
            _stable_json_bytes({"findings": report.get("findings", [])})
        ).hexdigest(),
        "arithmetic_claims": _balance_sheet_claims(filing),
    }
    if certificate is not None:
        proof["certificate_digest"] = sha256(_stable_json_bytes(certificate)).hexdigest()
    return proof


def _build_lean_script(proof: dict[str, Any]) -> str:
    claims = [row for row in proof.get("arithmetic_claims", []) if isinstance(row, dict)]
    lines: list[str] = []
    lines.append("/- Auto-generated by FormalFinance proof replay. -/")
    lines.append("def absDiff (a b c : Int) : Nat := Int.natAbs (a - (b + c))")
    lines.append("")
    if not claims:
        lines.append("theorem replay_has_no_claims : True := by trivial")
        return "\n".join(lines) + "\n"
    for idx, claim in enumerate(claims, start=1):
        lean = claim.get("lean", {}) if isinstance(claim.get("lean"), dict) else {}
        assets = int(lean.get("assets_scaled", 0))
        liabilities = int(lean.get("liabilities_scaled", 0))
        equity = int(lean.get("equity_scaled", 0))
        tolerance = int(lean.get("tolerance_scaled", 0))
        lines.append(
            f"theorem replay_claim_{idx} : absDiff {assets} {liabilities} {equity} <= {tolerance} := by decide"
        )
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class LeanReplayResult:
    status: str
    message: str
    script: str
    command: list[str]
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "command": self.command,
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "script": self.script,
        }


def run_lean_replay(
    proof: dict[str, Any],
    *,
    lean_bin: str = "lean",
    timeout_seconds: int = 20,
) -> LeanReplayResult:
    script = _build_lean_script(proof)
    with NamedTemporaryFile("w", suffix=".lean", encoding="utf-8", delete=False) as fp:
        fp.write(script)
        temp_path = Path(fp.name)
    command = [lean_bin, str(temp_path)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except FileNotFoundError:
        return LeanReplayResult(
            status="unavailable",
            message=f"Lean binary '{lean_bin}' was not found on PATH.",
            script=script,
            command=command,
        )
    except subprocess.TimeoutExpired:
        return LeanReplayResult(
            status="error",
            message=f"Lean replay timed out after {timeout_seconds}s.",
            script=script,
            command=command,
        )
    finally:
        temp_path.unlink(missing_ok=True)

    if completed.returncode == 0:
        return LeanReplayResult(
            status="ok",
            message="Lean replay succeeded.",
            script=script,
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout[-4000:],
            stderr=completed.stderr[-4000:],
        )
    return LeanReplayResult(
        status="error",
        message="Lean replay failed.",
        script=script,
        command=command,
        return_code=completed.returncode,
        stdout=completed.stdout[-4000:],
        stderr=completed.stderr[-4000:],
    )


def replay_proof_bundle(
    proof: dict[str, Any],
    *,
    report: dict[str, Any] | None = None,
    certificate: dict[str, Any] | None = None,
    signing_secret: str | None = None,
    require_certificate_signature: bool = False,
    run_lean: bool = False,
    lean_bin: str = "lean",
    lean_timeout_seconds: int = 20,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def _check(check_id: str, passed: bool, message: str, details: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"check_id": check_id, "passed": bool(passed), "message": message}
        if details:
            payload["details"] = details
        checks.append(payload)

    _check(
        "proof_schema",
        str(proof.get("schema_version") or "") == "formalfinance.proof_bundle.v0",
        "Proof schema version is recognized.",
    )
    _check("proof_input_digest", bool(str(proof.get("input_digest") or "")), "Proof input digest is present.")
    _check("proof_report_digest", bool(str(proof.get("report_digest") or "")), "Proof report digest is present.")

    if report is not None:
        computed_report_digest = sha256(_stable_json_bytes(report)).hexdigest()
        _check(
            "report_digest_match",
            computed_report_digest == str(proof.get("report_digest") or ""),
            "Report digest matches proof payload.",
        )
        _check(
            "report_input_digest_match",
            str(report.get("input_digest") or "") == str(proof.get("input_digest") or ""),
            "Report input digest matches proof input digest.",
        )
        proof_rules = list(proof.get("executed_rules") or [])
        report_rules = list(report.get("executed_rules") or [])
        _check(
            "executed_rules_match",
            proof_rules == report_rules,
            "Executed rule sequence matches between report and proof.",
            details={"proof_rules": len(proof_rules), "report_rules": len(report_rules)},
        )

    claims = [row for row in proof.get("arithmetic_claims", []) if isinstance(row, dict)]
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "unknown")
        try:
            assets = Decimal(str(claim.get("assets")))
            liabilities = Decimal(str(claim.get("liabilities")))
            equity = Decimal(str(claim.get("equity")))
            tolerance = Decimal(str(claim.get("tolerance")))
            expected_within = bool(claim.get("within_tolerance"))
            recomputed = abs(assets - (liabilities + equity)) <= tolerance
            _check(
                f"claim:{claim_id}",
                recomputed == expected_within,
                "Arithmetic claim replayed with expected result.",
                details={"expected": expected_within, "recomputed": recomputed},
            )
        except Exception as exc:
            _check(
                f"claim:{claim_id}",
                False,
                f"Arithmetic claim is invalid: {exc}",
            )

    if certificate is not None:
        expected_digest = str(proof.get("certificate_digest") or "")
        if expected_digest:
            actual_digest = sha256(_stable_json_bytes(certificate)).hexdigest()
            _check(
                "certificate_digest_match",
                hmac.compare_digest(expected_digest, actual_digest),
                "Certificate digest matches proof payload.",
            )
        verification = verify_certificate(
            certificate,
            signing_secret=signing_secret,
            require_signature=require_certificate_signature,
        )
        _check(
            "certificate_signature_verify",
            bool(verification.get("verified")),
            "Certificate signature verification succeeded.",
            details={"verification": verification},
        )
    elif require_certificate_signature:
        _check(
            "certificate_required",
            False,
            "Certificate signature was required but no certificate payload was provided.",
        )

    lean_result = None
    if run_lean:
        lean = run_lean_replay(proof, lean_bin=lean_bin, timeout_seconds=lean_timeout_seconds)
        lean_result = lean.as_dict()
        _check(
            "lean_replay",
            lean.status == "ok",
            lean.message,
            details={"return_code": lean.return_code, "command": lean.command},
        )

    verified = all(item["passed"] for item in checks)
    return {
        "schema_version": "formalfinance.proof_replay.v0",
        "verified": verified,
        "checks": checks,
        "lean": lean_result,
    }
