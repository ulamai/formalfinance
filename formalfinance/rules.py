from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence
import math

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
            if isinstance(fact.decimals, str) and fact.decimals.upper() == "INF":
                continue
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
            if str(prior.value) != str(fact.value):
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
