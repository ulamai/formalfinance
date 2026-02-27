from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Filing
from .rules import Finding, Rule


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _ixbrl_attachments(ixbrl: dict[str, Any]) -> list[dict[str, Any]]:
    raw = ixbrl.get("attachments", [])
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _filename(obj: dict[str, Any], fallback: str) -> str:
    name = str(obj.get("filename") or "").strip()
    return name or fallback


def _is_html_filename(filename: str) -> bool:
    lowered = filename.lower()
    return lowered.endswith(".htm") or lowered.endswith(".html")


def _collect_xbrl_errors(doc: dict[str, Any]) -> list[Any]:
    rows = _as_list(doc.get("xbrl_errors"))
    if rows:
        return rows
    return _as_list(doc.get("errors"))


@dataclass(frozen=True)
class InlineMetadataPresenceRule:
    rule_id: str = "ixbrl.inline_metadata_presence"
    description: str = "iXBRL gating requires a declared primary inline document and attachment metadata."

    def run(self, filing: Filing) -> list[Finding]:
        ixbrl = filing.ixbrl or {}
        if ixbrl:
            return []
        return [
            Finding(
                rule_id=self.rule_id,
                severity="error",
                message="Filing is missing `ixbrl` metadata required for inline preflight gating.",
            )
        ]


@dataclass(frozen=True)
class InlinePrimaryDocumentRule:
    rule_id: str = "ixbrl.primary_document_constraints"
    description: str = "Primary inline document must be HTML and marked as Inline XBRL."

    def run(self, filing: Filing) -> list[Finding]:
        ixbrl = filing.ixbrl or {}
        if not ixbrl:
            return []
        findings: list[Finding] = []
        primary = ixbrl.get("primary_document")
        if not isinstance(primary, dict):
            return [
                Finding(
                    rule_id=self.rule_id,
                    severity="error",
                    message="iXBRL metadata must include a `primary_document` object.",
                )
            ]

        filename = _filename(primary, "primary_document")
        if not _is_html_filename(filename):
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    severity="error",
                    message=f"Primary document '{filename}' must use .htm or .html extension.",
                )
            )
        if primary.get("is_inline_xbrl") is not True:
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    severity="error",
                    message=f"Primary document '{filename}' is not marked as inline XBRL.",
                )
            )
        if primary.get("contains_ix_header") is False:
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    severity="error",
                    message=f"Primary document '{filename}' does not declare inline XBRL header metadata.",
                )
            )
        return findings


@dataclass(frozen=True)
class InlineAttachmentHtmlRule:
    rule_id: str = "ixbrl.inline_attachment_html"
    description: str = "All attachments marked as inline XBRL must be HTML and uniquely named."

    def run(self, filing: Filing) -> list[Finding]:
        ixbrl = filing.ixbrl or {}
        if not ixbrl:
            return []
        findings: list[Finding] = []
        seen_names: set[str] = set()
        for idx, attachment in enumerate(_ixbrl_attachments(ixbrl), start=1):
            filename = _filename(attachment, f"attachment-{idx}")
            lowered = filename.lower()
            if lowered in seen_names:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Duplicate attachment filename '{filename}' in iXBRL package.",
                    )
                )
            else:
                seen_names.add(lowered)
            if attachment.get("is_inline_xbrl") is True and not _is_html_filename(filename):
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Inline XBRL attachment '{filename}' must use .htm or .html extension.",
                    )
                )
        return findings


@dataclass(frozen=True)
class InlineNoDisallowedHtmlRule:
    rule_id: str = "ixbrl.disallowed_html"
    description: str = "Inline XBRL package should not contain disallowed active HTML content."

    def run(self, filing: Filing) -> list[Finding]:
        ixbrl = filing.ixbrl or {}
        if not ixbrl:
            return []
        findings: list[Finding] = []
        package_tags = [str(t) for t in _as_list(ixbrl.get("disallowed_html_tags"))]
        if package_tags:
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    severity="error",
                    message="iXBRL package contains disallowed HTML tags.",
                    details={"tags": sorted(set(package_tags))},
                )
            )
        primary = ixbrl.get("primary_document")
        if isinstance(primary, dict):
            tags = [str(t) for t in _as_list(primary.get("disallowed_html_tags"))]
            if tags:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Primary document '{_filename(primary, 'primary_document')}' contains disallowed HTML tags.",
                        details={"tags": sorted(set(tags))},
                    )
                )
        for idx, attachment in enumerate(_ixbrl_attachments(ixbrl), start=1):
            tags = [str(t) for t in _as_list(attachment.get("disallowed_html_tags"))]
            if tags:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Attachment '{_filename(attachment, f'attachment-{idx}')}' contains disallowed HTML tags.",
                        details={"tags": sorted(set(tags))},
                    )
                )
        return findings


@dataclass(frozen=True)
class InlineNoExternalReferencesRule:
    rule_id: str = "ixbrl.external_reference_constraints"
    description: str = "Inline package should not reference external resources during submission validation."

    def run(self, filing: Filing) -> list[Finding]:
        ixbrl = filing.ixbrl or {}
        if not ixbrl:
            return []
        findings: list[Finding] = []
        package_refs = [str(ref) for ref in _as_list(ixbrl.get("external_references")) if str(ref).strip()]
        if package_refs:
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    severity="error",
                    message="iXBRL package includes external references.",
                    details={"references": package_refs[:20]},
                )
            )
        primary = ixbrl.get("primary_document")
        if isinstance(primary, dict):
            refs = [str(ref) for ref in _as_list(primary.get("external_references")) if str(ref).strip()]
            if refs:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Primary document '{_filename(primary, 'primary_document')}' references external resources.",
                        details={"references": refs[:20]},
                    )
                )
        for idx, attachment in enumerate(_ixbrl_attachments(ixbrl), start=1):
            refs = [str(ref) for ref in _as_list(attachment.get("external_references")) if str(ref).strip()]
            if refs:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        severity="error",
                        message=f"Attachment '{_filename(attachment, f'attachment-{idx}')}' references external resources.",
                        details={"references": refs[:20]},
                    )
                )
        return findings


@dataclass(frozen=True)
class InlineXbrlErrorSuspensionRiskRule:
    rule_id: str = "ixbrl.submission_suspension_risk"
    description: str = "Any XBRL validation error in the inline set should be flagged as suspension risk."

    def run(self, filing: Filing) -> list[Finding]:
        ixbrl = filing.ixbrl or {}
        if not ixbrl:
            return []
        findings: list[Finding] = []
        affected_docs: list[dict[str, Any]] = []

        primary = ixbrl.get("primary_document")
        if isinstance(primary, dict):
            errors = _collect_xbrl_errors(primary)
            if errors:
                affected_docs.append(
                    {"document": _filename(primary, "primary_document"), "error_count": len(errors)}
                )

        for idx, attachment in enumerate(_ixbrl_attachments(ixbrl), start=1):
            errors = _collect_xbrl_errors(attachment)
            if errors:
                affected_docs.append(
                    {"document": _filename(attachment, f"attachment-{idx}"), "error_count": len(errors)}
                )

        if affected_docs:
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    severity="error",
                    message=(
                        "Inline XBRL validation errors detected. Submission suspension risk is elevated until resolved."
                    ),
                    details={"affected_documents": affected_docs},
                )
            )
        return findings
