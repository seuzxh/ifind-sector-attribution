"""Network-free compatibility checks for the shared intraday adapter."""

import os
import unittest
from unittest.mock import patch

from intraday_fetcher import IntradayFetcher


class IntradayFetcherTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"KLINE_API_BASE_URL": "https://quotes.invalid"})
        environment.start()
        self.addCleanup(environment.stop)
        self.fetcher = IntradayFetcher(workers=2)

    def test_legacy_batch_returns_empty_dict_for_empty_input_or_total_failure(self):
        self.assertEqual(self.fetcher.fetch_batch([]), {})
        with patch("intraday_fetcher.TrendFetcher.fetch_trend", side_effect=TimeoutError("fixture")):
            result = self.fetcher.fetch_batch(["600001.SH", "000001.SZ"], "20261002")
            self.assertIs(type(result), dict)
            self.assertEqual(result, {})
            self.assertIsNone(self.fetcher._fetch_one("600001.SH", "20261002"))

    def test_legacy_batch_keeps_normalized_success_when_another_symbol_fails(self):
        def fetch(code, date=None):
            self.assertEqual(date, "20261002")
            if code == "SZ000001":
                raise TimeoutError("fixture")
            self.assertEqual(code, "SH600001")
            return {
                "pre_market": [{"time": "09:15:00", "ref_price": 100}],
                "trading": [{"time": "09:30:00", "last_price": 101,
                             "avg_price": 100.5, "volume": 10, "turnover": 1010}],
            }

        with patch("intraday_fetcher.TrendFetcher.fetch_trend", side_effect=fetch):
            result = self.fetcher.fetch_batch(["600001.SH", "000001.SZ"], "20261002")
        self.assertIs(type(result), dict)
        self.assertEqual(set(result), {"600001.SH"})
        self.assertEqual(result["600001.SH"]["pre_close"], 100)
        self.assertEqual(result["600001.SH"]["trading_times"], ["09:30"])
        self.assertEqual(result["600001.SH"]["trading"], [{
            "time": "09:30", "last_price": 101, "avg_price": 100.5,
            "volume": 10, "turnover": 1010,
        }])
