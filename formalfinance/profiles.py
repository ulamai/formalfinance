from __future__ import annotations

from dataclasses import dataclass

from .rules_ixbrl import (
    InlineAttachmentHtmlRule,
    InlineMetadataPresenceRule,
    InlineNoDisallowedHtmlRule,
    InlineNoExternalReferencesRule,
    InlinePrimaryDocumentRule,
    InlineXbrlErrorSuspensionRiskRule,
)
from .rules import (
    BalanceSheetEquationRule,
    ConceptPeriodTypeHeuristicRule,
    ConceptQNameRule,
    ContextPeriodSemanticsRule,
    ContextReferenceRule,
    DecimalsFormatRule,
    DuplicateFactConflictRule,
    FactDimensionConsistencyRule,
    FilingMetadataConsistencyRule,
    NegativeAssetsWarningRule,
    NumericFactsFiniteRule,
    NumericFactsHaveUnitsRule,
    RequiredConceptsRule,
    Rule,
    UnitConsistencyByConceptRule,
)
from .rules_taxonomy import (
    TaxonomyCalculationNoCycleRule,
    TaxonomyCustomConceptRelationshipRule,
    TaxonomyLabelRules,
    TaxonomyMetadataPresenceRule,
    TaxonomyNamespacePrefixRule,
    TaxonomyRelationshipTargetExistsRule,
)


REQUIRED_DEI_CONCEPTS = (
    "dei:DocumentType",
    "dei:EntityRegistrantName",
    "dei:EntityCentralIndexKey",
)


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    rules: list[Rule]


def _core_structural_rules() -> list[Rule]:
    return [
        ContextReferenceRule(),
        ContextPeriodSemanticsRule(),
        ConceptQNameRule(),
        FactDimensionConsistencyRule(),
        NumericFactsHaveUnitsRule(),
        DecimalsFormatRule(),
        NumericFactsFiniteRule(),
        UnitConsistencyByConceptRule(),
        DuplicateFactConflictRule(),
        FilingMetadataConsistencyRule(),
    ]


def _ixbrl_preflight_rules() -> list[Rule]:
    return [
        InlineMetadataPresenceRule(),
        InlinePrimaryDocumentRule(),
        InlineAttachmentHtmlRule(),
        InlineNoDisallowedHtmlRule(),
        InlineNoExternalReferencesRule(),
        InlineXbrlErrorSuspensionRiskRule(),
    ]


def _taxonomy_rules() -> list[Rule]:
    return [
        TaxonomyMetadataPresenceRule(),
        TaxonomyNamespacePrefixRule(),
        TaxonomyLabelRules(),
        TaxonomyRelationshipTargetExistsRule(),
        TaxonomyCalculationNoCycleRule(),
        TaxonomyCustomConceptRelationshipRule(),
    ]


def _ixbrl_gating_rules() -> list[Rule]:
    return [
        *_core_structural_rules(),
        *_ixbrl_preflight_rules(),
        *_taxonomy_rules(),
        RequiredConceptsRule(required_concepts=REQUIRED_DEI_CONCEPTS),
    ]


def _fsd_consistency_rules() -> list[Rule]:
    return [
        *_ixbrl_gating_rules(),
        ConceptPeriodTypeHeuristicRule(),
        NegativeAssetsWarningRule(),
        BalanceSheetEquationRule(),
    ]


def _companyfacts_consistency_rules() -> list[Rule]:
    return [
        *_core_structural_rules(),
        ConceptPeriodTypeHeuristicRule(),
        NegativeAssetsWarningRule(),
        BalanceSheetEquationRule(),
    ]


PROFILE_BUILDERS = {
    "ixbrl-gating": (
        "EDGAR/iXBRL-style preflight gate with inline attachment checks, taxonomy checks, and required DEI concepts.",
        _ixbrl_gating_rules,
    ),
    "fsd-consistency": (
        "Structural gating plus accounting consistency checks (balance-sheet equation and heuristics).",
        _fsd_consistency_rules,
    ),
    "companyfacts-consistency": (
        "Profile tuned for SEC companyfacts-derived filings, without strict DocumentType requirement.",
        _companyfacts_consistency_rules,
    ),
}


ALIASES = {
    "ixbrl_gating": "ixbrl-gating",
    "fsd_consistency": "fsd-consistency",
    "companyfacts_consistency": "companyfacts-consistency",
}


def normalize_profile_name(profile_name: str) -> str:
    key = profile_name.strip().lower()
    return ALIASES.get(key, key)


def get_profile(profile_name: str) -> list[Rule]:
    profile = normalize_profile_name(profile_name)
    if profile not in PROFILE_BUILDERS:
        valid = ", ".join(sorted(PROFILE_BUILDERS))
        raise ValueError(f"Unknown profile '{profile_name}'. Expected one of: {valid}.")
    _, builder = PROFILE_BUILDERS[profile]
    return builder()


def list_profiles() -> list[Profile]:
    profiles: list[Profile] = []
    for name in sorted(PROFILE_BUILDERS):
        description, builder = PROFILE_BUILDERS[name]
        profiles.append(Profile(name=name, description=description, rules=builder()))
    return profiles
