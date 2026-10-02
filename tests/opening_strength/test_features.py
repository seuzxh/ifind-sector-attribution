import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from database import Database
from opening_strength.features import (
    DailyHistoryFeatureProvider, compounded_return, rank_percentiles,
)
from opening_strength.attribution import score_attributions
from opening_strength.models import CandidateStock, PoolSource, ThemeMembership, ThemeType


def quotation(code, changes, start=date(2026, 9, 12)):
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
            {"code": "885001.TI", "trade_date": day.replace("-", ""), "change_ratio": i}
            for i, day in enumerate(quotation("885001.TI", [0] * 20)["time"], 1)])
        theme = quotation("885001.TI", [20, None, 999, float("nan"), 999])
        theme["time"] = ["2026-10-01", "2026-09-10", "2026-10-01", "2026-09-11", "2026-10-02"]
        client = HistoryClient([theme, quotation("600001.SH", list(range(1, 21)))])
        result = DailyHistoryFeatureProvider(self.db, client).build(
            "20261002", (self.stock,), self.memberships("885001.TI"))
        self.assertEqual(result.theme_recent_strength, {"885001.TI": 1.0})
        self.assertAlmostEqual(result.stock_theme_sync[("600001.SH", "885001.TI")], 1.0)
        self.assertEqual(result.coverage_ratio, 1.0)
        saved = self.db.get_daily_kline("885001.TI", "20261001", "20261002")
        self.assertEqual(saved[0]["trade_date"], "20261001")
        self.assertEqual(saved[0]["change_ratio"], 20)
        self.assertFalse(any(row["trade_date"] == "20261002" for row in saved))

    def test_sync_uses_twenty_common_observations_and_maps_negative_correlation(self):
        client = HistoryClient([quotation("885001.TI", [80] + list(range(1, 21)), date(2026, 9, 11)),
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

    def test_stale_success_cannot_receive_positive_attribution_evidence(self):
        stock = CandidateStock("600001.SH", "股票", tuple(
            PoolSource(pool, 1) for pool in ("high_beta", "recent_strong", "hot_stock")))
        memberships = self.memberships("885001.TI", "885002.TI")
        client = HistoryClient([
            quotation("885001.TI", list(range(1, 21)), date(2026, 8, 30)),
            quotation("885002.TI", list(range(1, 21)), date(2026, 9, 10)),
            quotation("600001.SH", list(range(1, 21)), date(2026, 9, 10))])
        history = DailyHistoryFeatureProvider(self.db, client).build(
            "20260930", (stock,), memberships)
        self.assertIsNone(history.theme_recent_strength["885001.TI"])
        self.assertIsNone(history.stock_theme_sync[(stock.stock_code, "885001.TI")])
        self.assertEqual(history.theme_recent_strength["885002.TI"], 1.0)
        self.assertAlmostEqual(history.stock_theme_sync[(stock.stock_code, "885002.TI")], 1.0)
        self.assertEqual(history.coverage_ratio, 0.5)
        items = {item.theme_code: item for item in score_attributions((stock,), memberships, history)}
        self.assertIn("MISSING_HISTORY", items["885001.TI"].reason_codes)
        self.assertNotIn("STRONG_RECENT_THEME", items["885001.TI"].reason_codes)
        self.assertNotIn("HIGH_SYNCHRONY", items["885001.TI"].reason_codes)
        self.assertLess(items["885001.TI"].weight, items["885002.TI"].weight)

    def test_unequal_endpoints_are_missing_even_with_latest_cached_values(self):
        for stale_code, expected_recent, expected_coverage in (
                ("600001.SH", 1.0, 0.5), ("885001.TI", None, 0.0)):
            with self.subTest(stale_code=stale_code):
                self.db.save_daily_kline([
                    {"code": stale_code, "trade_date": "20260929", "change_ratio": 20}])
                client = HistoryClient([
                    quotation(code, list(range(1, 21))[:19 if code == stale_code else 20],
                              date(2026, 9, 10))
                    for code in ("600001.SH", "885001.TI")])
                history = DailyHistoryFeatureProvider(self.db, client).build(
                    "20260930", (self.stock,), self.memberships("885001.TI"))
                self.assertEqual(history.theme_recent_strength["885001.TI"], expected_recent)
                self.assertIsNone(history.stock_theme_sync[(self.stock.stock_code, "885001.TI")])
                self.assertEqual(history.coverage_ratio, expected_coverage)

    def test_missing_latest_return_is_not_replaced_by_previous_valid_or_cached_return(self):
        for code in ("600001.SH", "885001.TI"):
            for missing in (None, float("nan")):
                with self.subTest(code=code, missing=missing):
                    self.db.save_daily_kline([
                        {"code": code, "trade_date": "20261001", "change_ratio": 20}])
                    client = HistoryClient([
                        quotation(symbol, list(range(1, 21)) + [missing if symbol == code else 21],
                                  date(2026, 9, 11))
                        for symbol in ("600001.SH", "885001.TI")])
                    history = DailyHistoryFeatureProvider(self.db, client).build(
                        "20261002", (self.stock,), self.memberships("885001.TI"))
                    self.assertEqual(history.theme_recent_strength["885001.TI"],
                                     None if code == "885001.TI" else 1.0)
                    self.assertIsNone(history.stock_theme_sync[(self.stock.stock_code, "885001.TI")])
                    self.assertEqual(history.coverage_ratio, 0.0 if code == "885001.TI" else 0.5)

    def test_weekend_targets_anchor_to_friday_and_ignore_later_rows(self):
        for target in ("20261003", "20261004", "20261005"):
            with self.subTest(target=target):
                client = HistoryClient([
                    quotation("885001.TI", list(range(1, 21)) + [99, 99], date(2026, 9, 13)),
                    quotation("600001.SH", list(range(1, 21)) + [-99, -99], date(2026, 9, 13))])
                history = DailyHistoryFeatureProvider(self.db, client).build(
                    target, (self.stock,), self.memberships("885001.TI"))
                self.assertEqual(history.theme_recent_strength["885001.TI"], 1.0)
                self.assertAlmostEqual(history.stock_theme_sync[(self.stock.stock_code, "885001.TI")], 1.0)
                self.assertEqual(history.coverage_ratio, 1.0)

    def test_injected_completed_session_handles_holiday_gap(self):
        client = HistoryClient([
            quotation(code, list(range(1, 21)), date(2026, 9, 11))
            for code in ("600001.SH", "885001.TI")])
        default = DailyHistoryFeatureProvider(self.db, client).build(
            "20261009", (self.stock,), self.memberships("885001.TI"))
        self.assertEqual(default.coverage_ratio, 0.0)
        requested_dates = []

        def completed_session(target):
            requested_dates.append(target)
            return "2026-09-30"

        history = DailyHistoryFeatureProvider(
            self.db, client, completed_session_resolver=completed_session).build(
                "20261009", (self.stock,), self.memberships("885001.TI"))
        self.assertEqual(requested_dates, ["20261009"])
        self.assertEqual(history.theme_recent_strength["885001.TI"], 1.0)
        self.assertAlmostEqual(history.stock_theme_sync[(self.stock.stock_code, "885001.TI")], 1.0)
        self.assertEqual(history.coverage_ratio, 1.0)

    def test_recent_return_windows_end_at_completed_session(self):
        client = HistoryClient([
            quotation("885001.TI", [1] * 20 + [-99, -99], date(2026, 9, 13)),
            quotation("885002.TI", [0] * 22, date(2026, 9, 13))])
        history = DailyHistoryFeatureProvider(self.db, client).build(
            "20261005", (self.stock,), self.memberships("885001.TI", "885002.TI"))
        self.assertEqual(history.theme_recent_strength, {"885001.TI": 1.0, "885002.TI": 0.0})
        self.assertEqual(history.coverage_ratio, 0.5)


if __name__ == "__main__":
    unittest.main()
