from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import json

from .certificate import issue_certificate
from .engine import ValidationEngine, ValidationResult
from .models import Filing
from .profiles import get_profile, normalize_profile_name
from .tracing import TraceLogger


@dataclass(frozen=True)
class EvidencePackResult:
    output_dir: Path
    status: str
    report_path: Path
    trace_path: Path
    summary_path: Path
    certificate_path: Path | None
    manifest_path: Path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, sort_keys=True)
        fp.write("\n")


def _write_markdown_summary(path: Path, profile: str, report: dict) -> None:
    findings = report.get("findings", []) or []
    by_severity: dict[str, list[dict]] = defaultdict(list)
    for finding in findings:
        by_severity[finding.get("severity", "unknown")].append(finding)

    lines: list[str] = []
    lines.append("# FormalFinance Evidence Summary")
    lines.append("")
    lines.append(f"- Profile: `{profile}`")
    lines.append(f"- Status: `{report['status']}`")
    lines.append(f"- Risk score: `{report['summary']['risk_score']}`")
    lines.append(f"- Rules executed: `{report['summary']['rules_executed']}`")
    lines.append(f"- Errors: `{report['summary']['error_count']}`")
    lines.append(f"- Warnings: `{report['summary']['warning_count']}`")
    lines.append(f"- Input digest: `{report['input_digest']}`")
    lines.append("")

    if not findings:
        lines.append("## Findings")
        lines.append("")
        lines.append("No findings.")
    else:
        lines.append("## Findings")
        lines.append("")
        for severity in ("error", "warning", "info"):
            items = by_severity.get(severity, [])
            if not items:
                continue
            lines.append(f"### {severity.capitalize()} ({len(items)})")
            lines.append("")
            for finding in items:
                fact_ids = ", ".join(finding.get("fact_ids", [])) or "-"
                lines.append(f"- `{finding['rule_id']}`: {finding['message']} (facts: {fact_ids})")
            lines.append("")

    lines.append("## Remediation Workflow")
    lines.append("")
    lines.append("1. Fix all `error` findings and re-run `formalfinance validate`.")
    lines.append("2. Review `warning` findings for classification/context quality.")
    lines.append("3. Re-generate evidence pack and archive report + trace + certificate.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_validation(filing: Filing, profile: str, trace_path: Path | None = None) -> tuple[dict, ValidationResult]:
    rules = get_profile(profile)
    engine = ValidationEngine(rules=rules)
    if trace_path is None:
        result = engine.validate(filing)
    else:
        with TraceLogger(str(trace_path)) as tracer:
            result = engine.validate(filing, trace_logger=tracer)
    return result.as_report(profile), result


def build_evidence_pack(
    filing: Filing,
    profile: str,
    output_dir: str | Path,
    include_certificate: bool = True,
) -> EvidencePackResult:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    normalized_profile = normalize_profile_name(profile)

    report_path = out_dir / "report.json"
    trace_path = out_dir / "trace.jsonl"
    summary_path = out_dir / "summary.md"
    certificate_path: Path | None = out_dir / "certificate.json" if include_certificate else None
    manifest_path = out_dir / "manifest.json"

    report, result = run_validation(filing, normalized_profile, trace_path=trace_path)
    _write_json(report_path, report)
    _write_markdown_summary(summary_path, normalized_profile, report)

    emitted_certificate_path: Path | None = None
    if include_certificate and report["status"] == "clean":
        certificate = issue_certificate(normalized_profile, result)
        _write_json(certificate_path, certificate)
        emitted_certificate_path = certificate_path

    manifest = {
        "schema_version": "formalfinance.evidence_pack.v0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": normalized_profile,
        "status": report["status"],
        "artifacts": {
            "report": report_path.name,
            "trace": trace_path.name,
            "summary": summary_path.name,
            "certificate": emitted_certificate_path.name if emitted_certificate_path else None,
        },
        "summary": report["summary"],
    }
    _write_json(manifest_path, manifest)

    return EvidencePackResult(
        output_dir=out_dir,
        status=report["status"],
        report_path=report_path,
        trace_path=trace_path,
        summary_path=summary_path,
        certificate_path=emitted_certificate_path,
        manifest_path=manifest_path,
    )


def filing_from_path(path: str | Path) -> Filing:
    raw = Path(path).read_text(encoding="utf-8")
    return Filing.from_dict(json.loads(raw))


def filing_to_dict(filing: Filing) -> dict:
    return filing.canonical_object()


def selection_to_dict(selection: object) -> dict:
    if hasattr(selection, "__dataclass_fields__"):
        return asdict(selection)
    raise TypeError("selection object is not a dataclass instance")
