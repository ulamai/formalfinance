from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json


def _normalize_id(value: Any) -> str:
    return str(value or "").strip()


def _extract_formal_ids(report: dict[str, Any]) -> tuple[set[str], set[str]]:
    error_ids: set[str] = set()
    warning_ids: set[str] = set()
    findings = report.get("findings", []) or []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        rule_id = _normalize_id(finding.get("rule_id"))
        severity = _normalize_id(finding.get("severity")).lower()
        if not rule_id:
            continue
        if severity == "error":
            error_ids.add(rule_id)
        elif severity == "warning":
            warning_ids.add(rule_id)
    return error_ids, warning_ids


def _extract_baseline_ids(payload: Any) -> tuple[set[str], set[str]]:
    error_ids: set[str] = set()
    warning_ids: set[str] = set()

    if isinstance(payload, list):
        findings = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("findings"), list):
            findings = payload.get("findings", [])
        else:
            findings = []
            for key, severity in (("errors", "error"), ("warnings", "warning")):
                rows = payload.get(key)
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            findings.append({**row, "severity": severity})
                        else:
                            findings.append({"id": row, "severity": severity})
    else:
        findings = []

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_id = _normalize_id(
            finding.get("rule_id")
            or finding.get("code")
            or finding.get("id")
            or finding.get("message")
        )
        if not finding_id:
            continue
        severity = _normalize_id(finding.get("severity")).lower()
        if severity in {"error", "fatal"}:
            error_ids.add(finding_id)
        elif severity in {"warning", "warn"}:
            warning_ids.add(finding_id)
        else:
            warning_ids.add(finding_id)
    return error_ids, warning_ids


def _jaccard(lhs: set[str], rhs: set[str]) -> float:
    union = lhs | rhs
    if not union:
        return 1.0
    return len(lhs & rhs) / len(union)


def _safe_div(num: int, den: int) -> float:
    if den <= 0:
        return 1.0 if num == 0 else 0.0
    return num / den


@dataclass(frozen=True)
class BaselineComparison:
    formal_error_ids: list[str]
    baseline_error_ids: list[str]
    matched_error_ids: list[str]
    formal_only_error_ids: list[str]
    baseline_only_error_ids: list[str]
    status_agreement: bool
    issue_jaccard: float
    precision: float
    recall: float
    f1: float
    meets_95pct_target: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "formalfinance.baseline_comparison.v0",
            "formal_error_ids": self.formal_error_ids,
            "baseline_error_ids": self.baseline_error_ids,
            "matched_error_ids": self.matched_error_ids,
            "formal_only_error_ids": self.formal_only_error_ids,
            "baseline_only_error_ids": self.baseline_only_error_ids,
            "metrics": {
                "status_agreement": self.status_agreement,
                "issue_jaccard": round(self.issue_jaccard, 6),
                "precision": round(self.precision, 6),
                "recall": round(self.recall, 6),
                "f1": round(self.f1, 6),
                "meets_95pct_target": self.meets_95pct_target,
            },
        }


def compare_with_baseline(formal_report: dict[str, Any], baseline_payload: Any) -> BaselineComparison:
    formal_errors, _ = _extract_formal_ids(formal_report)
    baseline_errors, _ = _extract_baseline_ids(baseline_payload)

    matched = formal_errors & baseline_errors
    formal_only = formal_errors - baseline_errors
    baseline_only = baseline_errors - formal_errors

    precision = _safe_div(len(matched), len(formal_errors))
    recall = _safe_div(len(matched), len(baseline_errors))
    f1 = _safe_div(2 * len(matched), len(formal_errors) + len(baseline_errors))
    issue_jaccard = _jaccard(formal_errors, baseline_errors)
    status_agreement = bool(formal_errors) == bool(baseline_errors)
    meets_95pct_target = status_agreement and issue_jaccard >= 0.95

    return BaselineComparison(
        formal_error_ids=sorted(formal_errors),
        baseline_error_ids=sorted(baseline_errors),
        matched_error_ids=sorted(matched),
        formal_only_error_ids=sorted(formal_only),
        baseline_only_error_ids=sorted(baseline_only),
        status_agreement=status_agreement,
        issue_jaccard=issue_jaccard,
        precision=precision,
        recall=recall,
        f1=f1,
        meets_95pct_target=meets_95pct_target,
    )


def load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)
