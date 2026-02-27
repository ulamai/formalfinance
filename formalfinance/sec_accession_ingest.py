from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
import json
import re
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from .models import Filing
from .sec_discovery import fetch_submissions
from .sec_ingest import companyfacts_to_filing, fetch_companyfacts_json, normalize_cik


RESERVED_PREFIXES = {"us-gaap", "dei", "ifrs-full", "xbrli", "link", "xlink", "iso4217"}
DISALLOWED_TAGS = ("script", "iframe", "object", "embed")
EXT_REF_RE = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
NS_RE = re.compile(r"""xmlns:([A-Za-z_][A-Za-z0-9_.-]*)=["']([^"']+)["']""")
TARGET_NS_RE = re.compile(r"""targetNamespace=["']([^"']+)["']""")
XSD_ELEMENT_RE = re.compile(r"""<\s*(?:xs|xsd):element\b[^>]*\bname=["']([^"']+)["']""", re.IGNORECASE)
IX_HEADER_RE = re.compile(r"<\s*ix:header\b", re.IGNORECASE)
IX_FACT_RE = re.compile(r"<\s*ix:(?:nonfraction|nonnumeric|fraction)\b", re.IGNORECASE)


def normalize_accession(accession: str) -> str:
    raw = str(accession or "").strip()
    if not raw:
        raise ValueError("Accession cannot be empty.")
    if re.match(r"^\d{10}-\d{2}-\d{6}$", raw):
        return raw
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 18:
        raise ValueError(f"Invalid accession format '{accession}'.")
    return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"


def accession_no_dash(accession: str) -> str:
    return normalize_accession(accession).replace("-", "")


def _cik_archive_fragment(cik: str | int) -> str:
    return str(int(normalize_cik(cik)))


def _fetch_json(url: str, user_agent: str, timeout_seconds: int = 30) -> dict[str, Any]:
    req = Request(
        url,
        headers={"User-Agent": user_agent.strip(), "Accept": "application/json"},
        method="GET",
    )
    with urlopen(req, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _fetch_text(url: str, user_agent: str, timeout_seconds: int = 30, max_bytes: int = 1_000_000) -> str:
    req = Request(url, headers={"User-Agent": user_agent.strip(), "Accept": "*/*"}, method="GET")
    with urlopen(req, timeout=timeout_seconds) as response:
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    return raw.decode("utf-8", errors="replace")


def filing_base_url(cik: str | int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{_cik_archive_fragment(cik)}/{accession_no_dash(accession)}/"


def fetch_filing_index_json(
    cik: str | int,
    accession: str,
    user_agent: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    url = filing_base_url(cik, accession) + "index.json"
    return _fetch_json(url, user_agent=user_agent, timeout_seconds=timeout_seconds)


def _index_items(index_json: dict[str, Any]) -> list[dict[str, Any]]:
    directory = index_json.get("directory", {}) or {}
    items = directory.get("item", []) or []
    return [dict(item) for item in items if isinstance(item, dict)]


def _is_html(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".htm") or lowered.endswith(".html")


def _is_xml(name: str) -> bool:
    return name.lower().endswith(".xml")


def _is_xsd(name: str) -> bool:
    return name.lower().endswith(".xsd")


def _submission_row(submissions: dict[str, Any], accession: str) -> dict[str, Any] | None:
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    accessions = recent.get("accessionNumber", []) or []
    if not isinstance(accessions, list):
        return None
    for idx, accn in enumerate(accessions):
        if str(accn) != accession:
            continue
        row: dict[str, Any] = {}
        for key, value in recent.items():
            if isinstance(value, list) and idx < len(value):
                row[key] = value[idx]
        return row
    return None


@dataclass(frozen=True)
class IngestionMetadata:
    cik: str
    accession: str
    base_url: str
    form: str | None
    filing_date: str | None
    used_companyfacts: bool
    attachment_count: int
    scanned_documents: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "formalfinance.accession_ingest_meta.v0",
            "cik": self.cik,
            "accession": self.accession,
            "base_url": self.base_url,
            "form": self.form,
            "filing_date": self.filing_date,
            "used_companyfacts": self.used_companyfacts,
            "attachment_count": self.attachment_count,
            "scanned_documents": self.scanned_documents,
        }


def _detect_disallowed_tags(text: str) -> list[str]:
    lowered = text.lower()
    return sorted([tag for tag in DISALLOWED_TAGS if f"<{tag}" in lowered])


def _detect_external_refs(text: str) -> list[str]:
    refs: list[str] = []
    for match in EXT_REF_RE.finditer(text):
        url = match.group(1).strip()
        if url.lower().startswith(("http://", "https://")):
            refs.append(url)
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    return deduped[:50]


def _doc_ix_metadata(name: str, text: str) -> dict[str, Any]:
    return {
        "filename": name,
        "is_inline_xbrl": bool(IX_HEADER_RE.search(text) or IX_FACT_RE.search(text)),
        "contains_ix_header": bool(IX_HEADER_RE.search(text)),
        "xbrl_errors": [],
        "disallowed_html_tags": _detect_disallowed_tags(text),
        "external_references": _detect_external_refs(text),
    }


def _extract_namespaces_from_xml(text: str) -> dict[str, str]:
    namespaces: dict[str, str] = {}
    for prefix, uri in NS_RE.findall(text):
        namespaces[prefix] = uri
    return namespaces


def _taxonomy_from_xsd_docs(
    *,
    xsd_docs: list[tuple[str, str]],
    fact_concepts: set[str],
) -> dict[str, Any]:
    namespaces: dict[str, str] = {}
    elements: dict[str, dict[str, Any]] = {}
    labels: list[dict[str, Any]] = []

    for filename, text in xsd_docs:
        ns_map = _extract_namespaces_from_xml(text)
        namespaces.update(ns_map)
        target_ns_match = TARGET_NS_RE.search(text)
        target_ns = target_ns_match.group(1).strip() if target_ns_match else ""
        target_prefix = ""
        if target_ns:
            for prefix, uri in ns_map.items():
                if uri == target_ns:
                    target_prefix = prefix
                    break
        if not target_prefix:
            target_prefix = PurePosixPath(filename).stem.split("_")[0].replace("-", "_")
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", target_prefix):
                target_prefix = "cust"
            if target_ns and target_prefix not in namespaces:
                namespaces[target_prefix] = target_ns

        for element_name in XSD_ELEMENT_RE.findall(text):
            concept = f"{target_prefix}:{element_name}"
            if concept not in elements:
                elements[concept] = {
                    "concept": concept,
                    "is_custom": target_prefix not in RESERVED_PREFIXES,
                }
                labels.append(
                    {
                        "concept": concept,
                        "role": "http://www.xbrl.org/2003/role/label",
                        "text": element_name.replace("_", " "),
                    }
                )

    for concept in sorted(fact_concepts):
        if concept in elements:
            continue
        if ":" not in concept:
            continue
        prefix = concept.split(":", 1)[0]
        elements[concept] = {
            "concept": concept,
            "is_custom": prefix not in RESERVED_PREFIXES,
        }
        labels.append(
            {
                "concept": concept,
                "role": "http://www.xbrl.org/2003/role/label",
                "text": concept.split(":", 1)[1].replace("_", " "),
            }
        )

    namespace_rows = [
        {"prefix": prefix, "uri": uri, "is_standard": prefix in RESERVED_PREFIXES}
        for prefix, uri in sorted(namespaces.items())
    ]
    return {
        "namespaces": namespace_rows,
        "elements": [elements[key] for key in sorted(elements)],
        "labels": labels,
        "relationships": [],
    }


def ingest_accession_to_filing(
    *,
    cik: str | int,
    accession: str,
    user_agent: str,
    timeout_seconds: int = 30,
    include_companyfacts: bool = True,
    max_scan_docs: int = 25,
    max_doc_scan_bytes: int = 1_000_000,
) -> tuple[Filing, IngestionMetadata]:
    if not user_agent.strip():
        raise ValueError("A non-empty SEC-compliant User-Agent is required.")

    cik10 = normalize_cik(cik)
    accession_norm = normalize_accession(accession)
    base_url = filing_base_url(cik10, accession_norm)

    submissions = fetch_submissions(cik10, user_agent=user_agent, timeout_seconds=timeout_seconds)
    row = _submission_row(submissions, accession_norm) or {}
    index_json = fetch_filing_index_json(cik10, accession_norm, user_agent=user_agent, timeout_seconds=timeout_seconds)
    items = _index_items(index_json)

    if include_companyfacts:
        companyfacts = fetch_companyfacts_json(cik10, user_agent=user_agent, timeout_seconds=timeout_seconds)
        base_filing, _selection = companyfacts_to_filing(companyfacts, accession=accession_norm)
    else:
        base_filing = Filing(
            accession=accession_norm,
            cik=cik10,
            entity=submissions.get("name"),
            period_end=None,
            taxonomy="sec-accession",
            contexts={},
            facts=[],
            ixbrl={},
            taxonomy_package={},
        )

    primary_name = str(row.get("primaryDocument") or "").strip()
    html_candidates = [item for item in items if _is_html(str(item.get("name", "")))]
    if not primary_name and html_candidates:
        primary_name = str(html_candidates[0].get("name", ""))

    scanned = 0
    primary_doc: dict[str, Any] | None = None
    attachments: list[dict[str, Any]] = []
    xsd_docs: list[tuple[str, str]] = []

    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        size_raw = item.get("size")
        try:
            size_value = int(size_raw) if size_raw is not None else None
        except (TypeError, ValueError):
            size_value = None

        text: str | None = None
        should_scan = scanned < max_scan_docs and (_is_html(name) or _is_xsd(name))
        if should_scan:
            try:
                text = _fetch_text(
                    base_url + name,
                    user_agent=user_agent,
                    timeout_seconds=timeout_seconds,
                    max_bytes=max_doc_scan_bytes,
                )
                scanned += 1
            except Exception:
                text = None

        if _is_xsd(name) and text is not None:
            xsd_docs.append((name, text))

        if _is_html(name):
            if text is not None:
                doc_meta = _doc_ix_metadata(name, text)
            else:
                doc_meta = {
                    "filename": name,
                    "is_inline_xbrl": name.lower().endswith(".htm"),
                    "contains_ix_header": None,
                    "xbrl_errors": [],
                    "disallowed_html_tags": [],
                    "external_references": [],
                }
            if size_value is not None:
                doc_meta["size_bytes"] = size_value
            if name == primary_name:
                primary_doc = doc_meta
            else:
                attachments.append(doc_meta)
            continue

        if _is_xml(name):
            attach = {
                "filename": name,
                "is_inline_xbrl": False,
                "contains_ix_header": False,
                "xbrl_errors": [],
                "disallowed_html_tags": [],
                "external_references": [],
            }
            if size_value is not None:
                attach["size_bytes"] = size_value
            attachments.append(attach)

    if primary_doc is None:
        primary_doc = {
            "filename": primary_name or "primary_document",
            "is_inline_xbrl": bool(primary_name and _is_html(primary_name)),
            "contains_ix_header": None,
            "xbrl_errors": [],
            "disallowed_html_tags": [],
            "external_references": [],
        }

    fact_concepts = {fact.concept for fact in base_filing.facts}
    taxonomy_package = _taxonomy_from_xsd_docs(xsd_docs=xsd_docs, fact_concepts=fact_concepts)

    filing = Filing(
        accession=base_filing.accession or accession_norm,
        cik=base_filing.cik or cik10,
        entity=base_filing.entity or submissions.get("name"),
        period_end=base_filing.period_end or row.get("reportDate") or row.get("filingDate"),
        taxonomy=base_filing.taxonomy or "sec-accession",
        contexts=base_filing.contexts,
        facts=base_filing.facts,
        ixbrl={
            "submission_type": row.get("form"),
            "document_period_end_date": row.get("reportDate"),
            "primary_document": primary_doc,
            "attachments": attachments,
            "disallowed_html_tags": [],
            "external_references": [],
        },
        taxonomy_package=taxonomy_package,
    )

    metadata = IngestionMetadata(
        cik=cik10,
        accession=accession_norm,
        base_url=base_url,
        form=row.get("form"),
        filing_date=row.get("filingDate"),
        used_companyfacts=include_companyfacts,
        attachment_count=len(items),
        scanned_documents=scanned,
    )
    return filing, metadata
