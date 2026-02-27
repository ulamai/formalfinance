from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from formalfinance.store import RunStore


class StoreTests(unittest.TestCase):
    def test_migration_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runs.sqlite3"
            store = RunStore(db_path)
            status = store.migration_status()
            self.assertGreaterEqual(status["latest_version"], 1)

            store.log_run(
                endpoint="/v1/validate",
                tenant_id="tenant-a",
                profile="ixbrl-gating",
                status="clean",
                error_count=0,
                warning_count=0,
                input_digest="abc",
                latency_ms=123,
                request_bytes=10,
                response_bytes=20,
                metadata_json="{}",
            )
            metrics = store.metrics()
            self.assertEqual(metrics["total_runs"], 1)
            self.assertIn("/v1/validate", metrics["endpoint_counts"])


if __name__ == "__main__":
    unittest.main()
