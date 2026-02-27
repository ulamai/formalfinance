from __future__ import annotations

import argparse
import json
from pathlib import Path

from .certificate import issue_certificate
from .engine import ValidationEngine
from .models import Filing
from .profiles import get_profile
from .tracing import TraceLogger


def _load_filing(path: str) -> Filing:
    with open(path, "r", encoding="utf-8") as fp:
        obj = json.load(fp)
    return Filing.from_dict(obj)


def _write_json(path: str | None, payload: dict) -> None:
    if path is None:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, sort_keys=True)
        fp.write("\n")


def _validate_once(filing_path: str, profile: str, trace_path: str | None) -> tuple[dict, object]:
    filing = _load_filing(filing_path)
    rules = get_profile(profile)
    engine = ValidationEngine(rules=rules)
    if trace_path:
        with TraceLogger(trace_path) as tracer:
            result = engine.validate(filing, trace_logger=tracer)
    else:
        result = engine.validate(filing)
    return result.as_report(profile), result


def _cmd_validate(args: argparse.Namespace) -> int:
    report, _ = _validate_once(args.filing, args.profile, args.trace)
    _write_json(args.report, report)
    return 0 if report["status"] == "clean" else 2


def _cmd_certify(args: argparse.Namespace) -> int:
    report, result = _validate_once(args.filing, args.profile, args.trace)
    if args.report:
        _write_json(args.report, report)
    if report["status"] != "clean":
        print("Cannot issue certificate: filing has validation errors.")
        return 2
    certificate = issue_certificate(args.profile, result)
    _write_json(args.certificate, certificate)
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
    certify_parser.set_defaults(handler=_cmd_certify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
