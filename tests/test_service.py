from __future__ import annotations

from http.client import HTTPConnection
from pathlib import Path
import json
import tempfile
import threading
import unittest

from formalfinance.api import ServiceConfig, create_server


ROOT = Path(__file__).resolve().parents[1]


class ServiceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        db_path = str(Path(cls._tmpdir.name) / "runs.sqlite3")
        config = ServiceConfig(
            host="127.0.0.1",
            port=0,
            db_path=db_path,
            api_keys=("test-key",),
            max_request_bytes=20000,
            rate_limit_per_minute=240,
        )
        cls._server = create_server(config)
        cls._host, cls._port = cls._server.server_address
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.shutdown()
        cls._server.server_close()
        cls._thread.join(timeout=2)
        cls._tmpdir.cleanup()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        conn = HTTPConnection(self._host, self._port, timeout=5)
        body = None
        final_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            final_headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=final_headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        parsed = json.loads(raw) if raw else {}
        return resp.status, parsed

    def _load_filing(self, name: str) -> dict:
        with open(ROOT / "examples" / name, "r", encoding="utf-8") as fp:
            return json.load(fp)

    def test_healthz_is_public(self) -> None:
        status, payload = self._request("GET", "/v1/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_profiles_requires_auth(self) -> None:
        status, _ = self._request("GET", "/v1/profiles")
        self.assertEqual(status, 401)

    def test_validate_and_runs(self) -> None:
        headers = {"X-API-Key": "test-key"}
        filing = self._load_filing("filing_clean.json")
        status, payload = self._request(
            "POST",
            "/v1/validate",
            payload={"profile": "ixbrl-gating", "filing": filing, "tenant_id": "tenant-a"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertIn("run_id", payload)
        self.assertEqual(payload["report"]["status"], "clean")
        self.assertEqual(payload["advisory"]["status"], "disabled")

        list_status, list_payload = self._request(
            "GET",
            "/v1/runs?limit=10&tenant_id=tenant-a",
            headers=headers,
        )
        self.assertEqual(list_status, 200)
        self.assertGreaterEqual(len(list_payload["runs"]), 1)
        self.assertEqual(list_payload["runs"][0]["tenant_id"], "tenant-a")

    def test_certify_risky_returns_not_issued(self) -> None:
        headers = {"Authorization": "Bearer test-key"}
        filing = self._load_filing("filing_risky.json")
        status, payload = self._request(
            "POST",
            "/v1/certify",
            payload={"profile": "fsd-consistency", "filing": filing},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertIsNone(payload["certificate"])
        self.assertEqual(payload["certificate_status"], "not_issued")
        self.assertEqual(payload["advisory"]["status"], "disabled")

    def test_validate_with_mock_llm(self) -> None:
        headers = {"X-API-Key": "test-key"}
        filing = self._load_filing("filing_risky.json")
        status, payload = self._request(
            "POST",
            "/v1/validate",
            payload={
                "profile": "fsd-consistency",
                "filing": filing,
                "llm": {"enabled": True, "provider": "mock", "model": "mock-v1"},
            },
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["advisory"]["status"], "ok")
        self.assertEqual(payload["advisory"]["provider"], "mock")
        self.assertTrue(isinstance(payload["advisory"].get("actions"), list))
        self.assertGreater(len(payload["advisory"]["actions"]), 0)

    def test_compare_baseline(self) -> None:
        headers = {"X-API-Key": "test-key"}
        status, payload = self._request(
            "POST",
            "/v1/compare-baseline",
            payload={
                "formal_report": {
                    "findings": [
                        {"rule_id": "ixbrl.primary_document_constraints", "severity": "error"}
                    ]
                },
                "baseline_report": {
                    "findings": [
                        {"code": "ixbrl.primary_document_constraints", "severity": "error"}
                    ]
                },
            },
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["comparison"]["metrics"]["meets_95pct_target"])

    def test_metrics_endpoint(self) -> None:
        headers = {"X-API-Key": "test-key"}
        status, payload = self._request("GET", "/v1/metrics", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn("total_runs", payload)
        self.assertIn("latency_ms", payload)

    def test_migrations_endpoint(self) -> None:
        headers = {"X-API-Key": "test-key"}
        status, payload = self._request("GET", "/v1/migrations", headers=headers)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["latest_version"], 1)

    def test_request_size_limit(self) -> None:
        headers = {"X-API-Key": "test-key"}
        large_payload = {
            "profile": "ixbrl-gating",
            "filing": {"contexts": {}, "facts": []},
            "tenant_id": "x" * 25000,
        }
        status, payload = self._request("POST", "/v1/validate", payload=large_payload, headers=headers)
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"], "invalid_json")


if __name__ == "__main__":
    unittest.main()
