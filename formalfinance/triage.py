from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


VALID_STATUSES = {"open", "in_progress", "blocked", "resolved", "accepted_risk"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _priority_for_severity(severity: str) -> str:
    lowered = (severity or "").lower()
    if lowered == "error":
        return "high"
    if lowered == "warning":
        return "medium"
    return "low"


def init_triage_from_report(
    report: dict[str, Any],
    *,
    owner: str | None = None,
) -> dict[str, Any]:
    findings = report.get("findings", []) or []
    issues: list[dict[str, Any]] = []
    for idx, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("finding_id") or "")
        if not finding_id:
            rule_id = str(finding.get("rule_id") or "finding")
            finding_id = f"{rule_id}:{idx:04d}"
        if not finding_id:
            continue
        issues.append(
            {
                "finding_id": finding_id,
                "rule_id": finding.get("rule_id"),
                "severity": finding.get("severity"),
                "message": finding.get("message"),
                "status": "open",
                "priority": _priority_for_severity(str(finding.get("severity") or "")),
                "assignee": owner,
                "notes": [],
                "last_updated_at": _utc_now(),
            }
        )

    return {
        "schema_version": "formalfinance.triage.v0",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "input_digest": report.get("input_digest"),
        "profile": report.get("profile"),
        "issues": issues,
    }


def write_triage(path: str | Path, triage: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fp:
        json.dump(triage, fp, indent=2, sort_keys=True)
        fp.write("\n")


def load_triage(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


@dataclass(frozen=True)
class TriageUpdate:
    finding_id: str
    status: str | None = None
    assignee: str | None = None
    note: str | None = None


def apply_triage_update(triage: dict[str, Any], update: TriageUpdate) -> dict[str, Any]:
    status = update.status.strip().lower() if update.status else None
    if status and status not in VALID_STATUSES:
        valid = ", ".join(sorted(VALID_STATUSES))
        raise ValueError(f"Invalid triage status '{update.status}'. Expected one of: {valid}.")

    found = False
    for issue in triage.get("issues", []) or []:
        if str(issue.get("finding_id")) != update.finding_id:
            continue
        found = True
        if status:
            issue["status"] = status
        if update.assignee is not None:
            issue["assignee"] = update.assignee
        if update.note:
            notes = issue.get("notes")
            if not isinstance(notes, list):
                notes = []
            notes.append({"timestamp": _utc_now(), "note": update.note})
            issue["notes"] = notes
        issue["last_updated_at"] = _utc_now()
        break

    if not found:
        raise ValueError(f"finding_id '{update.finding_id}' not found in triage file.")

    triage["updated_at"] = _utc_now()
    return triage
