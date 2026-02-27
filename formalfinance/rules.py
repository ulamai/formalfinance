from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, Sequence
import math
import re

from .models import Fact, Filing


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    message: str
    fact_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class Rule(Protocol):
    rule_id: str
    description: str

    def run(self, filing: Filing) -> list[Finding]:
        ...


QNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*:[A-Za-z_][A-Za-z0-9_.-]*$")
DATE_FMT = "%Y-%m-%d"

INSTANT_CONCEPT_HINTS = (
    "assets",
    "liabilities",
    "equity",
    "sharesoutstanding",
    "cashandcashequivalents",
)

DURATION_CONCEPT_HINTS = (
    "revenue",
    "sales",
    "netincome",
    "cashflow",
    "operatingincome",
    "earningspershare",
)


def _parse_date(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.strptime(raw, DATE_FMT)
    except ValueError:
        return None


@dataclass(frozen=True)
class ContextReferenceRule:
    rule_id: str = "ixbrl.context_reference_exists"
    description: str = "Each fact must reference a known context."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        for fact in filing.facts:
            if fact.context_id not in filing.contexts:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Fact {fact.id} references unknown context '{fact.context_id}'.",
                        fact_ids=[fact.id],
                    )
                )
        return findings


@dataclass(frozen=True)
class ContextPeriodSemanticsRule:
    rule_id: str = "xbrl.context_period_semantics"
    description: str = "Context period fields must be internally consistent and date-valid."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        for context in filing.contexts.values():
            if context.period_type == "instant":
                if not context.instant:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            severity="error",
                            message=f"Instant context '{context.id}' is missing instant date.",
                        )
                    )
                    continue
                if _parse_date(context.instant) is None:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            severity="error",
                            message=f"Context '{context.id}' has invalid instant date '{context.instant}'.",
                        )
                    )
                if context.start_date or context.end_date:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            severity="warning",
                            message=f"Instant context '{context.id}' should not define start/end dates.",
                        )
                    )
                continue

            if context.period_type == "duration":
                if not context.start_date or not context.end_date:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            severity="error",
                            message=(
                                f"Duration context '{context.id}' must define both start_date and end_date."
                            ),
                        )
                    )
                    continue
                start = _parse_date(context.start_date)
                end = _parse_date(context.end_date)
                if start is None or end is None:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            severity="error",
                            message=(
                                f"Context '{context.id}' has invalid start/end dates "
                                f"('{context.start_date}', '{context.end_date}')."
                            ),
                        )
                    )
                    continue
                if end < start:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            severity="error",
                            message=(
                                f"Context '{context.id}' has end_date before start_date "
                                f"('{context.start_date}' > '{context.end_date}')."
                            ),
                        )
                    )
                if context.instant:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            severity="warning",
                            message=f"Duration context '{context.id}' should not define instant date.",
                        )
                    )
                continue

            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    severity="error",
                    message=f"Context '{context.id}' has unsupported period_type '{context.period_type}'.",
                )
            )
        return findings


@dataclass(frozen=True)
class ConceptQNameRule:
    rule_id: str = "xbrl.concept_qname"
    description: str = "Fact concepts must be QName-like prefix:name identifiers."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        for fact in filing.facts:
            if not QNAME_RE.match(fact.concept):
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Fact {fact.id} has invalid concept QName '{fact.concept}'.",
                        fact_ids=[fact.id],
                    )
                )
        return findings


@dataclass(frozen=True)
class NumericFactsHaveUnitsRule:
    rule_id: str = "ixbrl.numeric_fact_unit"
    description: str = "Numeric facts must define a unit."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        for fact in filing.facts:
            if fact.numeric_value() is not None and not fact.unit:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Numeric fact {fact.id} ({fact.concept}) is missing unit.",
                        fact_ids=[fact.id],
                    )
                )
        return findings


@dataclass(frozen=True)
class DecimalsFormatRule:
    rule_id: str = "ixbrl.decimals_format"
    description: str = "Decimals must be integer or INF on numeric facts."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        for fact in filing.facts:
            if fact.numeric_value() is None:
                continue
            if fact.decimals is None:
                continue
            if isinstance(fact.decimals, int):
                continue
            if isinstance(fact.decimals, str):
                if fact.decimals.upper() == "INF":
                    continue
                try:
                    int(fact.decimals)
                    continue
                except ValueError:
                    pass
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    severity="error",
                    message=f"Fact {fact.id} has invalid decimals '{fact.decimals}'.",
                    fact_ids=[fact.id],
                )
            )
        return findings


@dataclass(frozen=True)
class NumericFactsFiniteRule:
    rule_id: str = "xbrl.numeric_fact_finite"
    description: str = "Numeric fact values must be finite (not NaN/Infinity)."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        for fact in filing.facts:
            value = fact.numeric_value()
            if value is None:
                continue
            if math.isnan(value) or math.isinf(value):
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Fact {fact.id} has non-finite numeric value '{fact.value}'.",
                        fact_ids=[fact.id],
                    )
                )
        return findings


def _value_tolerance_for_fact(fact: Fact) -> float:
    if fact.decimals is None:
        return 0.0
    if isinstance(fact.decimals, str):
        if fact.decimals.upper() == "INF":
            return 0.0
        try:
            decimals = int(fact.decimals)
        except ValueError:
            return 0.0
    else:
        decimals = fact.decimals
    return 0.5 * (10 ** (-decimals))


def _value_matches(prior: Fact, current: Fact) -> bool:
    prior_num = prior.numeric_value()
    current_num = current.numeric_value()
    if prior_num is not None and current_num is not None:
        tol = _value_tolerance_for_fact(prior) + _value_tolerance_for_fact(current)
        return abs(prior_num - current_num) <= tol
    return str(prior.value) == str(current.value)


@dataclass(frozen=True)
class DuplicateFactConflictRule:
    rule_id: str = "xbrl.duplicate_fact_conflict"
    description: str = "Duplicate facts with same key should not disagree on value."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        seen: dict[tuple[str, str, str, tuple[tuple[str, str], ...]], Fact] = {}
        for fact in filing.facts:
            key = fact.canonical_key()
            prior = seen.get(key)
            if prior is None:
                seen[key] = fact
                continue
            if not _value_matches(prior, fact):
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=(
                            f"Facts {prior.id} and {fact.id} share concept/context/unit/dimensions "
                            "but have different values."
                        ),
                        fact_ids=[prior.id, fact.id],
                        details={"prior_value": prior.value, "new_value": fact.value},
                    )
                )
        return findings


@dataclass(frozen=True)
class FactDimensionConsistencyRule:
    rule_id: str = "xbrl.dimension_context_consistency"
    description: str = "Fact dimensions should not conflict with context dimensions."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        for fact in filing.facts:
            context = filing.contexts.get(fact.context_id)
            if context is None:
                continue
            for dim, value in fact.dimensions.items():
                if dim in context.dimensions and context.dimensions[dim] != value:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            severity="error",
                            message=(
                                f"Fact {fact.id} dimension '{dim}' conflicts with its context "
                                f"('{value}' vs '{context.dimensions[dim]}')."
                            ),
                            fact_ids=[fact.id],
                            details={"dimension": dim},
                        )
                    )
        return findings


@dataclass(frozen=True)
class UnitConsistencyByConceptRule:
    rule_id: str = "xbrl.unit_consistency"
    description: str = "Numeric facts for same concept/context/dimensions should use a consistent unit."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        seen: dict[tuple[str, str, tuple[tuple[str, str], ...]], Fact] = {}
        for fact in filing.facts:
            if fact.numeric_value() is None:
                continue
            key = (fact.concept, fact.context_id, tuple(sorted(fact.dimensions.items())))
            prior = seen.get(key)
            if prior is None:
                seen[key] = fact
                continue
            if (prior.unit or "") != (fact.unit or ""):
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=(
                            f"Facts {prior.id} and {fact.id} use different units for the same concept/context "
                            f"('{prior.unit}' vs '{fact.unit}')."
                        ),
                        fact_ids=[prior.id, fact.id],
                    )
                )
        return findings


@dataclass(frozen=True)
class RequiredConceptsRule:
    required_concepts: Sequence[str]
    rule_id: str = "ixbrl.required_concepts"
    description: str = "A minimum set of key DEI concepts must be present."

    def run(self, filing: Filing) -> list[Finding]:
        present = {fact.concept for fact in filing.facts}
        missing = sorted([concept for concept in self.required_concepts if concept not in present])
        if not missing:
            return []
        return [
            Finding(
                rule_id=self.rule_id,
                severity="error",
                message="Required concepts are missing.",
                details={"missing": missing},
            )
        ]


@dataclass(frozen=True)
class FilingMetadataConsistencyRule:
    rule_id: str = "dei.metadata_consistency"
    description: str = "Top-level filing metadata should match DEI fact values when both are present."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        cik_values = [
            str(f.value).strip()
            for f in filing.facts
            if f.concept == "dei:EntityCentralIndexKey" and f.value is not None
        ]
        entity_values = [
            str(f.value).strip()
            for f in filing.facts
            if f.concept == "dei:EntityRegistrantName" and f.value is not None
        ]

        filing_cik = (filing.cik or "").lstrip("0")
        if filing_cik and cik_values:
            mismatched = [val for val in cik_values if val.lstrip("0") != filing_cik]
            if mismatched:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message="Top-level CIK does not match DEI CIK fact values.",
                        details={"filing_cik": filing.cik, "dei_cik_values": sorted(set(cik_values))},
                    )
                )

        filing_entity = (filing.entity or "").strip().lower()
        if filing_entity and entity_values:
            mismatched_entity = [val for val in entity_values if val.strip().lower() != filing_entity]
            if mismatched_entity:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="warning",
                        message="Top-level entity name does not exactly match DEI entity name fact values.",
                        details={
                            "filing_entity": filing.entity,
                            "dei_entity_values": sorted(set(entity_values)),
                        },
                    )
                )
        return findings


@dataclass(frozen=True)
class ConceptPeriodTypeHeuristicRule:
    rule_id: str = "acct.concept_period_type_heuristic"
    description: str = "Common balance-sheet concepts are typically instant; flow concepts are typically duration."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        for fact in filing.facts:
            context = filing.contexts.get(fact.context_id)
            if context is None:
                continue
            local_name = fact.concept.split(":", 1)[-1].replace("_", "").lower()
            if any(hint in local_name for hint in INSTANT_CONCEPT_HINTS) and context.period_type != "instant":
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="warning",
                        message=(
                            f"Fact {fact.id} ({fact.concept}) appears instant-like but uses "
                            f"'{context.period_type}' context '{context.id}'."
                        ),
                        fact_ids=[fact.id],
                    )
                )
            if any(hint in local_name for hint in DURATION_CONCEPT_HINTS) and context.period_type != "duration":
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="warning",
                        message=(
                            f"Fact {fact.id} ({fact.concept}) appears duration-like but uses "
                            f"'{context.period_type}' context '{context.id}'."
                        ),
                        fact_ids=[fact.id],
                    )
                )
        return findings


@dataclass(frozen=True)
class NegativeAssetsWarningRule:
    rule_id: str = "acct.assets_negative"
    description: str = "Assets are generally non-negative; flag obvious outliers."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        for fact in filing.facts:
            if fact.concept not in {"us-gaap:Assets", "ifrs-full:Assets"}:
                continue
            value = fact.numeric_value()
            if value is None:
                continue
            if value < 0:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="warning",
                        message=f"Assets fact {fact.id} is negative ({value}).",
                        fact_ids=[fact.id],
                    )
                )
        return findings


def _rounding_tolerance(decimals: int | str | None) -> float:
    if decimals is None:
        return 0.0
    if isinstance(decimals, str):
        if decimals.upper() == "INF":
            return 0.0
        try:
            decimals = int(decimals)
        except ValueError:
            return 0.0
    return 0.5 * (10 ** (-decimals))


def _best_fact_by_context(facts: list[Fact], concepts: set[str]) -> dict[str, Fact]:
    output: dict[str, Fact] = {}
    for fact in facts:
        if fact.concept not in concepts:
            continue
        if fact.numeric_value() is None:
            continue
        prior = output.get(fact.context_id)
        if prior is None:
            output[fact.context_id] = fact
            continue
        prior_score = abs(prior.numeric_value() or 0.0)
        this_score = abs(fact.numeric_value() or 0.0)
        if this_score >= prior_score:
            output[fact.context_id] = fact
    return output


@dataclass(frozen=True)
class BalanceSheetEquationRule:
    assets_concepts: set[str] = field(
        default_factory=lambda: {"us-gaap:Assets", "ifrs-full:Assets"}
    )
    liabilities_concepts: set[str] = field(
        default_factory=lambda: {"us-gaap:Liabilities", "ifrs-full:Liabilities"}
    )
    equity_concepts: set[str] = field(
        default_factory=lambda: {
            "us-gaap:StockholdersEquity",
            "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "ifrs-full:Equity",
        }
    )
    rule_id: str = "acct.balance_sheet_equation"
    description: str = "Assets should equal Liabilities plus Equity within rounding tolerance."

    def run(self, filing: Filing) -> list[Finding]:
        findings: list[Finding] = []
        assets = _best_fact_by_context(filing.facts, self.assets_concepts)
        liabilities = _best_fact_by_context(filing.facts, self.liabilities_concepts)
        equity = _best_fact_by_context(filing.facts, self.equity_concepts)

        common_contexts = sorted(set(assets) & set(liabilities) & set(equity))
        for context_id in common_contexts:
            a = assets[context_id]
            l = liabilities[context_id]
            e = equity[context_id]
            a_val = float(a.numeric_value() or 0.0)
            l_val = float(l.numeric_value() or 0.0)
            e_val = float(e.numeric_value() or 0.0)
            diff = abs(a_val - (l_val + e_val))
            tol = _rounding_tolerance(a.decimals) + _rounding_tolerance(l.decimals) + _rounding_tolerance(
                e.decimals
            )
            if math.isnan(diff) or diff > tol:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=(
                            f"Balance sheet equation mismatch in context '{context_id}': "
                            f"assets={a_val}, liabilities={l_val}, equity={e_val}."
                        ),
                        fact_ids=[a.id, l.id, e.id],
                        details={"difference": diff, "tolerance": tol},
                    )
                )
        return findings
