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
from .triage import init_triage_from_report, write_triage


@dataclass(frozen=True)
class EvidencePackResult:
    output_dir: Path
    status: str
    report_path: Path
    trace_path: Path
    summary_path: Path
    html_summary_path: Path
    triage_path: Path
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


def _write_html_summary(path: Path, profile: str, report: dict) -> None:
    findings = report.get("findings", []) or []
    summary = report.get("summary", {}) or {}
    rows = []
    for finding in findings:
        fid = str(finding.get("finding_id") or "")
        severity = str(finding.get("severity") or "")
        rule_id = str(finding.get("rule_id") or "")
        message = str(finding.get("message") or "")
        rows.append(
            "<tr>"
            f"<td>{fid}</td>"
            f"<td>{severity}</td>"
            f"<td>{rule_id}</td>"
            f"<td>{message}</td>"
            "</tr>"
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FormalFinance Evidence Summary</title>
  <style>
    body {{ font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #111; background: #f7fafc; }}
    h1 {{ margin: 0 0 16px; }}
    .meta {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; margin-bottom: 16px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e2e8f0; }}
    th, td {{ border-bottom: 1px solid #e2e8f0; text-align: left; padding: 8px; font-size: 14px; }}
    th {{ background: #edf2f7; }}
    tr:last-child td {{ border-bottom: 0; }}
    .sev-error {{ color: #b91c1c; font-weight: 600; }}
    .sev-warning {{ color: #92400e; font-weight: 600; }}
  </style>
</head>
<body>
  <h1>FormalFinance Evidence Summary</h1>
  <div class="meta">
    <div><strong>Profile:</strong> {profile}</div>
    <div><strong>Status:</strong> {report.get("status")}</div>
    <div><strong>Errors:</strong> {summary.get("error_count", 0)} | <strong>Warnings:</strong> {summary.get("warning_count", 0)}</div>
    <div><strong>Input Digest:</strong> {report.get("input_digest", "")}</div>
  </div>
  <table>
    <thead>
      <tr><th>Finding ID</th><th>Severity</th><th>Rule ID</th><th>Message</th></tr>
    </thead>
    <tbody>
      {''.join(rows) if rows else '<tr><td colspan="4">No findings.</td></tr>'}
    </tbody>
  </table>
</body>
</html>"""
    path.write_text(html, encoding="utf-8")


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
    html_summary_path = out_dir / "summary.html"
    triage_path = out_dir / "triage.json"
    certificate_path: Path | None = out_dir / "certificate.json" if include_certificate else None
    manifest_path = out_dir / "manifest.json"

    report, result = run_validation(filing, normalized_profile, trace_path=trace_path)
    _write_json(report_path, report)
    _write_markdown_summary(summary_path, normalized_profile, report)
    _write_html_summary(html_summary_path, normalized_profile, report)
    write_triage(triage_path, init_triage_from_report(report))

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
            "summary_html": html_summary_path.name,
            "triage": triage_path.name,
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
        html_summary_path=html_summary_path,
        triage_path=triage_path,
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
