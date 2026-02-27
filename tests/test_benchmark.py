from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from formalfinance.benchmark import benchmark_from_manifest


ROOT = Path(__file__).resolve().parents[1]


class BaselineBenchmarkTests(unittest.TestCase):
    def test_benchmark_manifest_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline_path = Path(tmp) / "baseline.json"
            manifest_path = Path(tmp) / "manifest.json"
            baseline_path.write_text(json.dumps({"findings": []}), encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "id": "clean-1",
                                "filing": str(ROOT / "examples" / "filing_clean.json"),
                                "baseline_report": str(baseline_path),
                                "profile": "ixbrl-gating",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = benchmark_from_manifest(manifest_path)
            self.assertEqual(result["summary"]["case_count"], 1)
            self.assertEqual(result["summary"]["meets_95pct_target_rate"], 1.0)
            self.assertEqual(result["cases"][0]["case_id"], "clean-1")


if __name__ == "__main__":
    unittest.main()
