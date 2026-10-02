import json
import unittest
from dataclasses import FrozenInstanceError, replace

from opening_strength.realtime_models import AttributedStock, OpeningDashboard, StockQuote
from opening_strength.realtime_aggregation import aggregate_themes, build_rankings


def attribution(code, weight=1.0, theme="885001.TI", pools=("high_beta",)):
    return AttributedStock(code, "股票" + code, theme, "题材" + theme,
                           "CONCEPT", weight, .812345678,
                           ("PEER_CONFIRMATION",), pools)


def quote(code, last, minute="09:31", pre_close=100.0):
    return StockQuote(code, minute, pre_close, last, 101.23456789, 2.3456789)


class RealtimeAggregationTests(unittest.TestCase):
    def fixture(self):
        stocks = (
            attribution("600001.SH", .5, pools=("high_beta", "recent_strong")),
            attribution("600002.SH", .3, pools=("hot_stock",)),
            attribution("600003.SH", .2), attribution("600004.SH", .4),
            attribution("600005.SH", .1), attribution("600006.SH", .9),
            attribution("600001.SH", .5, "886001.TI", ("high_beta", "recent_strong")),
            attribution("600002.SH", .7, "886001.TI", ("hot_stock",)),
        )
        current = {code: quote(code, last) for code, last in (
            ("600001.SH", 104), ("600002.SH", 102), ("600003.SH", 99),
            ("600004.SH", 101), ("600005.SH", 101))}
        previous = {code: quote(code, last, "09:30") for code, last in (
            ("600001.SH", 102), ("600002.SH", 99), ("600003.SH", 98),
            ("600004.SH", 100), ("600005.SH", 101))}
        return stocks, current, previous

    def test_two_themes_use_only_valid_weights_for_literal_formulas(self):
        first, second = aggregate_themes(*self.fixture())
        self.assertEqual(first.theme_code, "885001.TI")
        for field, want in (
                ("level", 29 / 15), ("momentum_1m", 5 / 3),
                ("up_ratio", .8), ("breadth_delta_1m", .4),
                ("support_weight", 13 / 15), ("data_health", 5 / 6),
                ("top1_concentration", 20 / 31), ("top3_concentration", 30 / 31)):
            self.assertAlmostEqual(getattr(first, field), want)
        self.assertEqual((first.attributed_stock_count, first.valid_quote_count,
                          first.supporting_count, first.source_pool_diversity), (6, 5, 4, 3))
        self.assertEqual(first.risk_tags, ())
        self.assertTrue(first.rankable)
        self.assertAlmostEqual(second.level, 17 / 6)
        self.assertAlmostEqual(second.momentum_1m, 31 / 12)
        self.assertEqual(second.up_ratio, 1)
        self.assertEqual(second.breadth_delta_1m, .5)
        self.assertEqual(second.support_weight, 1)
        self.assertEqual(second.data_health, 1)
        self.assertEqual(second.source_pool_diversity, 3)

    def test_contributors_preserve_metadata_negative_values_and_missing_rows(self):
        first = aggregate_themes(*self.fixture())[0]
        self.assertEqual([row.stock_code for row in first.contributors],
                         ["600001.SH", "600002.SH", "600004.SH", "600005.SH",
                          "600003.SH", "600006.SH"])
        for row, want in zip(first.contributors[:-1], (2, .6, .4, .1, -.2)):
            self.assertAlmostEqual(row.contribution, want)
            self.assertAlmostEqual(row.positive_contribution, max(want, 0))
        first_row, missing = first.contributors[0], first.contributors[-1]
        self.assertAlmostEqual(first_row.return_pct, 4)
        self.assertEqual(first_row.attribution_weight, .5)
        self.assertEqual(first_row.confidence, .812345678)
        self.assertEqual(first_row.reason_codes, ("PEER_CONFIRMATION",))
        self.assertEqual(first_row.source_pool_ids, ("high_beta", "recent_strong"))
        self.assertEqual((first_row.quote_time, first_row.pre_close, first_row.last_price),
                         ("09:31", 100, 104))
        self.assertEqual(first_row.avg_price, 101.23456789)
        self.assertEqual(first_row.turnover, 2.3456789)
        self.assertTrue(first_row.has_quote)
        self.assertFalse(missing.has_quote)
        self.assertIsNone(missing.return_pct)
        self.assertIsNone(missing.contribution)
        self.assertIsNone(missing.quote_time)

    def test_all_negative_returns_have_no_positive_concentration_or_diversity(self):
        stocks = (attribution("600001.SH"), attribution("600002.SH"))
        quotes = {"600001.SH": quote("600001.SH", 99),
                  "600002.SH": quote("600002.SH", 98)}
        theme = aggregate_themes(stocks, quotes, quotes)[0]
        self.assertAlmostEqual(theme.level, -1.5)
        self.assertEqual(theme.support_weight, 0)
        self.assertEqual(theme.up_ratio, 0)
        self.assertEqual(theme.source_pool_diversity, 0)
        self.assertIsNone(theme.top1_concentration)
        self.assertIsNone(theme.top3_concentration)
        self.assertEqual(theme.risk_tags, ("低支撑",))

    def test_invalid_prices_and_missing_quotes_do_not_poison_health_or_json(self):
        for pre_close, last in ((0, 100), (-1, 100), (float("nan"), 100),
                                (float("inf"), 100), (100, float("nan")),
                                (100, float("inf"))):
            with self.subTest(pre_close=pre_close, last=last):
                item = attribution("600001.SH")
                theme = aggregate_themes((item,), {item.stock_code: quote(
                    item.stock_code, last, pre_close=pre_close)}, {})[0]
                self.assertEqual(theme.valid_quote_count, 0)
                self.assertEqual(theme.data_health, 0)
                self.assertFalse(theme.rankable)
                self.assertIsNone(theme.level)
                self.assertIsNone(theme.up_ratio)
                self.assertIsNone(theme.support_weight)
                self.assertEqual(theme.risk_tags, ("单股驱动", "数据不足"))
                json.dumps(theme.to_dict(), allow_nan=False)

    def test_no_quotes_preserves_every_stock_with_null_metrics(self):
        stocks = (attribution("600002.SH"), attribution("600001.SH"))
        theme = aggregate_themes(stocks, {}, {})[0]
        for field in ("level", "momentum_1m", "up_ratio", "breadth_delta_1m",
                      "support_weight", "top1_concentration", "top3_concentration"):
            self.assertIsNone(getattr(theme, field))
        self.assertEqual(theme.attributed_stock_count, 2)
        self.assertEqual([row.stock_code for row in theme.contributors],
                         ["600001.SH", "600002.SH"])
        self.assertEqual(theme.risk_tags, ("低支撑", "数据不足"))

    def test_zero_valid_weight_sum_is_unrankable_without_losing_quote_count(self):
        item = attribution("600001.SH", 0)
        quotes = {item.stock_code: quote(item.stock_code, 103)}
        theme = aggregate_themes((item,), quotes, quotes)[0]
        self.assertEqual(theme.valid_quote_count, 1)
        self.assertEqual(theme.up_ratio, 1)
        self.assertIsNone(theme.level)
        self.assertIsNone(theme.support_weight)
        self.assertFalse(theme.rankable)

    def test_duplicate_stock_rows_do_not_add_votes_or_weights(self):
        stocks, quotes, previous = self.fixture()
        self.assertEqual(aggregate_themes(stocks + (stocks[0], stocks[0]), quotes, previous),
                         aggregate_themes(stocks, quotes, previous))

    def test_group_identity_includes_theme_name_and_type(self):
        item = attribution("600001.SH")
        stocks = (item, replace(item, theme_name="其他名称"),
                  replace(item, theme_type="INDUSTRY"))
        self.assertEqual(len(aggregate_themes(stocks, {}, {})), 3)

    def test_opening_minute_uses_its_own_previous_baseline(self):
        item = attribution("600001.SH")
        quotes = {item.stock_code: quote(item.stock_code, 103, "09:30")}
        theme = aggregate_themes((item,), quotes, quotes)[0]
        self.assertEqual(theme.momentum_1m, 0)
        self.assertEqual(theme.breadth_delta_1m, 0)

    def test_uneven_minute_points_already_sliced_by_provider_are_consumed_as_given(self):
        stocks = (attribution("600001.SH"), attribution("600002.SH"))
        current = {"600001.SH": quote("600001.SH", 103, "09:32"),
                   "600002.SH": quote("600002.SH", 99, "09:30")}
        previous = {"600001.SH": quote("600001.SH", 101, "09:31"),
                    "600002.SH": quote("600002.SH", 99, "09:30")}
        theme = aggregate_themes(stocks, current, previous)[0]
        self.assertAlmostEqual(theme.level, 1)
        self.assertAlmostEqual(theme.momentum_1m, 1)
        self.assertEqual(theme.breadth_delta_1m, 0)

    def test_previous_health_and_weights_are_calculated_independently(self):
        stocks, quotes, previous = self.fixture()
        theme = aggregate_themes(stocks, quotes, {"600001.SH": previous["600001.SH"]})[0]
        self.assertAlmostEqual(theme.momentum_1m, -1 / 15)
        self.assertAlmostEqual(theme.breadth_delta_1m, -.2)
        unknown = aggregate_themes(stocks, quotes, {})[0]
        self.assertIsNone(unknown.momentum_1m)
        self.assertIsNone(unknown.breadth_delta_1m)

    def test_risk_thresholds_are_inclusive_for_concentration_strict_for_health(self):
        stocks = tuple(attribution(f"60000{i}.SH") for i in range(1, 6))
        # Exact integer returns of 700, 300, 0 produce top-1 concentration 0.70.
        quotes = {"600001.SH": quote("600001.SH", 8, pre_close=1),
                  "600002.SH": quote("600002.SH", 4, pre_close=1),
                  "600003.SH": quote("600003.SH", 1, pre_close=1)}
        healthy = aggregate_themes(stocks, quotes, quotes)[0]
        self.assertEqual(healthy.top1_concentration, .7)
        self.assertEqual(healthy.data_health, .6)
        self.assertEqual(healthy.risk_tags, ("高度集中",))
        degraded = aggregate_themes(stocks, {"600001.SH": quotes["600001.SH"]},
                                    quotes, stale_quote=True)[0]
        self.assertEqual(degraded.risk_tags, ("低支撑", "高度集中", "数据不足", "行情滞后"))
        single = aggregate_themes(stocks[:1], quotes, quotes)[0]
        self.assertEqual(single.risk_tags, ("单股驱动", "高度集中"))

    def test_contribution_ties_use_stock_code(self):
        stocks = (attribution("600002.SH"), attribution("600001.SH"))
        quotes = {row.stock_code: quote(row.stock_code, 100) for row in stocks}
        self.assertEqual([row.stock_code for row in aggregate_themes(stocks, quotes, {})[0].contributors],
                         ["600001.SH", "600002.SH"])

    def test_serialization_rounds_only_output_and_lists_nested_values(self):
        first, second = aggregate_themes(*self.fixture())
        dashboard = OpeningDashboard("20261003", "run-1", "historical", "09:31", "09:31",
                                     ("09:30", "09:31"), 6, 2, 5 / 6, (first, second),
                                     (second.theme_code, first.theme_code),
                                     (second.theme_code, first.theme_code), "2026-10-03T09:31:00+08:00", "fresh")
        payload = dashboard.to_dict()
        self.assertEqual(payload["data_health"], .8333)
        self.assertEqual(payload["themes"][0]["level"], 1.9333)
        self.assertEqual(payload["themes"][0]["contributors"][0]["confidence"], .8123)
        self.assertEqual(payload["themes"][0]["contributors"][0]["avg_price"], 101.2346)
        self.assertEqual(payload["themes"][0]["contributors"][0]["source_pool_ids"],
                         ["high_beta", "recent_strong"])
        self.assertEqual(payload["available_times"], ["09:30", "09:31"])
        self.assertNotEqual(first.level, payload["themes"][0]["level"])
        json.dumps(payload, allow_nan=False)
        for value, field in ((first, "level"), (first.contributors[0], "contribution"),
                             (dashboard, "data_health"), (self.fixture()[0][0], "confidence"),
                             (self.fixture()[1]["600001.SH"], "last_price")):
            with self.assertRaises(FrozenInstanceError):
                setattr(value, field, 0)

    def test_empty_inputs_remain_empty(self):
        self.assertEqual(aggregate_themes((), {}, {}), ())
        self.assertEqual(build_rankings(()), ((), (), ()))

    def test_main_ranking_orders_rankable_level_momentum_then_code(self):
        base = aggregate_themes(*self.fixture())[0]
        themes = (
            replace(base, theme_code="885009.TI", level=None, momentum_1m=None),
            replace(base, theme_code="885008.TI", level=-1, momentum_1m=50),
            replace(base, theme_code="885004.TI", level=1, momentum_1m=None),
            replace(base, theme_code="885003.TI", level=1, momentum_1m=-1),
            replace(base, theme_code="885002.TI", level=1, momentum_1m=2),
            replace(base, theme_code="885001.TI", level=1, momentum_1m=2),
            replace(base, theme_code="885007.TI", level=2, momentum_1m=-50),
        )
        ordered, _, _ = build_rankings(themes)
        self.assertEqual([row.theme_code for row in ordered],
                         ["885007.TI", "885001.TI", "885002.TI", "885003.TI",
                          "885004.TI", "885008.TI", "885009.TI"])
        self.assertEqual(ordered, build_rankings(themes[::-1])[0])

    def test_auxiliary_rankings_enforce_health_quote_count_metrics_and_stable_ties(self):
        base = aggregate_themes(*self.fixture())[0]
        themes = (
            replace(base, theme_code="885009.TI", valid_quote_count=1,
                    data_health=1, momentum_1m=100, breadth_delta_1m=1),
            replace(base, theme_code="885008.TI", data_health=.59999,
                    momentum_1m=100, breadth_delta_1m=1),
            replace(base, theme_code="885007.TI", valid_quote_count=2, data_health=.6,
                    momentum_1m=-1, breadth_delta_1m=-.2),
            replace(base, theme_code="885006.TI", momentum_1m=None, breadth_delta_1m=None),
            replace(base, theme_code="885005.TI", momentum_1m=None, breadth_delta_1m=.5),
            replace(base, theme_code="885004.TI", momentum_1m=3, breadth_delta_1m=None),
            replace(base, theme_code="885003.TI", momentum_1m=2, breadth_delta_1m=.4),
            replace(base, theme_code="885002.TI", momentum_1m=2, breadth_delta_1m=.4),
            replace(base, theme_code="885001.TI", momentum_1m=1, breadth_delta_1m=.6),
        )
        _, acceleration, breadth = build_rankings(themes)
        self.assertEqual(acceleration, ("885004.TI", "885002.TI", "885003.TI", "885001.TI", "885007.TI"))
        self.assertEqual(breadth, ("885001.TI", "885005.TI", "885002.TI", "885003.TI", "885007.TI"))
        self.assertEqual(build_rankings(themes), build_rankings(themes[::-1]))


if __name__ == "__main__":
    unittest.main()
