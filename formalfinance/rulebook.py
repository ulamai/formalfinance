from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .profiles import get_profile, list_profiles


@dataclass(frozen=True)
class RulebookEntry:
    rule_id: str
    category: str
    reference_family: str
    reference: dict[str, Any]
    description: str
    evidence_fields: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "reference_family": self.reference_family,
            "reference": self.reference,
            "description": self.description,
            "evidence_fields": self.evidence_fields,
        }


def _category_for_rule(rule_id: str) -> tuple[str, str]:
    if rule_id.startswith("ixbrl."):
        return (
            "inline_xbrl_structural",
            "SEC EDGAR XBRL Guide (Inline XBRL validations)",
        )
    if rule_id.startswith("taxonomy."):
        return (
            "custom_taxonomy_validation",
            "SEC EDGAR XBRL Guide (custom taxonomy checks)",
        )
    if rule_id.startswith("xbrl."):
        return (
            "xbrl_core_integrity",
            "XBRL 2.1 + EDGAR acceptance constraints",
        )
    if rule_id.startswith("dei."):
        return (
            "dei_metadata_consistency",
            "DEI taxonomy + SEC filing metadata requirements",
        )
    if rule_id.startswith("acct."):
        return (
            "accounting_consistency",
            "Issuer financial statement consistency checks",
        )
    return (
        "general_validation",
        "FormalFinance internal validation policy",
    )


REFERENCE_BY_PREFIX: dict[str, dict[str, Any]] = {
    "ixbrl.": {
        "document": "SEC EDGAR Filer Manual (Volume II)",
        "section": "Chapter 6.5.20 (Inline XBRL submission validations)",
        "version": "EDGAR Release 26.x (as reflected in current public guidance)",
        "as_of_date": "2026-02-27",
        "url": "https://www.sec.gov/info/edgar/specifications",
    },
    "taxonomy.": {
        "document": "SEC EDGAR Filer Manual (Volume II)",
        "section": "Chapter 6.5.26-6.5.28 (custom taxonomy and linkbase validations)",
        "version": "EDGAR Release 26.x (as reflected in current public guidance)",
        "as_of_date": "2026-02-27",
        "url": "https://www.sec.gov/info/edgar/specifications",
    },
    "xbrl.": {
        "document": "XBRL 2.1 Specification + SEC EDGAR XBRL acceptance constraints",
        "section": "XBRL 2.1 Sections 4-5 (facts, contexts, units, dimensions)",
        "version": "XBRL 2.1 Recommendation",
        "as_of_date": "2026-02-27",
        "url": "https://www.xbrl.org/specification/xbrl-2.1/",
    },
    "dei.": {
        "document": "SEC DEI taxonomy filing metadata requirements",
        "section": "Entity and document identifier consistency checks",
        "version": "Current SEC DEI taxonomy cycle",
        "as_of_date": "2026-02-27",
        "url": "https://www.sec.gov/structureddata",
    },
    "acct.": {
        "document": "FormalFinance accounting consistency policy",
        "section": "Mechanical arithmetic and period-type sanity checks",
        "version": "formalfinance.policy.v0",
        "as_of_date": "2026-02-27",
        "url": "https://github.com/ulamai/formalfinance",
    },
}


def reference_for_rule(rule_id: str) -> dict[str, Any]:
    for prefix, reference in REFERENCE_BY_PREFIX.items():
        if rule_id.startswith(prefix):
            return dict(reference)
    return {
        "document": "FormalFinance internal validation policy",
        "section": "General checks",
        "version": "formalfinance.policy.v0",
        "as_of_date": "2026-02-27",
        "url": "https://github.com/ulamai/formalfinance",
    }


def rule_provenance_map(rule_ids: list[str]) -> dict[str, dict[str, Any]]:
    return {rule_id: reference_for_rule(rule_id) for rule_id in sorted(set(rule_ids))}


def _evidence_fields_for_rule(rule_id: str) -> list[str]:
    fields = ["rule_id", "severity", "message"]
    if rule_id.startswith(("xbrl.", "acct.", "ixbrl.")):
        fields.extend(["fact_ids", "details"])
    if rule_id.startswith("taxonomy."):
        fields.extend(["details"])
    return fields


def build_rulebook(profile: str = "ixbrl-gating") -> dict[str, Any]:
    rules = get_profile(profile)
    entries: list[RulebookEntry] = []
    for rule in rules:
        category, reference_family = _category_for_rule(rule.rule_id)
        entries.append(
            RulebookEntry(
                rule_id=rule.rule_id,
                category=category,
                reference_family=reference_family,
                reference=reference_for_rule(rule.rule_id),
                description=rule.description,
                evidence_fields=_evidence_fields_for_rule(rule.rule_id),
            )
        )
    return {
        "schema_version": "formalfinance.rulebook.v0",
        "profile": profile,
        "rule_count": len(entries),
        "rules": [entry.as_dict() for entry in entries],
    }


def build_global_rulebook() -> dict[str, Any]:
    seen: dict[str, RulebookEntry] = {}
    for profile in list_profiles():
        for rule in profile.rules:
            if rule.rule_id in seen:
                continue
            category, reference_family = _category_for_rule(rule.rule_id)
            seen[rule.rule_id] = RulebookEntry(
                rule_id=rule.rule_id,
                category=category,
                reference_family=reference_family,
                reference=reference_for_rule(rule.rule_id),
                description=rule.description,
                evidence_fields=_evidence_fields_for_rule(rule.rule_id),
            )
    entries = [seen[key] for key in sorted(seen)]
    return {
        "schema_version": "formalfinance.rulebook.v0",
        "profile": "all",
        "rule_count": len(entries),
        "rules": [entry.as_dict() for entry in entries],
    }
