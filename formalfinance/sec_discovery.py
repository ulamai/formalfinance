from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import json
from urllib.request import Request, urlopen

from .sec_ingest import normalize_cik


TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def _fetch_json(url: str, user_agent: str, timeout_seconds: int = 30) -> Any:
    req = Request(
        url,
        headers={
            "User-Agent": user_agent.strip(),
            "Accept": "application/json",
        },
        method="GET",
    )
    with urlopen(req, timeout=timeout_seconds) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def fetch_company_tickers(user_agent: str, timeout_seconds: int = 30) -> dict[str, Any]:
    if not user_agent.strip():
        raise ValueError("A non-empty SEC-compliant User-Agent is required.")
    payload = _fetch_json(TICKERS_URL, user_agent=user_agent, timeout_seconds=timeout_seconds)
    if not isinstance(payload, dict):
        raise ValueError("Unexpected SEC tickers payload type.")
    return payload


def fetch_submissions(cik: str | int, user_agent: str, timeout_seconds: int = 30) -> dict[str, Any]:
    if not user_agent.strip():
        raise ValueError("A non-empty SEC-compliant User-Agent is required.")
    cik10 = normalize_cik(cik)
    payload = _fetch_json(SUBMISSIONS_URL.format(cik=cik10), user_agent=user_agent, timeout_seconds=timeout_seconds)
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected submissions payload type for CIK {cik10}.")
    return payload


def _parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _to_rows(recent: dict[str, Any]) -> list[dict[str, Any]]:
    keys = [
        "accessionNumber",
        "form",
        "filingDate",
        "reportDate",
        "primaryDocument",
        "isXBRL",
        "isInlineXBRL",
    ]
    arrays = {key: list(recent.get(key, []) or []) for key in keys}
    length = min(len(arrays[key]) for key in keys if arrays.get(key) is not None)
    rows: list[dict[str, Any]] = []
    for i in range(length):
        rows.append(
            {
                "accession": arrays["accessionNumber"][i],
                "form": arrays["form"][i],
                "filing_date": arrays["filingDate"][i],
                "report_date": arrays["reportDate"][i],
                "primary_document": arrays["primaryDocument"][i],
                "is_xbrl": bool(arrays["isXBRL"][i]),
                "is_inline_xbrl": bool(arrays["isInlineXBRL"][i]),
            }
        )
    return rows


@dataclass(frozen=True)
class DiscoveredFiling:
    cik: str
    company_name: str
    accession: str
    form: str
    filing_date: str
    report_date: str | None
    primary_document: str | None
    is_xbrl: bool
    is_inline_xbrl: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "cik": self.cik,
            "company_name": self.company_name,
            "accession": self.accession,
            "form": self.form,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "primary_document": self.primary_document,
            "is_xbrl": self.is_xbrl,
            "is_inline_xbrl": self.is_inline_xbrl,
        }


def discover_recent_filings(
    user_agent: str,
    forms: list[str] | None = None,
    max_filings: int = 100,
    cik_limit: int = 250,
    filed_on_or_after: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    if max_filings <= 0:
        raise ValueError("max_filings must be > 0.")
    if cik_limit <= 0:
        raise ValueError("cik_limit must be > 0.")

    normalized_forms = {f.strip().upper() for f in (forms or ["10-K", "10-Q"]) if f.strip()}
    date_floor = _parse_iso_date(filed_on_or_after) if filed_on_or_after else None
    if filed_on_or_after and date_floor is None:
        raise ValueError(f"Invalid date '{filed_on_or_after}', expected YYYY-MM-DD.")

    tickers_payload = fetch_company_tickers(user_agent=user_agent, timeout_seconds=timeout_seconds)
    raw_rows = tickers_payload.get("data", []) or []
    ciks: list[str] = []
    for row in raw_rows:
        if not isinstance(row, list) or len(row) < 1:
            continue
        ciks.append(normalize_cik(row[0]))
    ciks = ciks[:cik_limit]

    discovered: list[DiscoveredFiling] = []
    visited_ciks = 0
    for cik in ciks:
        if len(discovered) >= max_filings:
            break
        try:
            submissions = fetch_submissions(cik, user_agent=user_agent, timeout_seconds=timeout_seconds)
        except Exception:
            continue
        visited_ciks += 1
        company_name = str(submissions.get("name") or "")
        recent = ((submissions.get("filings") or {}).get("recent") or {})
        for row in _to_rows(recent):
            if len(discovered) >= max_filings:
                break
            form = str(row.get("form") or "").upper()
            if normalized_forms and form not in normalized_forms:
                continue
            filing_date = str(row.get("filing_date") or "")
            filing_dt = _parse_iso_date(filing_date)
            if date_floor and (filing_dt is None or filing_dt < date_floor):
                continue
            accession = str(row.get("accession") or "").strip()
            if not accession:
                continue
            discovered.append(
                DiscoveredFiling(
                    cik=cik,
                    company_name=company_name,
                    accession=accession,
                    form=form,
                    filing_date=filing_date,
                    report_date=row.get("report_date"),
                    primary_document=row.get("primary_document"),
                    is_xbrl=bool(row.get("is_xbrl")),
                    is_inline_xbrl=bool(row.get("is_inline_xbrl")),
                )
            )

    return {
        "schema_version": "formalfinance.sec_discovery.v0",
        "parameters": {
            "forms": sorted(normalized_forms),
            "max_filings": max_filings,
            "cik_limit": cik_limit,
            "filed_on_or_after": filed_on_or_after,
        },
        "summary": {
            "visited_ciks": visited_ciks,
            "discovered_filings": len(discovered),
            "inline_xbrl_count": len([f for f in discovered if f.is_inline_xbrl]),
            "xbrl_count": len([f for f in discovered if f.is_xbrl]),
        },
        "filings": [item.as_dict() for item in discovered],
    }
