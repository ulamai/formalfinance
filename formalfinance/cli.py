from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import __version__
from .api import ServiceConfig, run_server
from .baseline_compare import compare_with_baseline, load_json_file
from .benchmark import benchmark_from_manifest
from .certificate import issue_certificate, sign_certificate, verify_certificate
from .evidence import (
    build_evidence_pack,
    filing_from_path,
    filing_to_dict,
    run_validation,
    selection_to_dict,
)
from .pilot_readiness import build_readiness_report
from .proof import build_proof_bundle, replay_proof_bundle
from .profiles import list_profiles, normalize_profile_name
from .rulebook import build_global_rulebook, build_rulebook
from .sec_accession_ingest import ingest_accession_to_filing
from .sec_discovery import discover_recent_filings
from .sec_ingest import companyfacts_to_filing, fetch_companyfacts_json
from .store import RunStore
from .triage import TriageUpdate, apply_triage_update, init_triage_from_report, load_triage, write_triage


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
    signing_secret = args.signing_secret or os.getenv("FORMALFINANCE_CERT_SIGNING_SECRET", "")
    key_id = args.key_id or os.getenv("FORMALFINANCE_CERT_SIGNING_KEY_ID") or None
    certificate = issue_certificate(
        normalize_profile_name(args.profile),
        result,
        signing_secret=signing_secret or None,
        key_id=key_id,
    )
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


def _cmd_ingest_accession(args: argparse.Namespace) -> int:
    user_agent = args.user_agent or os.getenv("FORMALFINANCE_USER_AGENT", "")
    try:
        filing, metadata = ingest_accession_to_filing(
            cik=args.cik,
            accession=args.accession,
            user_agent=user_agent,
            timeout_seconds=args.timeout,
            include_companyfacts=not args.no_companyfacts,
            max_scan_docs=args.max_scan_docs,
            max_doc_scan_bytes=args.max_doc_scan_bytes,
        )
        _write_json(args.output, filing_to_dict(filing))
        if args.metadata:
            _write_json(args.metadata, metadata.as_dict())
        return 0
    except Exception as exc:
        print(f"Ingestion failed: {exc}")
        return 5


def _cmd_evidence_pack(args: argparse.Namespace) -> int:
    filing = filing_from_path(args.filing)
    signing_secret = args.signing_secret or os.getenv("FORMALFINANCE_CERT_SIGNING_SECRET", "")
    key_id = args.key_id or os.getenv("FORMALFINANCE_CERT_SIGNING_KEY_ID") or None
    result = build_evidence_pack(
        filing=filing,
        profile=args.profile,
        output_dir=args.output_dir,
        include_certificate=not args.no_certificate,
        certificate_signing_secret=signing_secret or None,
        certificate_key_id=key_id,
    )
    manifest = {
        "output_dir": str(result.output_dir),
        "status": result.status,
        "report_path": str(result.report_path),
        "trace_path": str(result.trace_path),
        "summary_path": str(result.summary_path),
        "summary_html_path": str(result.html_summary_path),
        "triage_path": str(result.triage_path),
        "certificate_path": str(result.certificate_path) if result.certificate_path else None,
        "proof_path": str(result.proof_path),
        "manifest_path": str(result.manifest_path),
    }
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return _exit_code_for_status(result.status)


def _cmd_sign_certificate(args: argparse.Namespace) -> int:
    try:
        certificate = _load_json(args.certificate)
        signing_secret = args.signing_secret or os.getenv("FORMALFINANCE_CERT_SIGNING_SECRET", "")
        if not signing_secret:
            print("Certificate signing secret is required (pass --signing-secret or set FORMALFINANCE_CERT_SIGNING_SECRET).")
            return 5
        key_id = args.key_id or os.getenv("FORMALFINANCE_CERT_SIGNING_KEY_ID") or None
        signed = sign_certificate(certificate, signing_secret=signing_secret, key_id=key_id)
        _write_json(args.output, signed)
        return 0
    except Exception as exc:
        print(f"Certificate signing failed: {exc}")
        return 5


def _cmd_verify_certificate(args: argparse.Namespace) -> int:
    try:
        certificate = _load_json(args.certificate)
        signing_secret = args.signing_secret or os.getenv("FORMALFINANCE_CERT_SIGNING_SECRET", "")
        verification = verify_certificate(
            certificate,
            signing_secret=signing_secret or None,
            require_signature=bool(args.require_signature),
        )
        _write_json(args.output, verification)
        return 0 if verification["verified"] else 6
    except Exception as exc:
        print(f"Certificate verification failed: {exc}")
        return 5


def _cmd_build_proof(args: argparse.Namespace) -> int:
    filing = filing_from_path(args.filing)
    normalized_profile = normalize_profile_name(args.profile)
    trace = Path(args.trace) if args.trace else None
    report, result = run_validation(filing, normalized_profile, trace_path=trace)
    certificate_payload = None
    if report["status"] == "clean" and not args.no_certificate:
        signing_secret = args.signing_secret or os.getenv("FORMALFINANCE_CERT_SIGNING_SECRET", "")
        key_id = args.key_id or os.getenv("FORMALFINANCE_CERT_SIGNING_KEY_ID") or None
        certificate_payload = issue_certificate(
            normalized_profile,
            result,
            signing_secret=signing_secret or None,
            key_id=key_id,
        )
    proof = build_proof_bundle(
        filing=filing,
        profile=normalized_profile,
        report=report,
        result=result,
        certificate=certificate_payload,
    )
    _write_json(args.proof, proof)
    if args.report:
        _write_json(args.report, report)
    if args.certificate and certificate_payload is not None:
        _write_json(args.certificate, certificate_payload)
    return _exit_code_for_status(report["status"])


def _cmd_replay_proof(args: argparse.Namespace) -> int:
    try:
        proof = _load_json(args.proof)
        report = _load_json(args.report) if args.report else None
        certificate = _load_json(args.certificate) if args.certificate else None
        signing_secret = args.signing_secret or os.getenv("FORMALFINANCE_CERT_SIGNING_SECRET", "")
        replay = replay_proof_bundle(
            proof,
            report=report,
            certificate=certificate,
            signing_secret=signing_secret or None,
            require_certificate_signature=bool(args.require_certificate_signature),
            run_lean=bool(args.lean_check),
            lean_bin=args.lean_bin,
            lean_timeout_seconds=args.lean_timeout_seconds,
        )
        _write_json(args.output, replay)
        return 0 if replay["verified"] else 6
    except Exception as exc:
        print(f"Proof replay failed: {exc}")
        return 5


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


def _cmd_benchmark_baseline(args: argparse.Namespace) -> int:
    try:
        benchmark = benchmark_from_manifest(args.manifest)
    except Exception as exc:
        print(f"Benchmark failed: {exc}")
        return 5
    _write_json(args.output, benchmark)
    meets_rate = float(benchmark["summary"]["meets_95pct_target_rate"])
    return 0 if meets_rate >= float(args.pass_rate) else 4


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


def _cmd_rulebook(args: argparse.Namespace) -> int:
    profile = (args.profile or "all").strip().lower()
    payload = build_global_rulebook() if profile == "all" else build_rulebook(normalize_profile_name(profile))
    _write_json(args.output, payload)
    return 0


def _cmd_triage_init(args: argparse.Namespace) -> int:
    try:
        report = _load_json(args.report)
        triage = init_triage_from_report(report, owner=args.owner)
        _write_json(args.output, triage)
        return 0
    except Exception as exc:
        print(f"Triage init failed: {exc}")
        return 5


def _cmd_triage_update(args: argparse.Namespace) -> int:
    try:
        triage = load_triage(args.triage)
        updated = apply_triage_update(
            triage,
            TriageUpdate(
                finding_id=args.finding_id,
                status=args.status,
                assignee=args.assignee,
                note=args.note,
            ),
        )
        output = args.output or args.triage
        write_triage(output, updated)
        return 0
    except Exception as exc:
        print(f"Triage update failed: {exc}")
        return 5


def _cmd_serve(args: argparse.Namespace) -> int:
    config = ServiceConfig.from_args(
        host=args.host,
        port=args.port,
        db_path=args.db_path,
        api_keys_raw=args.api_keys,
        llm_enabled=args.llm_enabled if args.llm_enabled else None,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
        llm_api_key=args.llm_api_key,
        llm_timeout_seconds=args.llm_timeout_seconds,
        llm_max_findings=args.llm_max_findings,
        max_request_bytes=args.max_request_bytes,
        rate_limit_per_minute=args.rate_limit_per_minute,
        allowlist_cidrs_raw=args.allowlist_cidrs,
        cert_signing_secret=args.cert_signing_secret,
        cert_signing_key_id=args.cert_signing_key_id,
    )
    print(
        json.dumps(
            {
                "service": "formalfinance",
                "version": __version__,
                "host": config.host,
                "port": config.port,
                "db_path": config.db_path,
                "api_key_count": len(config.api_keys),
                "llm_default_enabled": config.llm_default.enabled,
                "llm_default_provider": config.llm_default.provider,
                "llm_default_model": config.llm_default.model,
                "max_request_bytes": config.max_request_bytes,
                "rate_limit_per_minute": config.rate_limit_per_minute,
                "allowlist_cidrs": list(config.allowlist_cidrs),
                "certificate_signing_enabled": bool(config.cert_signing_secret),
                "certificate_signing_key_id": config.cert_signing_key_id,
            },
            indent=2,
            sort_keys=True,
        )
    )
    run_server(config)
    return 0


def _cmd_db_migrate(args: argparse.Namespace) -> int:
    store = RunStore(args.db_path)
    status = store.migration_status()
    _write_json(args.output, status)
    return 0


def _cmd_db_status(args: argparse.Namespace) -> int:
    store = RunStore(args.db_path)
    status = store.migration_status()
    _write_json(args.output, status)
    return 0


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
    certify_parser.add_argument(
        "--signing-secret",
        default=None,
        help="Optional HMAC secret for certificate signing (or FORMALFINANCE_CERT_SIGNING_SECRET).",
    )
    certify_parser.add_argument(
        "--key-id",
        default=None,
        help="Optional certificate signing key identifier.",
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

    ingest_parser = subparsers.add_parser(
        "ingest-accession",
        help="Build a normalized filing from SEC accession-level raw package metadata + companyfacts.",
    )
    ingest_parser.add_argument("cik", help="CIK (numeric or zero-padded string).")
    ingest_parser.add_argument("accession", help="Accession number (0000000000-00-000000 or digits).")
    ingest_parser.add_argument(
        "--user-agent",
        default=None,
        help="SEC-compliant User-Agent header (or set FORMALFINANCE_USER_AGENT).",
    )
    ingest_parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    ingest_parser.add_argument(
        "--no-companyfacts",
        action="store_true",
        help="Do not enrich with companyfacts; emit structural filing metadata only.",
    )
    ingest_parser.add_argument(
        "--max-scan-docs",
        type=int,
        default=25,
        help="Maximum number of raw filing documents to fetch for metadata scanning.",
    )
    ingest_parser.add_argument(
        "--max-doc-scan-bytes",
        type=int,
        default=1000000,
        help="Max bytes per scanned document.",
    )
    ingest_parser.add_argument(
        "--metadata",
        default=None,
        help="Optional path to save ingestion metadata JSON.",
    )
    ingest_parser.add_argument(
        "--output",
        default=None,
        help="Path to save normalized filing JSON. Prints to stdout if omitted.",
    )
    ingest_parser.set_defaults(handler=_cmd_ingest_accession)

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
    evidence_parser.add_argument(
        "--signing-secret",
        default=None,
        help="Optional HMAC secret for evidence-pack certificate signing (or FORMALFINANCE_CERT_SIGNING_SECRET).",
    )
    evidence_parser.add_argument(
        "--key-id",
        default=None,
        help="Optional certificate signing key identifier for evidence-pack output.",
    )
    evidence_parser.set_defaults(handler=_cmd_evidence_pack)

    sign_cert_parser = subparsers.add_parser(
        "sign-certificate",
        help="Attach an HS256 signature to a certificate JSON payload.",
    )
    sign_cert_parser.add_argument("certificate", help="Path to certificate JSON file.")
    sign_cert_parser.add_argument(
        "--signing-secret",
        default=None,
        help="HMAC secret (or FORMALFINANCE_CERT_SIGNING_SECRET).",
    )
    sign_cert_parser.add_argument("--key-id", default=None, help="Optional key identifier.")
    sign_cert_parser.add_argument(
        "--output",
        default=None,
        help="Path to save signed certificate JSON. Prints to stdout if omitted.",
    )
    sign_cert_parser.set_defaults(handler=_cmd_sign_certificate)

    verify_cert_parser = subparsers.add_parser(
        "verify-certificate",
        help="Verify certificate structure and optional HS256 signature.",
    )
    verify_cert_parser.add_argument("certificate", help="Path to certificate JSON file.")
    verify_cert_parser.add_argument(
        "--signing-secret",
        default=None,
        help="HMAC secret for signature verification (or FORMALFINANCE_CERT_SIGNING_SECRET).",
    )
    verify_cert_parser.add_argument(
        "--require-signature",
        action="store_true",
        help="Fail verification when certificate has no signature block.",
    )
    verify_cert_parser.add_argument(
        "--output",
        default=None,
        help="Path to save verification JSON. Prints to stdout if omitted.",
    )
    verify_cert_parser.set_defaults(handler=_cmd_verify_certificate)

    build_proof_parser = subparsers.add_parser(
        "build-proof",
        help="Run validation and emit a replayable proof bundle.",
    )
    build_proof_parser.add_argument("filing", help="Path to normalized filing JSON.")
    build_proof_parser.add_argument(
        "--profile",
        default="ixbrl-gating",
        help="Validation profile to execute for proof generation.",
    )
    build_proof_parser.add_argument("--trace", help="Optional trace path for validation run.", default=None)
    build_proof_parser.add_argument(
        "--proof",
        default=None,
        help="Path to proof JSON output. Prints to stdout if omitted.",
    )
    build_proof_parser.add_argument(
        "--report",
        default=None,
        help="Optional path to save report JSON.",
    )
    build_proof_parser.add_argument(
        "--certificate",
        default=None,
        help="Optional path to save certificate JSON when status is clean.",
    )
    build_proof_parser.add_argument(
        "--no-certificate",
        action="store_true",
        help="Do not emit certificate payload during proof generation.",
    )
    build_proof_parser.add_argument(
        "--signing-secret",
        default=None,
        help="Optional HMAC secret for certificate signing (or FORMALFINANCE_CERT_SIGNING_SECRET).",
    )
    build_proof_parser.add_argument("--key-id", default=None, help="Optional certificate signing key identifier.")
    build_proof_parser.set_defaults(handler=_cmd_build_proof)

    replay_proof_parser = subparsers.add_parser(
        "replay-proof",
        help="Replay a proof bundle against report/certificate artifacts.",
    )
    replay_proof_parser.add_argument("proof", help="Path to proof JSON file.")
    replay_proof_parser.add_argument("--report", default=None, help="Optional report JSON path.")
    replay_proof_parser.add_argument("--certificate", default=None, help="Optional certificate JSON path.")
    replay_proof_parser.add_argument(
        "--signing-secret",
        default=None,
        help="Optional HMAC secret for certificate signature verification.",
    )
    replay_proof_parser.add_argument(
        "--require-certificate-signature",
        action="store_true",
        help="Fail replay when certificate signature is missing or invalid.",
    )
    replay_proof_parser.add_argument(
        "--lean-check",
        action="store_true",
        help="Run optional Lean checker for arithmetic claims in the proof bundle.",
    )
    replay_proof_parser.add_argument(
        "--lean-bin",
        default="lean",
        help="Lean executable path for --lean-check.",
    )
    replay_proof_parser.add_argument(
        "--lean-timeout-seconds",
        type=int,
        default=20,
        help="Timeout for Lean checker execution.",
    )
    replay_proof_parser.add_argument(
        "--output",
        default=None,
        help="Path to save replay result JSON. Prints to stdout if omitted.",
    )
    replay_proof_parser.set_defaults(handler=_cmd_replay_proof)

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

    benchmark_parser = subparsers.add_parser(
        "benchmark-baseline",
        help="Run baseline parity benchmark across a manifest of filing test cases.",
    )
    benchmark_parser.add_argument("manifest", help="Path to benchmark manifest JSON.")
    benchmark_parser.add_argument(
        "--pass-rate",
        type=float,
        default=0.95,
        help="Minimum rate of cases that must meet 95%% target to return success.",
    )
    benchmark_parser.add_argument(
        "--output",
        default=None,
        help="Path to save benchmark result JSON. Prints to stdout if omitted.",
    )
    benchmark_parser.set_defaults(handler=_cmd_benchmark_baseline)

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

    rulebook_parser = subparsers.add_parser(
        "rulebook",
        help="Emit formal rulebook metadata for governance and audit mapping.",
    )
    rulebook_parser.add_argument(
        "--profile",
        default="all",
        help="Profile name or `all`.",
    )
    rulebook_parser.add_argument(
        "--output",
        default=None,
        help="Path to save rulebook JSON. Prints to stdout if omitted.",
    )
    rulebook_parser.set_defaults(handler=_cmd_rulebook)

    triage_init_parser = subparsers.add_parser(
        "triage-init",
        help="Initialize triage workflow JSON from a FormalFinance report.",
    )
    triage_init_parser.add_argument("report", help="Path to FormalFinance report JSON.")
    triage_init_parser.add_argument("--owner", default=None, help="Default owner for new triage issues.")
    triage_init_parser.add_argument(
        "--output",
        default=None,
        help="Path to triage JSON output. Prints to stdout if omitted.",
    )
    triage_init_parser.set_defaults(handler=_cmd_triage_init)

    triage_update_parser = subparsers.add_parser(
        "triage-update",
        help="Update issue status/assignee/notes in a triage JSON file.",
    )
    triage_update_parser.add_argument("triage", help="Path to triage JSON file.")
    triage_update_parser.add_argument("--finding-id", required=True, help="Finding ID to update.")
    triage_update_parser.add_argument(
        "--status",
        default=None,
        help="New status: open, in_progress, blocked, resolved, accepted_risk.",
    )
    triage_update_parser.add_argument("--assignee", default=None, help="Assignee name/email.")
    triage_update_parser.add_argument("--note", default=None, help="Optional note appended to issue history.")
    triage_update_parser.add_argument(
        "--output",
        default=None,
        help="Optional output path; defaults to in-place update of the input file.",
    )
    triage_update_parser.set_defaults(handler=_cmd_triage_update)

    serve_parser = subparsers.add_parser(
        "serve",
        help="Run FormalFinance HTTP API service with optional API-key auth and run logging.",
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    serve_parser.add_argument("--port", type=int, default=8080, help="Bind port.")
    serve_parser.add_argument(
        "--db-path",
        default=".formalfinance/runs.sqlite3",
        help="SQLite path for run history and service metadata.",
    )
    serve_parser.add_argument(
        "--api-keys",
        default=None,
        help="Comma-separated API keys. If omitted, also reads FORMALFINANCE_API_KEYS.",
    )
    serve_parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=None,
        help="Maximum accepted request body size in bytes.",
    )
    serve_parser.add_argument(
        "--rate-limit-per-minute",
        type=int,
        default=None,
        help="Rate limit per API key/IP per minute.",
    )
    serve_parser.add_argument(
        "--allowlist-cidrs",
        default=None,
        help="Comma-separated CIDR allowlist for client IPs.",
    )
    serve_parser.add_argument(
        "--cert-signing-secret",
        default=None,
        help="Default HS256 secret for signing certificates in /v1/certify responses.",
    )
    serve_parser.add_argument(
        "--cert-signing-key-id",
        default=None,
        help="Default key identifier for signed certificates.",
    )
    serve_parser.add_argument(
        "--llm-enabled",
        action="store_true",
        help="Enable default LLM advisory generation for API validate/certify responses.",
    )
    serve_parser.add_argument(
        "--llm-provider",
        default=None,
        help="Default LLM provider: openai-compatible, ollama, mock, or none.",
    )
    serve_parser.add_argument("--llm-model", default=None, help="Default model name.")
    serve_parser.add_argument("--llm-base-url", default=None, help="Default LLM endpoint base URL.")
    serve_parser.add_argument("--llm-api-key", default=None, help="Default LLM API key.")
    serve_parser.add_argument(
        "--llm-timeout-seconds",
        type=int,
        default=None,
        help="Default LLM timeout seconds.",
    )
    serve_parser.add_argument(
        "--llm-max-findings",
        type=int,
        default=None,
        help="Max findings to send to the LLM in advisory prompts.",
    )
    serve_parser.set_defaults(handler=_cmd_serve)

    db_migrate_parser = subparsers.add_parser(
        "db-migrate",
        help="Apply/initialize SQLite schema migrations for FormalFinance service storage.",
    )
    db_migrate_parser.add_argument(
        "--db-path",
        default=".formalfinance/runs.sqlite3",
        help="SQLite path for migrations.",
    )
    db_migrate_parser.add_argument(
        "--output",
        default=None,
        help="Path to save migration status JSON. Prints to stdout if omitted.",
    )
    db_migrate_parser.set_defaults(handler=_cmd_db_migrate)

    db_status_parser = subparsers.add_parser(
        "db-status",
        help="Show applied SQLite schema migrations for FormalFinance service storage.",
    )
    db_status_parser.add_argument(
        "--db-path",
        default=".formalfinance/runs.sqlite3",
        help="SQLite path for status query.",
    )
    db_status_parser.add_argument(
        "--output",
        default=None,
        help="Path to save migration status JSON. Prints to stdout if omitted.",
    )
    db_status_parser.set_defaults(handler=_cmd_db_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
