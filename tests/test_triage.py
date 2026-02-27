from __future__ import annotations

import unittest

from formalfinance.triage import TriageUpdate, apply_triage_update, init_triage_from_report


class TriageTests(unittest.TestCase):
    def test_init_and_update_triage(self) -> None:
        report = {
            "input_digest": "abc",
            "profile": "ixbrl-gating",
            "findings": [
                {
                    "finding_id": "ixbrl.primary_document_constraints:0001",
                    "rule_id": "ixbrl.primary_document_constraints",
                    "severity": "error",
                    "message": "bad primary",
                }
            ],
        }
        triage = init_triage_from_report(report, owner="alice@example.com")
        self.assertEqual(len(triage["issues"]), 1)
        self.assertEqual(triage["issues"][0]["status"], "open")
        self.assertEqual(triage["issues"][0]["assignee"], "alice@example.com")

        updated = apply_triage_update(
            triage,
            TriageUpdate(
                finding_id="ixbrl.primary_document_constraints:0001",
                status="in_progress",
                assignee="bob@example.com",
                note="Investigating source document",
            ),
        )
        self.assertEqual(updated["issues"][0]["status"], "in_progress")
        self.assertEqual(updated["issues"][0]["assignee"], "bob@example.com")
        self.assertEqual(len(updated["issues"][0]["notes"]), 1)


if __name__ == "__main__":
    unittest.main()
