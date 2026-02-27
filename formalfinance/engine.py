from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

from .models import Filing
from .rules import Finding, Rule
from .tracing import NoopTraceLogger


@dataclass(frozen=True)
class ValidationResult:
    input_digest: str
    executed_rules: list[str]
    findings: list[Finding] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len([f for f in self.findings if f.severity == "error"])

    @property
    def warning_count(self) -> int:
        return len([f for f in self.findings if f.severity == "warning"])

    @property
    def status(self) -> str:
        return "risk" if self.error_count > 0 else "clean"

    def as_report(self, profile: str) -> dict:
        return {
            "schema_version": "formalfinance.report.v0",
            "profile": profile,
            "status": self.status,
            "input_digest": self.input_digest,
            "summary": {
                "rules_executed": len(self.executed_rules),
                "error_count": self.error_count,
                "warning_count": self.warning_count,
            },
            "executed_rules": self.executed_rules,
            "findings": [asdict(f) for f in self.findings],
        }


@dataclass
class ValidationEngine:
    rules: Iterable[Rule]

    def validate(self, filing: Filing, trace_logger: object | None = None) -> ValidationResult:
        logger = trace_logger if trace_logger is not None else NoopTraceLogger()
        findings: list[Finding] = []
        executed: list[str] = []
        input_digest = filing.input_digest()

        logger.log("validation.start", input_digest=input_digest)
        for rule in self.rules:
            executed.append(rule.rule_id)
            logger.log("rule.start", rule_id=rule.rule_id, description=rule.description)
            rule_findings = rule.run(filing)
            findings.extend(rule_findings)
            logger.log(
                "rule.end",
                rule_id=rule.rule_id,
                finding_count=len(rule_findings),
                severities=[f.severity for f in rule_findings],
            )
            for finding in rule_findings:
                logger.log(
                    "finding",
                    rule_id=finding.rule_id,
                    severity=finding.severity,
                    message=finding.message,
                    fact_ids=finding.fact_ids,
                    details=finding.details,
                )
        logger.log("validation.end", total_findings=len(findings))
        return ValidationResult(input_digest=input_digest, executed_rules=executed, findings=findings)
