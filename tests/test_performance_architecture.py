"""实时热路径的回归测试：保证优化不改变计算语义和缓存隔离。"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_calculator import calc_all_sectors_strength
from database import Database
from realtime_engine import RealtimeEngine, _result_cache_key


class PerformanceArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.rt_df = pd.DataFrame([
            {"code": "000001.SZ", "change_ratio": 1.0, "body": 0.5},
            {"code": "000002.SZ", "change_ratio": 2.0, "body": 1.5},
            {"code": "600001.SH", "change_ratio": -1.0, "body": -0.5},
            {"code": "600002.SH", "change_ratio": 0.0, "body": 0.0},
        ])

    def test_indexed_sector_calculation_keeps_expected_values(self):
        members = {
            "sector-a": ["000001.SZ", "000002.SZ"],
            "sector-b": ["600001.SH", "600002.SH"],
        }
        result = calc_all_sectors_strength(self.rt_df, members, min_member_count=0)
        rows = result.set_index("concept_code")

        self.assertEqual(rows.loc["sector-a", "s1_return"], 1.5)
        self.assertEqual(rows.loc["sector-a", "s2_breadth"], 1.0)
        self.assertEqual(rows.loc["sector-a", "s_body"], 1.0)
        self.assertEqual(rows.loc["sector-b", "s1_return"], -0.5)
        self.assertEqual(rows.loc["sector-b", "s2_breadth"], 0.0)

    def test_indicator_snapshot_uses_only_data_up_to_requested_time(self):
        engine = object.__new__(RealtimeEngine)
        series = {
            "000001.SZ": {
                "pre_close": 100.0,
                "open": 100.0,
                "trading": [
                    {"time": "09:30", "last_price": 100.0, "turnover": 10},
                    {"time": "09:31", "last_price": 101.0, "turnover": 20},
                    {"time": "09:32", "last_price": 103.0, "turnover": 30},
                    {"time": "09:33", "last_price": 102.0, "turnover": 40},
                ],
                "trading_times": ["09:30", "09:31", "09:32", "09:33"],
                "pre_market": [],
                "pre_market_times": [],
            }
        }
        row = engine._build_indicator_df(series, "09:32").iloc[0]

        self.assertAlmostEqual(row["change_ratio"], 3.0)
        self.assertAlmostEqual(row["speed"], (103 / 101 - 1) * 100)
        expected_previous_speed = (101 / 100 - 1) * 100
        self.assertAlmostEqual(row["acceleration"], row["speed"] - expected_previous_speed)
        self.assertEqual(row["amount"], 30.0)

    def test_result_cache_separates_different_top_n_requests(self):
        common = ("20260710", "10:00", True, "20260710", False)
        self.assertNotEqual(
            _result_cache_key(*common, 5),
            _result_cache_key(*common, 10),
        )

    def test_series_cache_is_bounded(self):
        original = RealtimeEngine._series_cache
        try:
            RealtimeEngine._series_cache = {
                str(i): {"fetched_at": float(i)} for i in range(20)
            }
            RealtimeEngine._trim_series_cache()
            self.assertLessEqual(len(RealtimeEngine._series_cache), 12)
            self.assertNotIn("0", RealtimeEngine._series_cache)
        finally:
            RealtimeEngine._series_cache = original

    def test_batch_member_query_matches_single_queries(self):
        db = Database()
        codes = db.get_observe_concept_codes()[:3]
        batched = db.get_concept_members_map(codes)
        for code in codes:
            self.assertEqual(batched.get(code, []), db.get_concept_members(code))


if __name__ == "__main__":
    unittest.main()
