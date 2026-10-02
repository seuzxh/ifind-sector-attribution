"""Offline contracts for the premarket CLI and its dependency assembly."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import main
from database import Database
from opening_strength.snapshot_service import PremarketRunError, PremarketRunResult


class RecordingService:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def run_and_freeze(self, trade_date, force_replace=False):
        self.calls.append((trade_date, force_replace))
        if self.error:
            raise self.error
        return PremarketRunResult("fixture-run", trade_date, "FROZEN", 10, 9, .9, .5)


class FixtureHub:
    """Exact hub public interfaces, with no token or network side effects."""
    def __init__(self):
        self.client = self
        self.store = self

    def get_concept_members(self, index_code, trade_date):
        return {"errorcode": 0, "tables": [{"table": {
            "p03473_f002": ["600001.SH"], "p03473_f003": ["甲"],
        }}]}

    def get_concept_names(self):
        return {"884001.TI": "行业", "885001.TI": "概念"}

    def get_concept_members_map(self, theme_codes):
        return {code: [{"stock_code": "600001.SH"}] for code in theme_codes}

    def get_latest_member_date(self):
        return "2026-10-01"

    def get_history_quotation(self, codes, start_date, end_date, indicators):
        return {"errorcode": 0, "tables": []}


class OpeningPremarketCliTests(unittest.TestCase):
    def invoke(self, argv, service):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(main, "_build_opening_premarket_service", return_value=service,
                          create=True), patch("sys.argv", ["main.py", *argv]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                main.main()
            except SystemExit as error:
                code = error.code
            else:
                code = 0
        return code, stdout.getvalue(), stderr.getvalue()

    def test_date_is_required_before_any_service_call(self):
        service = RecordingService()
        code, _, stderr = self.invoke(["opening-premarket"], service)
        self.assertEqual(code, 2)
        self.assertIn("--date", stderr)
        self.assertEqual(service.calls, [])

    def test_date_must_be_eight_ascii_digits(self):
        for date in ("2026102", "202610022", "2026-10-02", "2026100x", "２０２６１００２"):
            with self.subTest(date=date):
                service = RecordingService()
                code, _, stderr = self.invoke(["opening-premarket", "--date", date], service)
                self.assertEqual(code, 2)
                self.assertIn("YYYYMMDD", stderr)
                self.assertEqual(service.calls, [])

    def test_normal_run_does_not_force_replace_and_prints_metrics(self):
        service = RecordingService()
        code, stdout, stderr = self.invoke(["opening-premarket", "--date", "20261002"], service)
        self.assertEqual(code, 0)
        self.assertEqual(service.calls, [("20261002", False)])
        for field in ("run_id=fixture-run", "status=FROZEN", "candidate_count=10",
                      "mapped_count=9", "coverage_ratio=0.9"):
            self.assertIn(field, stdout)
        self.assertEqual(stderr, "")

    def test_explicit_flag_passes_force_replace(self):
        service = RecordingService()
        code, _, _ = self.invoke(
            ["opening-premarket", "--date", "20261002", "--force-replace"], service)
        self.assertEqual(code, 0)
        self.assertEqual(service.calls, [("20261002", True)])

    def test_domain_failure_returns_one_and_omits_sensitive_detail(self):
        service = RecordingService(PremarketRunError(
            "failed-run", "SOURCE_POOL_REQUEST_FAILED", "access_token=secret-fixture"))
        code, stdout, stderr = self.invoke(["opening-premarket", "--date", "20261002"], service)
        self.assertEqual(code, 1)
        self.assertIn("failed-run", stderr)
        self.assertIn("SOURCE_POOL_REQUEST_FAILED", stderr)
        self.assertNotIn("secret-fixture", stdout + stderr)
        self.assertNotIn("access_token", stdout + stderr)
        self.assertNotIn("Traceback", stdout + stderr)

    def test_real_assembly_freezes_fixture_hub_into_temporary_database(self):
        with tempfile.TemporaryDirectory() as temp:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                db = Database(str(Path(temp) / "opening.db"))
            with patch.object(main, "Database", return_value=db), \
                    patch("ifind_hub.get_hub", return_value=FixtureHub()), \
                    patch("sys.argv", ["main.py", "opening-premarket", "--date", "20261002"]), \
                    redirect_stdout(stdout):
                try:
                    main.main()
                except SystemExit as error:
                    self.fail(f"CLI failed with exit code {error.code}")
            frozen = db.get_frozen_opening_run("20261002")
            self.assertIsNotNone(frozen)
            self.assertEqual((frozen["candidate_count"], frozen["mapped_count"]), (1, 1))
            self.assertEqual(frozen["history_coverage_ratio"], 0.0)
            self.assertEqual(len(db.get_opening_candidates(frozen["run_id"])), 3)
            self.assertEqual(len(db.get_opening_memberships(frozen["run_id"])), 2)
            self.assertTrue(db.get_opening_attributions(frozen["run_id"]))
            self.assertIn("status=FROZEN", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
