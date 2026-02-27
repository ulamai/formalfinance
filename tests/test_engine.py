from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from formalfinance.certificate import issue_certificate
from formalfinance.evidence import build_evidence_pack
from formalfinance.engine import ValidationEngine
from formalfinance.models import Filing
from formalfinance.profiles import get_profile
from formalfinance.sec_ingest import companyfacts_to_filing


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
        self.assertIn("acct.balance_sheet_equation", rule_ids)
        self.assertIn("ixbrl.required_concepts", rule_ids)
        self.assertIn("ixbrl.submission_suspension_risk", rule_ids)
        self.assertIn("taxonomy.calculation_no_cycles", rule_ids)
        self.assertIn("taxonomy.relationship_target_exists", rule_ids)

    def test_certificate_issued_only_when_clean(self) -> None:
        clean = ValidationEngine(get_profile("ixbrl-gating")).validate(_load_example("filing_clean.json"))
        cert = issue_certificate("ixbrl-gating", clean)
        self.assertEqual(cert["verdict"], "clean")

        risky = ValidationEngine(get_profile("ixbrl-gating")).validate(_load_example("filing_risky.json"))
        with self.assertRaises(ValueError):
            issue_certificate("ixbrl-gating", risky)

    def test_warning_only_status_is_review(self) -> None:
        with open(ROOT / "examples" / "filing_clean.json", "r", encoding="utf-8") as fp:
            raw = json.load(fp)
        raw["entity"] = "Example Clean Corp, Inc."
        filing = Filing.from_dict(raw)
        result = ValidationEngine(get_profile("ixbrl-gating")).validate(filing)
        self.assertEqual(result.status, "review")
        self.assertEqual(result.error_count, 0)
        self.assertGreater(result.warning_count, 0)

    def test_companyfacts_normalization_and_profile(self) -> None:
        with open(ROOT / "examples" / "companyfacts_sample.json", "r", encoding="utf-8") as fp:
            raw = json.load(fp)
        filing, selection = companyfacts_to_filing(raw)
        self.assertEqual(selection.accession, "0000123456-26-000001")
        self.assertEqual(filing.accession, selection.accession)
        self.assertEqual(filing.cik, "0000123456")

        assets_values = [f.value for f in filing.facts if f.concept == "us-gaap:Assets"]
        self.assertIn(1000, assets_values)
        self.assertNotIn(900, assets_values)

        result = ValidationEngine(get_profile("companyfacts-consistency")).validate(filing)
        self.assertEqual(result.status, "clean")

    def test_evidence_pack_outputs_files(self) -> None:
        clean_filing = _load_example("filing_clean.json")
        risky_filing = _load_example("filing_risky.json")
        with tempfile.TemporaryDirectory() as tmp:
            clean_pack_dir = Path(tmp) / "clean"
            clean_result = build_evidence_pack(
                filing=clean_filing,
                profile="fsd-consistency",
                output_dir=clean_pack_dir,
            )
            self.assertEqual(clean_result.status, "clean")
            self.assertTrue(clean_result.report_path.exists())
            self.assertTrue(clean_result.trace_path.exists())
            self.assertTrue(clean_result.summary_path.exists())
            self.assertIsNotNone(clean_result.certificate_path)
            self.assertTrue(clean_result.certificate_path.exists())

            risky_pack_dir = Path(tmp) / "risky"
            risky_result = build_evidence_pack(
                filing=risky_filing,
                profile="fsd-consistency",
                output_dir=risky_pack_dir,
            )
            self.assertEqual(risky_result.status, "risk")
            self.assertTrue(risky_result.report_path.exists())
            self.assertTrue(risky_result.trace_path.exists())
            self.assertTrue(risky_result.summary_path.exists())
            self.assertIsNone(risky_result.certificate_path)


if __name__ == "__main__":
    unittest.main()
