from __future__ import annotations

import unittest

from formalfinance.baseline_compare import compare_with_baseline
from formalfinance.pilot_readiness import build_readiness_report
from formalfinance.profiles import get_profile
from formalfinance.sec_discovery import discover_recent_filings


class PilotReadinessTests(unittest.TestCase):
    def test_ixbrl_rule_count_in_target_window(self) -> None:
        rule_count = len(get_profile("ixbrl-gating"))
        self.assertGreaterEqual(rule_count, 30)
        self.assertLessEqual(rule_count, 50)

    def test_readiness_report_ready(self) -> None:
        report = build_readiness_report(user_agent="FormalFinance/0.1.0 test@example.com")
        self.assertTrue(report["summary"]["ready"])
        self.assertGreaterEqual(report["summary"]["total_checks"], 5)

    def test_baseline_comparison_95_target(self) -> None:
        formal = {
            "findings": [
                {"rule_id": "ixbrl.primary_document_constraints", "severity": "error"},
                {"rule_id": "taxonomy.relationship_target_exists", "severity": "error"},
            ]
        }
        baseline = {
            "findings": [
                {"code": "ixbrl.primary_document_constraints", "severity": "error"},
                {"code": "taxonomy.relationship_target_exists", "severity": "error"},
            ]
        }
        comparison = compare_with_baseline(formal, baseline).as_dict()
        self.assertTrue(comparison["metrics"]["status_agreement"])
        self.assertTrue(comparison["metrics"]["meets_95pct_target"])

    def test_baseline_comparison_detects_discrepancy(self) -> None:
        formal = {
            "findings": [
                {"rule_id": "ixbrl.primary_document_constraints", "severity": "error"},
            ]
        }
        baseline = {
            "findings": [
                {"code": "taxonomy.relationship_target_exists", "severity": "error"},
            ]
        }
        comparison = compare_with_baseline(formal, baseline).as_dict()
        self.assertFalse(comparison["metrics"]["meets_95pct_target"])
        self.assertEqual(
            comparison["formal_only_error_ids"],
            ["ixbrl.primary_document_constraints"],
        )

    def test_discovery_requires_user_agent(self) -> None:
        with self.assertRaises(ValueError):
            discover_recent_filings(user_agent="", max_filings=5)


if __name__ == "__main__":
    unittest.main()
