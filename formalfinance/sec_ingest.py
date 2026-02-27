from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
import json
from urllib.request import Request, urlopen

from .models import Context, Fact, Filing


def normalize_cik(cik: str | int) -> str:
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    if not digits:
        raise ValueError(f"Invalid CIK '{cik}'.")
    return digits.zfill(10)


def fetch_companyfacts_json(cik: str | int, user_agent: str, timeout_seconds: int = 30) -> dict[str, Any]:
    if not user_agent.strip():
        raise ValueError("A non-empty SEC-compliant User-Agent is required.")
    cik10 = normalize_cik(cik)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    req = Request(
        url,
        headers={
            "User-Agent": user_agent.strip(),
            "Accept": "application/json",
        },
        method="GET",
    )
    with urlopen(req, timeout=timeout_seconds) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload)


def _parse_iso_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None


@dataclass(frozen=True)
class CompanyFactsSelection:
    accession: str | None
    form: str | None
    selected_count: int
    total_count: int


def _iter_fact_rows(companyfacts: dict[str, Any]) -> Iterable[tuple[str, str, str, dict[str, Any]]]:
    facts = companyfacts.get("facts", {}) or {}
    for namespace, concepts in facts.items():
        concepts = concepts or {}
        for concept_name, concept_obj in concepts.items():
            units = (concept_obj or {}).get("units", {}) or {}
            for unit, rows in units.items():
                for row in rows or []:
                    yield namespace, concept_name, unit, dict(row or {})


def _select_default_accession(rows: list[tuple[str, str, str, dict[str, Any]]]) -> str | None:
    by_accn: Counter[str] = Counter()
    latest_by_accn: dict[str, datetime] = {}
    for _, _, _, row in rows:
        accn = row.get("accn")
        if not accn:
            continue
        by_accn[accn] += 1
        filed = _parse_iso_date(row.get("filed"))
        if filed is None:
            continue
        prior = latest_by_accn.get(accn)
        if prior is None or filed > prior:
            latest_by_accn[accn] = filed
    if not by_accn:
        return None
    ranked = sorted(
        by_accn.keys(),
        key=lambda accn: (
            latest_by_accn.get(accn, datetime.min),
            by_accn[accn],
            accn,
        ),
        reverse=True,
    )
    return ranked[0]


def _context_signature(row: dict[str, Any]) -> tuple[str, str, str]:
    start = str(row.get("start") or "").strip()
    end = str(row.get("end") or "").strip()
    if start and end:
        return ("duration", start, end)
    if end:
        return ("instant", end, "")
    instant = str(row.get("instant") or "").strip()
    if instant:
        return ("instant", instant, "")
    return ("instant", "1970-01-01", "")


def companyfacts_to_filing(
    companyfacts: dict[str, Any],
    accession: str | None = None,
    form: str | None = None,
    max_facts: int | None = None,
) -> tuple[Filing, CompanyFactsSelection]:
    rows = list(_iter_fact_rows(companyfacts))
    total_count = len(rows)
    normalized_form = form.strip().upper() if form else None
    if normalized_form:
        rows = [row for row in rows if str(row[3].get("form", "")).upper() == normalized_form]

    selected_accession = accession.strip() if accession else _select_default_accession(rows)
    if selected_accession:
        rows = [row for row in rows if row[3].get("accn") == selected_accession]
    if max_facts is not None and max_facts > 0:
        rows = rows[:max_facts]

    context_ids: dict[tuple[str, str, str], str] = {}
    contexts: dict[str, Context] = {}
    facts: list[Fact] = []

    for idx, (namespace, concept_name, unit, row) in enumerate(rows, start=1):
        signature = _context_signature(row)
        context_id = context_ids.get(signature)
        if context_id is None:
            context_id = f"ctx-{len(context_ids) + 1:06d}"
            context_ids[signature] = context_id
            if signature[0] == "duration":
                contexts[context_id] = Context(
                    id=context_id,
                    period_type="duration",
                    start_date=signature[1],
                    end_date=signature[2],
                    dimensions={},
                )
            else:
                contexts[context_id] = Context(
                    id=context_id,
                    period_type="instant",
                    instant=signature[1],
                    dimensions={},
                )

        dimensions: dict[str, str] = {}
        if row.get("frame"):
            dimensions["frame"] = str(row["frame"])

        raw_decimals = row.get("dec")
        decimals: int | str | None = raw_decimals
        if isinstance(raw_decimals, str):
            try:
                decimals = int(raw_decimals)
            except ValueError:
                decimals = raw_decimals

        facts.append(
            Fact(
                id=f"fact-{idx:07d}",
                concept=f"{namespace}:{concept_name}",
                context_id=context_id,
                value=row.get("val"),
                unit=unit,
                decimals=decimals,
                dimensions=dimensions,
                source={
                    "accn": row.get("accn"),
                    "form": row.get("form"),
                    "filed": row.get("filed"),
                    "fy": row.get("fy"),
                    "fp": row.get("fp"),
                    "frame": row.get("frame"),
                },
            )
        )

    period_candidates: list[datetime] = []
    for _, _, _, row in rows:
        date = _parse_iso_date(row.get("end")) or _parse_iso_date(row.get("filed"))
        if date is not None:
            period_candidates.append(date)
    period_end = max(period_candidates).strftime("%Y-%m-%d") if period_candidates else None

    filing = Filing(
        accession=selected_accession,
        cik=normalize_cik(companyfacts.get("cik", "")) if companyfacts.get("cik") is not None else None,
        entity=companyfacts.get("entityName"),
        period_end=period_end,
        taxonomy=companyfacts.get("taxonomy") or "sec-companyfacts",
        contexts=contexts,
        facts=facts,
    )
    selection = CompanyFactsSelection(
        accession=selected_accession,
        form=normalized_form,
        selected_count=len(facts),
        total_count=total_count,
    )
    return filing, selection
