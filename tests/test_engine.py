from __future__ import annotations

import json
from pathlib import Path
import unittest

from formalfinance.certificate import issue_certificate
from formalfinance.engine import ValidationEngine
from formalfinance.models import Filing
from formalfinance.profiles import get_profile


ROOT = Path(__file__).resolve().parents[1]


def _load_example(name: str) -> Filing:
    with open(ROOT / "examples" / name, "r", encoding="utf-8") as fp:
        return Filing.from_dict(json.load(fp))


class EngineTests(unittest.TestCase):
    def test_clean_ixbrl_profile(self) -> None:
        filing = _load_example("filing_clean.json")
        result = ValidationEngine(get_profile("ixbrl-gating")).validate(filing)
        self.assertEqual(result.status, "clean")
        self.assertEqual(result.error_count, 0)

    def test_risky_fsd_profile(self) -> None:
        filing = _load_example("filing_risky.json")
        result = ValidationEngine(get_profile("fsd-consistency")).validate(filing)
        self.assertEqual(result.status, "risk")
        rule_ids = [f.rule_id for f in result.findings]
        self.assertIn("xbrl.duplicate_fact_conflict", rule_ids)
        self.assertIn("acct.balance_sheet_equation", rule_ids)
        self.assertIn("ixbrl.required_concepts", rule_ids)

    def test_certificate_issued_only_when_clean(self) -> None:
        clean = ValidationEngine(get_profile("ixbrl-gating")).validate(_load_example("filing_clean.json"))
        cert = issue_certificate("ixbrl-gating", clean)
        self.assertEqual(cert["verdict"], "clean")

        risky = ValidationEngine(get_profile("ixbrl-gating")).validate(_load_example("filing_risky.json"))
        with self.assertRaises(ValueError):
            issue_certificate("ixbrl-gating", risky)


if __name__ == "__main__":
    unittest.main()
