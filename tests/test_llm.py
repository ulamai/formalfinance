from __future__ import annotations

import unittest

from formalfinance.llm import LLMConfig, generate_advisory


class LLMTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        cfg = LLMConfig()
        advisory = generate_advisory({"findings": []}, cfg)
        self.assertFalse(advisory["enabled"])
        self.assertEqual(advisory["status"], "disabled")

    def test_mock_provider_generates_actions(self) -> None:
        cfg = LLMConfig(enabled=True, provider="mock", model="mock-v1")
        report = {
            "findings": [
                {"rule_id": "ixbrl.primary_document_constraints", "severity": "error", "message": "bad primary"},
                {"rule_id": "taxonomy.relationship_target_exists", "severity": "warning", "message": "bad target"},
            ]
        }
        advisory = generate_advisory(report, cfg)
        self.assertEqual(advisory["status"], "ok")
        self.assertEqual(advisory["provider"], "mock")
        self.assertEqual(advisory["model"], "mock-v1")
        self.assertGreaterEqual(len(advisory["actions"]), 2)

    def test_unsupported_provider_returns_error_status(self) -> None:
        cfg = LLMConfig(enabled=True, provider="unknown-provider")
        advisory = generate_advisory({"findings": []}, cfg)
        self.assertEqual(advisory["status"], "error")
        self.assertIn("Unsupported LLM provider", advisory["error"])


if __name__ == "__main__":
    unittest.main()
