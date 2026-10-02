import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from database import Database
from opening_strength.features import (
    DailyHistoryFeatureProvider, compounded_return, rank_percentiles,
)
from opening_strength.models import CandidateStock, ThemeMembership, ThemeType


def quotation(code, changes, start=date(2026, 9, 1)):
    return {"thscode": code,
            "time": [(start + timedelta(days=i)).isoformat() for i in range(len(changes))],
            "table": {"preClose": [100] * len(changes),
                      "close": [101] * len(changes), "changeRatio": changes}}


class HistoryClient:
    def __init__(self, tables=(), fail_codes=(), payload=None):
        self.tables = tables
        self.fail_codes = set(fail_codes)
        self.payload = payload
        self.calls = []

    def get_history_quotation(self, codes, start_date, end_date, indicators):
        self.calls.append((tuple(codes), start_date, end_date, indicators))
        if self.fail_codes.intersection(codes):
            raise OSError("offline")
        if self.payload is not None:
            return self.payload
        return {"errorcode": 0, "tables": [t for t in self.tables if t["thscode"] in codes]}


class FeatureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Database(str(Path(temporary.name) / "history.db"))
        self.stock = CandidateStock("600001.SH", "股票", ())

    def memberships(self, *themes):
        return tuple(ThemeMembership(self.stock.stock_code, code, code, ThemeType.CONCEPT)
                     for code in themes)

    def test_compounded_return_uses_latest_valid_observations(self):
        # Addition would give 0.1, whereas 1.1 * .9 * 1.1 - 1 is .089.
        self.assertAlmostEqual(compounded_return([90, 10, None, -10, 10], 3), 0.089)
        self.assertAlmostEqual(compounded_return([10, -10, 10], 2), -0.01)
        self.assertAlmostEqual(compounded_return([10, -10, 10], 1), 0.1)
        self.assertIsNone(compounded_return([None, 1], 3))

    def test_percentiles_use_average_ranks_for_ties(self):
        self.assertEqual(rank_percentiles({"a": 0, "b": 1, "c": 1, "d": 3}),
                         {"a": 0.0, "b": 0.5, "c": 0.5, "d": 1.0})
        self.assertEqual(rank_percentiles({"a": 0}), {"a": 1.0})
        self.assertEqual(rank_percentiles({}), {})

    def test_four_return_percentiles_combine_with_literal_weights(self):
        client = HistoryClient([quotation("885001.TI", [1] * 20),
                                quotation("885002.TI", [-1] * 19 + [2]),
                                quotation("886001.TI", [0] * 20)])
        result = DailyHistoryFeatureProvider(self.db, client).build(
            "20261002", (self.stock,), self.memberships("885001.TI", "885002.TI", "886001.TI"))
        self.assertAlmostEqual(result.theme_recent_strength["885001.TI"], 0.8)
        self.assertAlmostEqual(result.theme_recent_strength["885002.TI"], 0.4)
        self.assertAlmostEqual(result.theme_recent_strength["886001.TI"], 0.3)
        self.assertEqual(result.coverage_ratio, 0.5)

    def test_invalid_duplicate_and_target_dates_are_excluded_and_cache_is_merged(self):
        self.db.save_daily_kline([
            {"code": "885001.TI", "trade_date": f"202609{i:02}", "change_ratio": i}
            for i in range(1, 21)])
        theme = quotation("885001.TI", [20, None, 999, float("nan"), 999], date(2026, 9, 20))
        theme["time"] = ["2026-09-20", "2026-09-21", "2026-09-20", "2026-09-22", "2026-10-02"]
        client = HistoryClient([theme, quotation("600001.SH", list(range(1, 21)))])
        result = DailyHistoryFeatureProvider(self.db, client).build(
            "20261002", (self.stock,), self.memberships("885001.TI"))
        self.assertEqual(result.theme_recent_strength, {"885001.TI": 1.0})
        self.assertAlmostEqual(result.stock_theme_sync[("600001.SH", "885001.TI")], 1.0)
        self.assertEqual(result.coverage_ratio, 1.0)
        saved = self.db.get_daily_kline("885001.TI", "20260920", "20261002")
        self.assertEqual(saved[0]["trade_date"], "20260920")
        self.assertEqual(saved[0]["change_ratio"], 20)
        self.assertFalse(any(row["trade_date"] == "20261002" for row in saved))

    def test_sync_uses_twenty_common_observations_and_maps_negative_correlation(self):
        client = HistoryClient([quotation("885001.TI", [80] + list(range(1, 21)), date(2026, 8, 31)),
                                quotation("600001.SH", [-i for i in range(1, 21)])])
        result = DailyHistoryFeatureProvider(self.db, client).build(
            "20261002", (self.stock,), self.memberships("885001.TI"))
        self.assertAlmostEqual(result.stock_theme_sync[("600001.SH", "885001.TI")], 0.0)

    def test_insufficient_common_data_and_constant_series_are_missing(self):
        for stock_changes in (list(range(1, 10)), [1] * 20):
            with self.subTest(stock_changes=stock_changes):
                client = HistoryClient([quotation("885001.TI", list(range(1, 21))),
                                        quotation("600001.SH", stock_changes)])
                result = DailyHistoryFeatureProvider(self.db, client).build(
                    "20261002", (self.stock,), self.memberships("885001.TI"))
                self.assertIsNone(result.stock_theme_sync[("600001.SH", "885001.TI")])

    def test_fetch_failure_and_invalid_response_do_not_use_stale_cache(self):
        self.db.save_daily_kline([
            {"code": code, "trade_date": f"202609{i:02}", "change_ratio": i}
            for code in ("600001.SH", "885001.TI") for i in range(1, 21)])
        for client in (HistoryClient(fail_codes=("600001.SH",)),
                       HistoryClient(payload={"errorcode": -1, "tables": []}),
                       HistoryClient(payload={"tables": []}),
                       HistoryClient(payload={"tables": "invalid"})):
            with self.subTest(client=client):
                result = DailyHistoryFeatureProvider(self.db, client).build(
                    "20261002", (self.stock,), self.memberships("885001.TI"))
                self.assertIsNone(result.theme_recent_strength["885001.TI"])
                self.assertIsNone(result.stock_theme_sync[("600001.SH", "885001.TI")])
                self.assertEqual(result.coverage_ratio, 0.0)

    def test_fetch_uses_exact_window_indicators_and_fifty_code_batches(self):
        candidates = tuple(CandidateStock(f"600{i:03}.SH", "", ()) for i in range(52))
        client = HistoryClient()
        DailyHistoryFeatureProvider(self.db, client).build(
            "20261002", candidates, self.memberships("885001.TI"))
        self.assertEqual([len(call[0]) for call in client.calls], [50, 3])
        for _, start, end, indicators in client.calls:
            self.assertEqual((start, end, indicators),
                             ("2026-08-03", "2026-10-01", "preClose,close,changeRatio"))

    def test_insufficient_twenty_day_history_and_empty_inputs_have_zero_coverage(self):
        client = HistoryClient([quotation("885001.TI", [1] * 19)])
        provider = DailyHistoryFeatureProvider(self.db, client)
        result = provider.build("20261002", (self.stock,), self.memberships("885001.TI"))
        self.assertIsNone(result.theme_recent_strength["885001.TI"])
        self.assertEqual(result.coverage_ratio, 0.0)
        empty = provider.build("20261002", (), ())
        self.assertEqual(empty.theme_recent_strength, {})
        self.assertEqual(empty.stock_theme_sync, {})
        self.assertEqual(empty.coverage_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
