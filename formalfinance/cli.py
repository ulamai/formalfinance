from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .baseline_compare import compare_with_baseline, load_json_file
from .certificate import issue_certificate
from .evidence import (
    build_evidence_pack,
    filing_from_path,
    filing_to_dict,
    run_validation,
    selection_to_dict,
)
from .pilot_readiness import build_readiness_report
from .profiles import list_profiles, normalize_profile_name
from .sec_discovery import discover_recent_filings
from .sec_ingest import companyfacts_to_filing, fetch_companyfacts_json


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def _write_json(path: str | None, payload: dict) -> None:
    if path is None:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, sort_keys=True)
        fp.write("\n")


def _exit_code_for_status(status: str) -> int:
    if status == "clean":
        return 0
    if status == "review":
        return 1
    return 2


def _validate_once(filing_path: str, profile: str, trace_path: str | None) -> tuple[dict, object]:
    filing = filing_from_path(filing_path)
    normalized_profile = normalize_profile_name(profile)
    trace = Path(trace_path) if trace_path else None
    return run_validation(filing, normalized_profile, trace_path=trace)


def _cmd_validate(args: argparse.Namespace) -> int:
    report, _ = _validate_once(args.filing, args.profile, args.trace)
    _write_json(args.report, report)
    return _exit_code_for_status(report["status"])


def _cmd_certify(args: argparse.Namespace) -> int:
    report, result = _validate_once(args.filing, args.profile, args.trace)
    if args.report:
        _write_json(args.report, report)
    if report["status"] != "clean":
        print(f"Cannot issue certificate: validation status is '{report['status']}'.")
        return _exit_code_for_status(report["status"])
    certificate = issue_certificate(normalize_profile_name(args.profile), result)
    _write_json(args.certificate, certificate)
    return 0


def _cmd_profiles(args: argparse.Namespace) -> int:
    profiles = list_profiles()
    if args.json:
        payload = [
            {"name": profile.name, "description": profile.description, "rule_count": len(profile.rules)}
            for profile in profiles
        ]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    for profile in profiles:
        print(f"{profile.name:26} {len(profile.rules):2} rules  {profile.description}")
    return 0


def _cmd_fetch_companyfacts(args: argparse.Namespace) -> int:
    user_agent = args.user_agent or os.getenv("FORMALFINANCE_USER_AGENT", "")
    payload = fetch_companyfacts_json(args.cik, user_agent=user_agent, timeout_seconds=args.timeout)
    _write_json(args.output, payload)
    return 0


def _cmd_normalize_companyfacts(args: argparse.Namespace) -> int:
    payload = _load_json(args.companyfacts)
    filing, selection = companyfacts_to_filing(
        payload,
        accession=args.accession,
        form=args.form,
        max_facts=args.max_facts,
    )
    _write_json(args.output, filing_to_dict(filing))
    if args.selection:
        _write_json(args.selection, selection_to_dict(selection))
    return 0


def _cmd_evidence_pack(args: argparse.Namespace) -> int:
    filing = filing_from_path(args.filing)
    result = build_evidence_pack(
        filing=filing,
        profile=args.profile,
        output_dir=args.output_dir,
        include_certificate=not args.no_certificate,
    )
    manifest = {
        "output_dir": str(result.output_dir),
        "status": result.status,
        "report_path": str(result.report_path),
        "trace_path": str(result.trace_path),
        "summary_path": str(result.summary_path),
        "certificate_path": str(result.certificate_path) if result.certificate_path else None,
        "manifest_path": str(result.manifest_path),
    }
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return _exit_code_for_status(result.status)


def _cmd_discover_filings(args: argparse.Namespace) -> int:
    user_agent = args.user_agent or os.getenv("FORMALFINANCE_USER_AGENT", "")
    forms = [item.strip() for item in args.forms.split(",") if item.strip()]
    payload = discover_recent_filings(
        user_agent=user_agent,
        forms=forms,
        max_filings=args.max_filings,
        cik_limit=args.cik_limit,
        filed_on_or_after=args.filed_on_or_after,
        timeout_seconds=args.timeout,
    )
    _write_json(args.output, payload)
    return 0


def _cmd_compare_baseline(args: argparse.Namespace) -> int:
    formal = load_json_file(args.formal_report)
    baseline = load_json_file(args.baseline_report)
    comparison = compare_with_baseline(formal, baseline).as_dict()
    _write_json(args.output, comparison)
    return 0 if comparison["metrics"]["meets_95pct_target"] else 3


def _cmd_pilot_readiness(args: argparse.Namespace) -> int:
    report = build_readiness_report(
        min_rules=args.min_rules,
        max_rules=args.max_rules,
        min_filings=args.min_filings,
        max_filings=args.max_filings,
        user_agent=args.user_agent,
    )
    _write_json(args.output, report)
    return 0 if report["summary"]["ready"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="formalfinance",
        description="FormalFinance CLI for SEC filing conformance checks and evidence generation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Run profile checks and produce a risk report.")
    validate_parser.add_argument("filing", help="Path to normalized filing JSON.")
    validate_parser.add_argument(
        "--profile",
        default="ixbrl-gating",
        help="Validation profile: ixbrl-gating or fsd-consistency.",
    )
    validate_parser.add_argument("--trace", help="Path to JSONL execution trace.", default=None)
    validate_parser.add_argument("--report", help="Path to JSON risk report. Prints to stdout if omitted.", default=None)
    validate_parser.set_defaults(handler=_cmd_validate)

    certify_parser = subparsers.add_parser(
        "certify",
        help="Run validation and emit a compliance certificate when clean.",
    )
    certify_parser.add_argument("filing", help="Path to normalized filing JSON.")
    certify_parser.add_argument(
        "--profile",
        default="ixbrl-gating",
        help="Validation profile: ixbrl-gating or fsd-consistency.",
    )
    certify_parser.add_argument(
        "--certificate",
        help="Path to certificate JSON. Prints to stdout if omitted.",
        default=None,
    )
    certify_parser.add_argument("--trace", help="Path to JSONL execution trace.", default=None)
    certify_parser.add_argument(
        "--report",
        help="Optional path to save full validation report.",
        default=None,
    )
    certify_parser.set_defaults(handler=_cmd_certify)

    profiles_parser = subparsers.add_parser("profiles", help="List available validation profiles.")
    profiles_parser.add_argument("--json", action="store_true", help="Emit profiles as JSON.")
    profiles_parser.set_defaults(handler=_cmd_profiles)

    fetch_parser = subparsers.add_parser(
        "fetch-companyfacts",
        help="Fetch SEC companyfacts JSON from data.sec.gov for a CIK.",
    )
    fetch_parser.add_argument("cik", help="CIK (numeric or zero-padded string).")
    fetch_parser.add_argument(
        "--user-agent",
        default=None,
        help="SEC-compliant User-Agent header (or set FORMALFINANCE_USER_AGENT).",
    )
    fetch_parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    fetch_parser.add_argument(
        "--output",
        default=None,
        help="Path to save companyfacts JSON. Prints to stdout if omitted.",
    )
    fetch_parser.set_defaults(handler=_cmd_fetch_companyfacts)

    normalize_parser = subparsers.add_parser(
        "normalize-companyfacts",
        help="Convert SEC companyfacts JSON into canonical FormalFinance filing JSON.",
    )
    normalize_parser.add_argument("companyfacts", help="Path to SEC companyfacts JSON file.")
    normalize_parser.add_argument("--accession", default=None, help="Select a specific accession number.")
    normalize_parser.add_argument("--form", default=None, help="Filter by SEC form (for example 10-K, 10-Q).")
    normalize_parser.add_argument(
        "--max-facts",
        type=int,
        default=None,
        help="Optional cap for fact count after filtering.",
    )
    normalize_parser.add_argument(
        "--selection",
        default=None,
        help="Optional path to write normalization selection metadata JSON.",
    )
    normalize_parser.add_argument(
        "--output",
        default=None,
        help="Path to save normalized filing JSON. Prints to stdout if omitted.",
    )
    normalize_parser.set_defaults(handler=_cmd_normalize_companyfacts)

    evidence_parser = subparsers.add_parser(
        "evidence-pack",
        help="Run validation and write an audit-ready evidence bundle to a directory.",
    )
    evidence_parser.add_argument("filing", help="Path to normalized filing JSON.")
    evidence_parser.add_argument(
        "--profile",
        default="ixbrl-gating",
        help="Validation profile to execute.",
    )
    evidence_parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for report, trace, summary, manifest, and optional certificate.",
    )
    evidence_parser.add_argument(
        "--no-certificate",
        action="store_true",
        help="Do not emit certificate even when status is clean.",
    )
    evidence_parser.set_defaults(handler=_cmd_evidence_pack)

    discover_parser = subparsers.add_parser(
        "discover-recent-filings",
        help="Discover recent SEC filings suitable for pilot sampling.",
    )
    discover_parser.add_argument(
        "--forms",
        default="10-K,10-Q",
        help="Comma-separated SEC forms to include.",
    )
    discover_parser.add_argument(
        "--max-filings",
        type=int,
        default=100,
        help="Maximum number of discovered filings to return.",
    )
    discover_parser.add_argument(
        "--cik-limit",
        type=int,
        default=250,
        help="Maximum number of issuers (CIKs) to scan.",
    )
    discover_parser.add_argument(
        "--filed-on-or-after",
        default=None,
        help="Optional lower filing-date bound (YYYY-MM-DD).",
    )
    discover_parser.add_argument(
        "--user-agent",
        default=None,
        help="SEC-compliant User-Agent header (or set FORMALFINANCE_USER_AGENT).",
    )
    discover_parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    discover_parser.add_argument(
        "--output",
        default=None,
        help="Path to save discovered filing list JSON. Prints to stdout if omitted.",
    )
    discover_parser.set_defaults(handler=_cmd_discover_filings)

    baseline_parser = subparsers.add_parser(
        "compare-baseline",
        help="Compare FormalFinance findings against baseline validator output.",
    )
    baseline_parser.add_argument("formal_report", help="Path to FormalFinance report JSON.")
    baseline_parser.add_argument("baseline_report", help="Path to baseline report JSON.")
    baseline_parser.add_argument(
        "--output",
        default=None,
        help="Path to save comparison JSON. Prints to stdout if omitted.",
    )
    baseline_parser.set_defaults(handler=_cmd_compare_baseline)

    readiness_parser = subparsers.add_parser(
        "pilot-readiness",
        help="Check whether pilot prerequisites and scope controls are in place.",
    )
    readiness_parser.add_argument("--min-rules", type=int, default=30, help="Minimum target rule count.")
    readiness_parser.add_argument("--max-rules", type=int, default=50, help="Maximum target rule count.")
    readiness_parser.add_argument(
        "--min-filings",
        type=int,
        default=50,
        help="Minimum pilot sample size target.",
    )
    readiness_parser.add_argument(
        "--max-filings",
        type=int,
        default=100,
        help="Maximum pilot sample size target.",
    )
    readiness_parser.add_argument(
        "--user-agent",
        default=None,
        help="Optional SEC User-Agent override for readiness checks.",
    )
    readiness_parser.add_argument(
        "--output",
        default=None,
        help="Path to save readiness JSON. Prints to stdout if omitted.",
    )
    readiness_parser.set_defaults(handler=_cmd_pilot_readiness)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
