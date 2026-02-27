from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

from .models import Filing
from .rules import Finding, Rule


PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
RESERVED_STANDARD_PREFIXES = {"us-gaap", "dei", "ifrs-full", "xbrli", "link", "xlink", "iso4217"}
DEFAULT_LABEL_MAX_LEN = 511


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _taxonomy(filing: Filing) -> dict[str, Any]:
    value = filing.taxonomy_package or {}
    return value if isinstance(value, dict) else {}


def _relationships(package: dict[str, Any]) -> list[dict[str, Any]]:
    rows = package.get("relationships", [])
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _namespace_rows(package: dict[str, Any]) -> list[dict[str, Any]]:
    rows = package.get("namespaces", [])
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _element_concept(element: dict[str, Any]) -> str:
    if isinstance(element.get("concept"), str):
        return element["concept"].strip()
    prefix = str(element.get("prefix") or "").strip()
    name = str(element.get("name") or "").strip()
    if prefix and name:
        return f"{prefix}:{name}"
    return ""


def _element_rows(package: dict[str, Any]) -> list[dict[str, Any]]:
    rows = package.get("elements", [])
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        concept = _element_concept(row)
        if not concept:
            continue
        row = dict(row)
        row["concept"] = concept
        out.append(row)
    return out


def _label_rows(package: dict[str, Any]) -> list[dict[str, Any]]:
    rows = package.get("labels", [])
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        concept = str(row.get("concept") or "").strip()
        if not concept:
            continue
        out.append(dict(row))
    return out


@dataclass(frozen=True)
class TaxonomyMetadataPresenceRule:
    rule_id: str = "taxonomy.metadata_presence"
    description: str = "Taxonomy validation requires explicit taxonomy_package metadata."

    def run(self, filing: Filing) -> list[Finding]:
        package = _taxonomy(filing)
        if package:
            return []
        return [
            Finding(
                rule_id=self.rule_id,
                severity="error",
                message="Filing is missing `taxonomy_package` metadata required for taxonomy checks.",
            )
        ]


@dataclass(frozen=True)
class TaxonomyNamespacePrefixRule:
    rule_id: str = "taxonomy.namespace_prefix_consistency"
    description: str = "Namespace prefix mappings should be well-formed and non-conflicting."

    def run(self, filing: Filing) -> list[Finding]:
        package = _taxonomy(filing)
        if not package:
            return []
        findings: list[Finding] = []
        seen_prefix: dict[str, str] = {}
        seen_uri: dict[str, str] = {}
        for row in _namespace_rows(package):
            prefix = str(row.get("prefix") or "").strip()
            uri = str(row.get("uri") or "").strip()
            is_standard = bool(row.get("is_standard"))

            if not prefix or not PREFIX_RE.match(prefix):
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Invalid namespace prefix '{prefix}'.",
                    )
                )
                continue
            if not uri:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Namespace '{prefix}' is missing a URI.",
                    )
                )
                continue

            prior_uri = seen_prefix.get(prefix)
            if prior_uri is not None and prior_uri != uri:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Namespace prefix '{prefix}' maps to conflicting URIs.",
                        details={"first_uri": prior_uri, "second_uri": uri},
                    )
                )
            else:
                seen_prefix[prefix] = uri

            prior_prefix = seen_uri.get(uri)
            if prior_prefix is not None and prior_prefix != prefix:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="warning",
                        message=f"Namespace URI '{uri}' appears with multiple prefixes ('{prior_prefix}', '{prefix}').",
                    )
                )
            else:
                seen_uri[uri] = prefix

            if prefix in RESERVED_STANDARD_PREFIXES and not is_standard:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Reserved prefix '{prefix}' cannot be marked as custom.",
                    )
                )
        return findings


@dataclass(frozen=True)
class TaxonomyLabelRules:
    max_label_length: int = DEFAULT_LABEL_MAX_LEN
    rule_id: str = "taxonomy.label_constraints"
    description: str = "Taxonomy labels should reference known concepts and satisfy basic length constraints."

    def run(self, filing: Filing) -> list[Finding]:
        package = _taxonomy(filing)
        if not package:
            return []
        findings: list[Finding] = []
        elements = _element_rows(package)
        labels = _label_rows(package)
        concepts = {row["concept"] for row in elements}
        concepts_with_label = {str(label.get("concept")).strip() for label in labels}

        for label in labels:
            concept = str(label.get("concept") or "").strip()
            text = str(label.get("text") or "")
            if concept and concept not in concepts:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Label references unknown concept '{concept}'.",
                    )
                )
            if len(text) > self.max_label_length:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Label for '{concept}' exceeds maximum length {self.max_label_length}.",
                        details={"length": len(text)},
                    )
                )
            if not text.strip():
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Label for '{concept}' is empty.",
                    )
                )

        for element in elements:
            concept = element["concept"]
            is_custom = bool(element.get("is_custom"))
            if is_custom and concept not in concepts_with_label:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Custom taxonomy concept '{concept}' is missing a label.",
                    )
                )
        return findings


@dataclass(frozen=True)
class TaxonomyRelationshipTargetExistsRule:
    rule_id: str = "taxonomy.relationship_target_exists"
    description: str = "All relationship endpoints must refer to defined taxonomy concepts."

    def run(self, filing: Filing) -> list[Finding]:
        package = _taxonomy(filing)
        if not package:
            return []
        findings: list[Finding] = []
        concept_set = {row["concept"] for row in _element_rows(package)}
        for relationship in _relationships(package):
            source = str(relationship.get("from") or "").strip()
            target = str(relationship.get("to") or "").strip()
            arcrole = str(relationship.get("arcrole") or "unspecified")
            if source and source not in concept_set:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Relationship source '{source}' is not defined in taxonomy elements.",
                        details={"arcrole": arcrole},
                    )
                )
            if target and target not in concept_set:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Relationship target '{target}' is not defined in taxonomy elements.",
                        details={"arcrole": arcrole},
                    )
                )
        return findings


def _is_calculation_arc(arcrole: str) -> bool:
    lowered = arcrole.lower()
    return "calculation" in lowered or lowered.endswith("summation-item")


@dataclass(frozen=True)
class TaxonomyCalculationNoCycleRule:
    rule_id: str = "taxonomy.calculation_no_cycles"
    description: str = "Calculation relationship graph should be acyclic."

    def run(self, filing: Filing) -> list[Finding]:
        package = _taxonomy(filing)
        if not package:
            return []
        edges: dict[str, set[str]] = {}
        for rel in _relationships(package):
            arcrole = str(rel.get("arcrole") or "")
            if not _is_calculation_arc(arcrole):
                continue
            source = str(rel.get("from") or "").strip()
            target = str(rel.get("to") or "").strip()
            if not source or not target:
                continue
            edges.setdefault(source, set()).add(target)

        findings: list[Finding] = []
        visiting: set[str] = set()
        visited: set[str] = set()
        stack: list[str] = []

        def dfs(node: str) -> None:
            if node in visited or findings:
                return
            visiting.add(node)
            stack.append(node)
            for child in edges.get(node, set()):
                if child in visiting:
                    cycle_start = stack.index(child) if child in stack else 0
                    cycle = stack[cycle_start:] + [child]
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            severity="error",
                            message="Calculation relationship cycle detected.",
                            details={"cycle": cycle},
                        )
                    )
                    return
                dfs(child)
                if findings:
                    return
            stack.pop()
            visiting.remove(node)
            visited.add(node)

        for node in list(edges):
            dfs(node)
            if findings:
                break
        return findings


@dataclass(frozen=True)
class TaxonomyCustomConceptRelationshipRule:
    rule_id: str = "taxonomy.custom_concept_relationship_coverage"
    description: str = "Custom concepts should participate in at least one taxonomy relationship."

    def run(self, filing: Filing) -> list[Finding]:
        package = _taxonomy(filing)
        if not package:
            return []
        findings: list[Finding] = []
        custom_elements = {
            row["concept"]
            for row in _element_rows(package)
            if bool(row.get("is_custom"))
        }
        if not custom_elements:
            return []

        linked: set[str] = set()
        for rel in _relationships(package):
            source = str(rel.get("from") or "").strip()
            target = str(rel.get("to") or "").strip()
            if source in custom_elements:
                linked.add(source)
            if target in custom_elements:
                linked.add(target)

        for concept in sorted(custom_elements - linked):
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    severity="warning",
                    message=f"Custom concept '{concept}' is isolated from taxonomy relationship networks.",
                )
            )
        return findings
