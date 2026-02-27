from __future__ import annotations

from .rules import (
    BalanceSheetEquationRule,
    ContextReferenceRule,
    DecimalsFormatRule,
    DuplicateFactConflictRule,
    NumericFactsHaveUnitsRule,
    RequiredConceptsRule,
    Rule,
)


REQUIRED_DEI_CONCEPTS = (
    "dei:DocumentType",
    "dei:EntityRegistrantName",
    "dei:EntityCentralIndexKey",
)


def get_profile(profile_name: str) -> list[Rule]:
    profile = profile_name.strip().lower()
    base_rules: list[Rule] = [
        ContextReferenceRule(),
        NumericFactsHaveUnitsRule(),
        DecimalsFormatRule(),
        DuplicateFactConflictRule(),
        RequiredConceptsRule(required_concepts=REQUIRED_DEI_CONCEPTS),
    ]

    if profile in {"ixbrl-gating", "ixbrl_gating"}:
        return base_rules
    if profile in {"fsd-consistency", "fsd_consistency"}:
        return [*base_rules, BalanceSheetEquationRule()]

    raise ValueError(
        f"Unknown profile '{profile_name}'. Expected one of: ixbrl-gating, fsd-consistency."
    )
