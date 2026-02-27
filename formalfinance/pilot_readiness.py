from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os

from .profiles import get_profile


@dataclass(frozen=True)
class ReadinessCheck:
    check_id: str
    status: str
    message: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


def _pass(check_id: str, message: str, **details: Any) -> ReadinessCheck:
    return ReadinessCheck(check_id=check_id, status="pass", message=message, details=details)


def _fail(check_id: str, message: str, **details: Any) -> ReadinessCheck:
    return ReadinessCheck(check_id=check_id, status="fail", message=message, details=details)


def _warn(check_id: str, message: str, **details: Any) -> ReadinessCheck:
    return ReadinessCheck(check_id=check_id, status="warn", message=message, details=details)


def build_readiness_report(
    min_rules: int = 30,
    max_rules: int = 50,
    min_filings: int = 50,
    max_filings: int = 100,
    user_agent: str | None = None,
) -> dict[str, Any]:
    checks: list[ReadinessCheck] = []
    rule_ids = [rule.rule_id for rule in get_profile("ixbrl-gating")]
    total_rules = len(rule_ids)

    if min_rules <= total_rules <= max_rules:
        checks.append(
            _pass(
                "rule_count_window",
                "iXBRL gating profile rule count is inside pilot target window.",
                total_rules=total_rules,
                target_min=min_rules,
                target_max=max_rules,
            )
        )
    else:
        checks.append(
            _fail(
                "rule_count_window",
                "iXBRL gating profile rule count is outside pilot target window.",
                total_rules=total_rules,
                target_min=min_rules,
                target_max=max_rules,
            )
        )

    ixbrl_rules = [r for r in rule_ids if r.startswith("ixbrl.")]
    taxonomy_rules = [r for r in rule_ids if r.startswith("taxonomy.")]
    if ixbrl_rules and taxonomy_rules:
        checks.append(
            _pass(
                "scope_rule_coverage",
                "Rulepack includes both iXBRL structural and taxonomy validation checks.",
                ixbrl_rule_count=len(ixbrl_rules),
                taxonomy_rule_count=len(taxonomy_rules),
            )
        )
    else:
        checks.append(
            _fail(
                "scope_rule_coverage",
                "Rulepack is missing iXBRL or taxonomy coverage.",
                ixbrl_rule_count=len(ixbrl_rules),
                taxonomy_rule_count=len(taxonomy_rules),
            )
        )

    if min_filings <= max_filings and min_filings > 0:
        checks.append(
            _pass(
                "sample_size_target",
                "Pilot target sample-size range is valid.",
                target_min_filings=min_filings,
                target_max_filings=max_filings,
            )
        )
    else:
        checks.append(
            _fail(
                "sample_size_target",
                "Pilot sample-size range is invalid.",
                target_min_filings=min_filings,
                target_max_filings=max_filings,
            )
        )

    ua = (user_agent or os.getenv("FORMALFINANCE_USER_AGENT", "")).strip()
    if ua:
        checks.append(
            _pass(
                "sec_user_agent",
                "SEC-compliant User-Agent is configured for ingestion/discovery commands.",
                user_agent=ua,
            )
        )
    else:
        checks.append(
            _warn(
                "sec_user_agent",
                "No SEC User-Agent configured; network ingestion commands will fail until set.",
                env_var="FORMALFINANCE_USER_AGENT",
            )
        )

    fixtures = [
        Path("examples/filing_clean.json"),
        Path("examples/filing_risky.json"),
    ]
    missing = [str(path) for path in fixtures if not path.exists()]
    if missing:
        checks.append(
            _fail(
                "fixtures_present",
                "Required example fixtures are missing.",
                missing=missing,
            )
        )
    else:
        checks.append(
            _pass(
                "fixtures_present",
                "Example fixtures for clean and risky conformance scenarios are present.",
                files=[str(path) for path in fixtures],
            )
        )

    try:
        from .baseline_compare import compare_with_baseline  # noqa: F401

        checks.append(
            _pass(
                "baseline_comparison_tooling",
                "Baseline discrepancy comparison module is available.",
                module="formalfinance.baseline_compare",
            )
        )
    except Exception as exc:
        checks.append(
            _fail(
                "baseline_comparison_tooling",
                "Baseline discrepancy comparison module is unavailable.",
                error=str(exc),
            )
        )

    failed = [check for check in checks if check.status == "fail"]
    warnings = [check for check in checks if check.status == "warn"]
    ready = not failed
    next_actions: list[str] = []
    if failed:
        next_actions.extend([f"{check.check_id}: {check.message}" for check in failed])
    if warnings:
        next_actions.extend([f"{check.check_id}: {check.message}" for check in warnings])

    return {
        "schema_version": "formalfinance.pilot_readiness.v0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": {
            "sample_size_range": [min_filings, max_filings],
            "rule_count_range": [min_rules, max_rules],
        },
        "summary": {
            "ready": ready,
            "failed_checks": len(failed),
            "warning_checks": len(warnings),
            "total_checks": len(checks),
        },
        "checks": [check.as_dict() for check in checks],
        "next_actions": next_actions,
        "rule_ids": sorted(rule_ids),
    }
