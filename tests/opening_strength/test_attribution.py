import json
import unittest
from dataclasses import replace

from opening_strength.attribution import score_attributions
from opening_strength.models import (
    AttributionConfig, CandidateStock, HistoricalFeatures, PoolSource,
    ThemeMembership, ThemeType,
)


def candidate(code, *pools, name="股票"):
    return CandidateStock(code, name, tuple(PoolSource(pool, 1) for pool in pools))


def relation(stock, theme, name="主题"):
    return ThemeMembership(stock, theme, name,
                           ThemeType.INDUSTRY if theme.startswith("884") else ThemeType.CONCEPT)


class AttributionTests(unittest.TestCase):
    def test_literal_scores_missing_sync_confidence_and_theme_limits(self):
        candidates = (candidate("600001.SH", "high_beta"),
                      candidate("600002.SH", "recent_strong"),
                      candidate("600003.SH", "hot_stock"),
                      candidate("600004.SH", "high_beta"),
                      candidate("600005.SH", "high_beta"),
                      candidate("600006.SH", "high_beta"))
        themes = ("884001.TI", "884002.TI", "885001.TI", "885002.TI", "886001.TI")
        memberships = tuple(relation(stock.stock_code, theme)
                            for stock in candidates for theme in themes)
        history = HistoricalFeatures(
            {"884001.TI": .8, "884002.TI": .6, "885001.TI": 1,
             "885002.TI": .5, "886001.TI": .2},
            {("600001.SH", "884001.TI"): .6, ("600001.SH", "884002.TI"): .4,
             ("600001.SH", "885001.TI"): 1, ("600001.SH", "885002.TI"): None,
             ("600001.SH", "886001.TI"): .2}, 0.0)
        items = [item for item in score_attributions(candidates, memberships, history)
                 if item.stock_code == "600001.SH"]
        self.assertEqual([item.theme_code for item in items],
                         ["885001.TI", "884001.TI", "885002.TI"])
        self.assertEqual([item.rank for item in items], [1, 2, 3])
        for item, score, weight, confidence, coverage in zip(
                items, (1.0, .88, .8235294117647058),
                (.369886858137511, .325500435161010, .304612706701479),
                (1.0, 1.0, .9325), (1.0, 1.0, .85)):
            self.assertAlmostEqual(item.raw_score, score)
            self.assertAlmostEqual(item.weight, weight)
            self.assertAlmostEqual(item.confidence, confidence)
            self.assertAlmostEqual(item.evidence["feature_coverage"], coverage)
            self.assertEqual(item.evidence["peer_count"], 5)
            self.assertEqual(item.evidence["source_pool_count"], 3)
            self.assertEqual(item.evidence["peer_support"], 1.0)
            self.assertEqual(item.evidence["source_pool_diversity"], 1.0)
            json.dumps(item.evidence, allow_nan=False)
        self.assertAlmostEqual(sum(item.weight for item in items), 1.0, delta=1e-6)
        self.assertEqual(items[0].reason_codes,
                         ("HIGH_SYNCHRONY", "MULTI_POOL_SUPPORT", "PEER_CONFIRMATION",
                          "STRONG_RECENT_THEME"))
        self.assertEqual(items[2].reason_codes,
                         ("MISSING_HISTORY", "MULTI_POOL_SUPPORT", "PEER_CONFIRMATION"))
        self.assertEqual(items[2].evidence["missing_features"], ["stock_theme_sync"])

    def test_peers_are_unique_exclude_self_and_diversity_includes_all_source_pools(self):
        candidates = (candidate("600001.SH", "high_beta", "recent_strong", name="同名"),
                      candidate("600002.SH", "recent_strong", "hot_stock", name="同名"))
        memberships = (relation("600001.SH", "885001.TI", "原名"),
                       relation("600001.SH", "885001.TI", "异名"),
                       relation("600002.SH", "885001.TI", "原名"),
                       relation("600002.SH", "885001.TI", "原名"),
                       relation("600099.SH", "885001.TI"))
        history = HistoricalFeatures({"885001.TI": .8},
                                     {("600001.SH", "885001.TI"): .6}, 1.0)
        items = score_attributions(candidates, memberships, history)
        self.assertEqual(len(items), 2)
        current = items[0]
        self.assertEqual(current.stock_code, "600001.SH")
        self.assertEqual(current.evidence["peer_count"], 1)
        self.assertEqual(current.evidence["peer_support"], .2)
        self.assertEqual(current.evidence["source_pool_diversity"], 1.0)
        self.assertAlmostEqual(current.raw_score, .6)
        self.assertAlmostEqual(current.confidence, .72)
        self.assertEqual(current.reason_codes,
                         ("LOW_SUPPORT", "MULTI_POOL_SUPPORT", "STRONG_RECENT_THEME"))
        # Theme and stock identities are codes; input ordering/names cannot add votes.
        reordered = score_attributions(candidates[::-1], memberships[::-1], history)
        self.assertEqual(items, reordered)

    def test_equal_scores_sort_by_peers_then_pool_count_then_code(self):
        candidates = (candidate("600001.SH", "high_beta"),
                      candidate("600002.SH", "high_beta"),
                      candidate("600003.SH", "recent_strong"))
        memberships = tuple(relation("600001.SH", f"88500{i}.TI") for i in range(1, 5)) + (
            relation("600002.SH", "885002.TI"),
            relation("600003.SH", "885003.TI"),
            relation("600003.SH", "885004.TI"))
        history = HistoricalFeatures({f"88500{i}.TI": 1 for i in range(1, 5)}, {}, 1.0)
        config = replace(AttributionConfig(), feature_weights=(1, 0, 0, 0), max_concepts=4)
        items = [item for item in score_attributions(candidates, memberships, history, config)
                 if item.stock_code == "600001.SH"]
        self.assertEqual([item.theme_code for item in items],
                         ["885003.TI", "885004.TI", "885002.TI", "885001.TI"])

    def test_zero_scores_receive_equal_weights(self):
        stock = candidate("600001.SH")
        memberships = (relation(stock.stock_code, "884001.TI"),
                       relation(stock.stock_code, "885001.TI"),
                       relation(stock.stock_code, "886001.TI"))
        history = HistoricalFeatures({m.theme_code: 0 for m in memberships},
                                     {(m.stock_code, m.theme_code): 0 for m in memberships}, 1.0)
        items = score_attributions((stock,), memberships, history)
        self.assertEqual(len(items), 3)
        self.assertEqual([item.raw_score for item in items], [0.0, 0.0, 0.0])
        for item in items:
            self.assertAlmostEqual(item.weight, .3333333333333333)
        self.assertAlmostEqual(sum(item.weight for item in items), 1.0, delta=1e-6)

    def test_all_low_confidence_falls_back_to_one_item(self):
        stock = candidate("600001.SH", "high_beta")
        memberships = (relation(stock.stock_code, "886001.TI"),
                       relation(stock.stock_code, "884001.TI"),
                       relation(stock.stock_code, "885001.TI"))
        items = score_attributions((stock,), memberships, HistoricalFeatures({}, {}, 0.0))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].theme_code, "884001.TI")
        self.assertAlmostEqual(items[0].raw_score, .1212121212121212)
        self.assertAlmostEqual(items[0].confidence, .31416666666666665)
        self.assertAlmostEqual(items[0].evidence["feature_coverage"], .55)
        self.assertEqual(items[0].weight, 1.0)
        self.assertEqual(items[0].reason_codes,
                         ("LOW_CONFIDENCE", "LOW_SUPPORT", "MISSING_HISTORY"))
        self.assertEqual(items[0].evidence["missing_features"],
                         ["theme_recent_strength", "stock_theme_sync"])

    def test_support_saturates_and_missing_recent_reweights_available_features(self):
        candidates = tuple(candidate(f"60000{i}.SH", "high_beta") for i in range(1, 8))
        memberships = tuple(relation(stock.stock_code, "885001.TI") for stock in candidates)
        history = HistoricalFeatures({}, {("600001.SH", "885001.TI"): 1}, 0.0)
        item = score_attributions(candidates, memberships, history)[0]
        self.assertEqual(item.evidence["peer_count"], 6)
        self.assertEqual(item.evidence["peer_support"], 1.0)
        self.assertAlmostEqual(item.evidence["feature_coverage"], .7)
        self.assertAlmostEqual(item.raw_score, .8095238095238095)
        self.assertAlmostEqual(item.confidence, .7316666666666667)

    def test_reason_and_confidence_thresholds_are_inclusive(self):
        candidates = (candidate("600001.SH", "high_beta"),
                      candidate("600002.SH", "recent_strong"),
                      candidate("600003.SH", "recent_strong"))
        memberships = tuple(relation(stock.stock_code, "885001.TI") for stock in candidates)
        history = HistoricalFeatures({"885001.TI": .75},
                                     {("600001.SH", "885001.TI"): .75}, 1.0)
        items = score_attributions(candidates, memberships, history,
                                   replace(AttributionConfig(), min_confidence=.7233333333333333))
        current = items[0]
        self.assertEqual(current.reason_codes,
                         ("HIGH_SYNCHRONY", "MULTI_POOL_SUPPORT", "PEER_CONFIRMATION",
                          "STRONG_RECENT_THEME"))

    def test_empty_and_unmapped_candidates_do_not_generate_memberships(self):
        history = HistoricalFeatures({}, {}, 0.0)
        self.assertEqual(score_attributions((), (), history), ())
        self.assertEqual(score_attributions((candidate("600001.SH"),),
                                           (relation("600099.SH", "885001.TI"),), history), ())


if __name__ == "__main__":
    unittest.main()
