"""Offline HTTP contracts for the read-only opening dashboard."""

import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from database import Database
from intraday_fetcher import IntradayFetcher


ENDPOINT = "/api/opening-strength/dashboard"
TRADE_DATE = "20261002"


class QuoteFetcher:
    def __init__(self):
        self.requests = []
        self.raw = {
            "600001.SH": {
                "pre_close": 100,
                "trading": [
                    {"time": "09:30", "last_price": 101, "avg_price": 100, "turnover": 1000},
                    {"time": "09:32", "last_price": 103, "avg_price": 102, "turnover": 2000},
                ],
            },
        }

    def fetch_batch(self, codes, date=None):
        self.requests.append((codes, date))
        return self.raw


class OpeningStrengthApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(str(Path(temp.name) / "opening.db"))
        # Importing the existing API initializes its shared DB; redirect it to this
        # test's SQLite file and never touch the runtime database.
        with patch("database.Database", return_value=self.db):
            self.app = importlib.import_module("api.app").app
        self._patch(patch.object(importlib.import_module("api.deps"), "db", self.db))
        router = sys.modules.get("api.routers.opening_strength")
        if router is not None:
            self._patch(patch.object(router, "_service", None))
        self.fetcher = QuoteFetcher()
        self.fetcher_constructor = self._patch(
            patch("intraday_fetcher.IntradayFetcher", return_value=self.fetcher))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def _patch(self, patcher):
        replacement = patcher.start()
        self.addCleanup(patcher.stop)
        return replacement

    def freeze(self):
        self.db.create_opening_run({
            "run_id": "frozen-api", "trade_date": TRADE_DATE,
            "model_version": "model-1", "config_version": "config-1",
            "started_at": "2026-10-02T09:00:00+08:00",
        })
        self.db.save_opening_candidates("frozen-api", [{
            "stock_code": "600001.SH", "stock_name": "甲",
            "source_pool_id": "883910.TI", "source_rank": 1,
        }])
        self.db.save_opening_attributions("frozen-api", [{
            "stock_code": "600001.SH", "theme_code": "885001.TI", "theme_name": "冻结题材",
            "theme_type": "CONCEPT", "rank": 1, "raw_score": .8, "weight": 1,
            "confidence": .9, "reason_codes": ["MULTI_POOL_SUPPORT"], "evidence": {},
        }])
        self.db.mark_opening_run_validated("frozen-api", {
            "candidate_count": 1, "mapped_count": 1, "coverage_ratio": 1,
            "history_coverage_ratio": 1, "hub_member_date": "2026-10-01",
        })
        self.db.freeze_opening_run("frozen-api", replace_existing=False)

    def assert_error(self, response, status, code, retryable, message=None):
        self.assertEqual(response.status_code, status, response.text)
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        error = payload["error"]
        self.assertEqual(set(error), {"code", "message", "retryable"})
        self.assertEqual(error["code"], code)
        self.assertIs(error["retryable"], retryable)
        self.assertIsInstance(error["message"], str)
        self.assertTrue(error["message"])
        if message is not None:
            self.assertEqual(error["message"], message)

    def test_success_serializes_complete_frozen_dashboard(self):
        self.freeze()
        response = self.client.get(ENDPOINT, params={"trade_date": TRADE_DATE})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        generated_at = payload.pop("generated_at")
        self.assertRegex(generated_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")
        self.assertEqual(payload, {
            "trade_date": TRADE_DATE, "run_id": "frozen-api", "mode": "historical",
            "snapshot_time": "09:32", "latest_time": "09:32",
            "available_times": ["09:30", "09:32"], "candidate_count": 1,
            "theme_count": 1, "data_health": 1, "cache_status": "fresh",
            "acceleration_theme_codes": [], "breadth_theme_codes": [],
            "themes": [{
                "theme_code": "885001.TI", "theme_name": "冻结题材", "theme_type": "CONCEPT",
                "level": 3, "momentum_1m": 2, "up_ratio": 1, "breadth_delta_1m": 0,
                "attributed_stock_count": 1, "valid_quote_count": 1, "supporting_count": 1,
                "support_weight": 1, "source_pool_diversity": 1, "top1_concentration": 1,
                "top3_concentration": 1, "data_health": 1,
                "risk_tags": ["单股驱动", "高度集中"],
                "contributors": [{
                    "stock_code": "600001.SH", "stock_name": "甲", "attribution_weight": 1,
                    "confidence": .9, "reason_codes": ["MULTI_POOL_SUPPORT"],
                    "source_pool_ids": ["883910.TI"], "quote_time": "09:32", "pre_close": 100,
                    "last_price": 103, "avg_price": 102, "turnover": 2000,
                    "return_pct": 3, "contribution": 3, "positive_contribution": 3,
                    "has_quote": True,
                }],
            }],
        })
        self.assertEqual(self.fetcher.requests, [(["600001.SH"], TRADE_DATE)])

    def test_snapshot_time_resolves_downward_and_reuses_service_cache(self):
        self.freeze()
        for requested, actual, level in (("09:30", "09:30", 1), ("09:31", "09:30", 1),
                                         ("23:59", "09:32", 3)):
            with self.subTest(requested=requested):
                response = self.client.get(ENDPOINT, params={
                    "trade_date": TRADE_DATE, "snapshot_time": requested,
                })
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual((payload["snapshot_time"], payload["themes"][0]["level"]),
                                 (actual, level))
        self.assertEqual(self.fetcher.requests, [(["600001.SH"], TRADE_DATE)])

    def test_missing_or_malformed_date_returns_stable_422_before_service_creation(self):
        cases = [None, "", "2026-10-02", "2026102", "202610020", "20260230", "20261301",
                 "00001002", "20261002\n", "２０２６１００２", " 20261002", "20261002extra"]
        for date in cases:
            with self.subTest(date=date):
                params = {} if date is None else {"trade_date": date}
                response = self.client.get(ENDPOINT, params=params)
                self.assert_error(response, 422, "INVALID_REQUEST", False)
        self.assertEqual(self.fetcher.requests, [])
        router = sys.modules.get("api.routers.opening_strength")
        if router is not None:
            self.assertIsNone(router._service)

    def test_malformed_or_preopening_time_returns_stable_422_before_service_creation(self):
        for minute in ("", "9:30", "09:3", "09:30:00", "24:00", "09:60", "09:29", "00:00",
                       "09:30\n", "０９:３０", "latest", " 09:30", "09:30extra"):
            with self.subTest(minute=minute):
                response = self.client.get(ENDPOINT, params={
                    "trade_date": TRADE_DATE, "snapshot_time": minute,
                })
                self.assert_error(response, 422, "INVALID_REQUEST", False)
        self.assertEqual(self.fetcher.requests, [])
        router = sys.modules.get("api.routers.opening_strength")
        if router is not None:
            self.assertIsNone(router._service)

    def test_missing_snapshot_returns_404_without_kline_config_or_fetcher(self):
        import config
        with patch.dict(os.environ, {}, clear=True), patch.object(config, "KLINE_API_BASE_URL", ""):
            self.fetcher_constructor.side_effect = AssertionError("fetcher must remain lazy")
            response = self.client.get(ENDPOINT, params={"trade_date": TRADE_DATE})
        self.assert_error(response, 404, "SNAPSHOT_NOT_FOUND", False, "该日期尚未生成盘前冻结快照")

    def test_empty_quote_series_returns_exact_503_domain_error(self):
        self.freeze()
        self.fetcher.raw = {}
        response = self.client.get(ENDPOINT, params={"trade_date": TRADE_DATE})
        self.assert_error(response, 503, "QUOTE_DATA_UNAVAILABLE", True, "暂无可用的盘中行情")

    def test_upstream_failure_returns_exact_safe_503_domain_error(self):
        self.freeze()
        with patch.object(self.fetcher, "fetch_batch", side_effect=RuntimeError(
                "private-token=do-not-expose /private/local/provider.py")):
            response = self.client.get(ENDPOINT, params={"trade_date": TRADE_DATE})
        self.assert_error(response, 503, "QUOTE_PROVIDER_FAILED", True, "行情获取暂时失败，请稍后重试")
        self.assertNotIn("private-token", response.text)
        self.assertNotIn("/private/local", response.text)

    def test_real_adapter_timeout_returns_safe_provider_failure(self):
        self.freeze()
        with patch.dict(os.environ, {"KLINE_API_BASE_URL": "https://quotes.invalid"}), \
                patch("intraday_fetcher.TrendFetcher.fetch_trend", side_effect=TimeoutError(
                    "private-token=fixture /private/local/provider.py")):
            self.fetcher_constructor.return_value = IntradayFetcher(workers=1)
            response = self.client.get(ENDPOINT, params={"trade_date": TRADE_DATE})
        self.assert_error(response, 503, "QUOTE_PROVIDER_FAILED", True, "行情获取暂时失败，请稍后重试")
        self.assertNotIn("private-token", response.text)
        self.assertNotIn("/private/local", response.text)

    def test_unexpected_database_failure_returns_sanitized_503(self):
        with patch.object(self.db, "get_frozen_opening_run", side_effect=RuntimeError(
                "private-token=do-not-expose /private/local/database.py")):
            response = self.client.get(ENDPOINT, params={"trade_date": TRADE_DATE})
        self.assert_error(response, 503, "QUOTE_PROVIDER_FAILED", True)
        self.assertNotIn("private-token", response.text)
        self.assertNotIn("/private/local", response.text)

    def test_endpoint_is_read_only_and_rejects_post(self):
        response = self.client.post(ENDPOINT, params={"trade_date": TRADE_DATE})
        self.assertEqual(response.status_code, 405)

    def test_openapi_marks_trade_date_required(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        parameters = response.json()["paths"][ENDPOINT]["get"]["parameters"]
        trade_date = next(item for item in parameters if item["name"] == "trade_date")
        self.assertIs(trade_date["required"], True)

    def test_existing_route_retains_default_framework_validation_error(self):
        response = self.client.get("/api/concept/members")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(set(response.json()), {"detail"})
        errors = response.json()["detail"]
        self.assertTrue(any(error["loc"] == ["query", "concept_code"] for error in errors))

    def test_import_app_does_not_construct_fetcher_or_require_kline_config(self):
        source = """
import os
from unittest.mock import patch
import config
os.environ.pop('KLINE_API_BASE_URL', None)
config.KLINE_API_BASE_URL = ''
with patch('database.Database'), patch('intraday_fetcher.IntradayFetcher',
        side_effect=AssertionError('fetcher constructed during import')):
    from api.app import app
    assert any(route.path == '/api/opening-strength/dashboard' for route in app.routes)
"""
        result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
