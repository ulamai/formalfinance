from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

from .baseline_compare import compare_with_baseline, load_json_file
from .evidence import filing_from_path, run_validation
from .profiles import normalize_profile_name


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    filing_path: str
    baseline_path: str
    profile: str


def _to_case(obj: dict[str, Any], index: int) -> BenchmarkCase:
    case_id = str(obj.get("id") or f"case-{index:04d}")
    filing_path = str(obj.get("filing") or "").strip()
    baseline_path = str(obj.get("baseline_report") or "").strip()
    profile = normalize_profile_name(str(obj.get("profile") or "ixbrl-gating"))
    if not filing_path:
        raise ValueError(f"Case {case_id} missing `filing` path.")
    if not baseline_path:
        raise ValueError(f"Case {case_id} missing `baseline_report` path.")
    return BenchmarkCase(
        case_id=case_id,
        filing_path=filing_path,
        baseline_path=baseline_path,
        profile=profile,
    )


def load_benchmark_manifest(path: str | Path) -> list[BenchmarkCase]:
    payload = load_json_file(str(path))
    if not isinstance(payload, dict):
        raise ValueError("Benchmark manifest must be a JSON object.")
    rows = payload.get("cases", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("Benchmark manifest must include non-empty `cases` array.")
    cases: list[BenchmarkCase] = []
    for idx, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Case #{idx} must be an object.")
        cases.append(_to_case(item, idx))
    return cases


def run_baseline_benchmark(cases: list[BenchmarkCase]) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    status_agreements = 0
    jaccard_total = 0.0
    precision_total = 0.0
    recall_total = 0.0
    f1_total = 0.0
    meets_95pct = 0

    for case in cases:
        filing = filing_from_path(case.filing_path)
        report, _ = run_validation(filing, case.profile, trace_path=None)
        baseline = load_json_file(case.baseline_path)
        comparison = compare_with_baseline(report, baseline).as_dict()
        metrics = comparison["metrics"]
        if metrics["status_agreement"]:
            status_agreements += 1
        jaccard_total += float(metrics["issue_jaccard"])
        precision_total += float(metrics["precision"])
        recall_total += float(metrics["recall"])
        f1_total += float(metrics["f1"])
        if metrics["meets_95pct_target"]:
            meets_95pct += 1

        details.append(
            {
                "case_id": case.case_id,
                "profile": case.profile,
                "filing_path": case.filing_path,
                "baseline_path": case.baseline_path,
                "validation_status": report["status"],
                "comparison": comparison,
            }
        )

    count = len(cases)
    summary = {
        "case_count": count,
        "status_agreement_rate": (status_agreements / count) if count else 0.0,
        "issue_jaccard_mean": (jaccard_total / count) if count else 0.0,
        "precision_mean": (precision_total / count) if count else 0.0,
        "recall_mean": (recall_total / count) if count else 0.0,
        "f1_mean": (f1_total / count) if count else 0.0,
        "meets_95pct_target_cases": meets_95pct,
        "meets_95pct_target_rate": (meets_95pct / count) if count else 0.0,
    }

    return {
        "schema_version": "formalfinance.baseline_benchmark.v0",
        "summary": summary,
        "cases": details,
    }


def benchmark_from_manifest(path: str | Path) -> dict[str, Any]:
    cases = load_benchmark_manifest(path)
    return run_baseline_benchmark(cases)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, sort_keys=True)
        fp.write("\n")
