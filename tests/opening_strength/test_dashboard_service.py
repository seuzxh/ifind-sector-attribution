import sqlite3
import sys
import tempfile
import unittest
import weakref
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from database import Database
from opening_strength.dashboard_service import OpeningDashboardError, OpeningDashboardService
from opening_strength.quote_provider import OpeningQuoteProvider, QuoteSlice
from opening_strength.realtime_models import StockQuote
from tests.opening_strength.test_quote_provider import Clock, Fetcher, record


A, B, C = "600001.SH", "000001.SZ", "600002.SH"


class Provider:
    def __init__(self):
        self.calls = []
        self.quotes = {A: StockQuote(A, "09:32", 100, 103, 102, 1000),
                       B: StockQuote(B, "09:32", 100, 101, 100, 2000)}
        self.previous = {A: StockQuote(A, "09:30", 100, 101, 100, 1000),
                         B: StockQuote(B, "09:30", 100, 99, 99, 2000)}
        self.cache_status = "fresh"
        self.stale_quote = False

    def load(self, codes, trade_date, snapshot_time=None):
        self.calls.append((tuple(codes), trade_date, snapshot_time))
        minute = "09:30" if snapshot_time and snapshot_time < "09:32" else "09:32"
        quotes = self.previous if minute == "09:30" else self.quotes
        previous = quotes if minute == "09:30" else self.previous
        return QuoteSlice(("09:30", "09:32"), minute, "09:32", quotes, previous,
                          self.cache_status, self.stale_quote)


class DashboardServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(str(Path(temp.name) / "opening.db"))
        self.provider = Provider()
        self.clock = Clock()
        self.service = OpeningDashboardService(
            self.db, self.provider, clock=self.clock, monotonic=self.clock.monotonic)

    def create_run(self, run_id="frozen", trade_date="20261003", theme="885001.TI",
                   third=False, status="FROZEN", extra_attributions=()):
        self.db.create_opening_run({
            "run_id": run_id, "trade_date": trade_date, "model_version": "model-1",
            "config_version": "config-1", "started_at": "2026-10-03T09:00:00+08:00",
        })
        candidates = [
            {"stock_code": A, "stock_name": "甲", "source_pool_id": "883926.TI", "source_rank": 1},
            {"stock_code": A, "stock_name": "甲", "source_pool_id": "883910.TI", "source_rank": 2},
            {"stock_code": B, "stock_name": "乙", "source_pool_id": "883409.TI", "source_rank": 1},
        ]
        if third:
            candidates.append({"stock_code": C, "stock_name": "丙", "source_pool_id": "883910.TI",
                               "source_rank": 3})
        self.db.save_opening_candidates(run_id, candidates)
        self.db.save_opening_memberships(run_id, [
            {"stock_code": code, "theme_code": "885999.TI", "theme_name": "未选中的关系",
             "theme_type": "CONCEPT", "source": "sector_hub_authoritative"} for code in (A, B)])
        attrs = [self.attribution(code, theme) for code in (A, B)]
        if third:
            attrs.append(self.attribution(C, theme))
        self.db.save_opening_attributions(run_id, attrs + list(extra_attributions))
        if status == "RUNNING":
            return
        if status == "FAILED":
            self.db.fail_opening_run(run_id, "TEST_FAILURE", "safe fixture")
            return
        self.db.mark_opening_run_validated(run_id, {
            "candidate_count": 3 if third else 2, "mapped_count": 3 if third else 2,
            "coverage_ratio": 1, "history_coverage_ratio": 1, "hub_member_date": "2026-10-02",
        })
        if status == "FROZEN":
            self.db.freeze_opening_run(run_id, replace_existing=True)

    def attribution(self, code, theme, rank=1):
        return {"stock_code": code, "theme_code": theme, "theme_name": "已冻结题材",
                "theme_type": "INDUSTRY" if theme.startswith("884") else "CONCEPT",
                "rank": rank, "raw_score": .8, "weight": .5, "confidence": .9,
                "reason_codes": ["MULTI_POOL_SUPPORT", "STRONG_RECENT_THEME"], "evidence": {}}

    def test_filters_full_membership_over_300_for_both_theme_types_before_top10(self):
        themes = ["884001.TI", "885002.TI"] + [f"885{i:03d}.TI" for i in range(3, 16)]
        extras = [self.attribution(A, code, rank=i + 2) for i, code in enumerate(themes[1:])]
        self.create_run(theme=themes[0], extra_attributions=extras)
        for code in themes:
            size = 301 if code in themes[:2] else 300
            self.db.save_concept_members(code, [
                {"stock_code": f"{i:06d}.SZ", "stock_name": str(i)} for i in range(size)
            ], "20261002")
        # A newer membership snapshot must not change historical eligibility.
        self.db.save_concept_members(themes[2], [
            {"stock_code": f"{i:06d}.SZ", "stock_name": str(i)} for i in range(400)
        ], "20261004")
        result = self.service.build("20261003")
        self.assertEqual(len(result.themes), 10)
        self.assertEqual(result.theme_count, 13)
        self.assertFalse(set(themes[:2]) & {row.theme_code for row in result.themes})
        self.assertTrue(all(row.total_member_count == 300 for row in result.themes))
        self.assertEqual(self.db.get_opening_theme_member_counts(themes, "2026-10-02")[themes[2]], 300)
        self.assertEqual(len(self.db.get_opening_attributions("frozen")), 16)

    def test_unknown_member_count_is_explicit_and_does_not_hide_theme(self):
        self.create_run()
        result = self.service.build("20261003")
        self.assertIsNone(result.themes[0].total_member_count)

    def test_candidates_collapse_sources_and_only_attributions_form_relationships(self):
        self.create_run()
        result = self.service.build("20261003")
        self.assertEqual((result.run_id, result.candidate_count, result.theme_count), ("frozen", 2, 1))
        self.assertEqual(self.provider.calls, [((B, A), "20261003", None)])
        self.assertEqual([theme.theme_code for theme in result.themes], ["885001.TI"])
        theme = result.themes[0]
        self.assertEqual((theme.level, theme.momentum_1m, theme.breadth_delta_1m), (2, 2, .5))
        self.assertEqual(theme.attributed_stock_count, 2)
        self.assertEqual(theme.contributors[0].source_pool_ids, ("883910.TI", "883926.TI"))
        self.assertEqual(theme.contributors[0].reason_codes,
                         ("MULTI_POOL_SUPPORT", "STRONG_RECENT_THEME"))

    def test_no_frozen_run_returns_not_found_without_fetch(self):
        for status in ("RUNNING", "VALIDATED", "FAILED"):
            self.create_run(status.lower(), status=status)
        with self.assertRaises(OpeningDashboardError) as raised:
            self.service.build("20261003")
        self.assertEqual(raised.exception.code, "SNAPSHOT_NOT_FOUND")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(self.provider.calls, [])

    def test_fallback_loads_latest_frozen_run_on_or_before_requested_date(self):
        self.create_run(run_id="sep-30", trade_date="20260930")

        result = self.service.build("20261003", fallback_to_previous=True)

        self.assertEqual((result.trade_date, result.run_id, result.mode),
                         ("20260930", "sep-30", "historical"))
        self.assertEqual(self.provider.calls, [((B, A), "20260930", None)])

    def test_frozen_run_ignores_newer_running_and_validated_rows(self):
        self.create_run()
        self.create_run("new-running", status="RUNNING", theme="885002.TI")
        self.create_run("new-validated", status="VALIDATED", theme="885003.TI")
        result = self.service.build("20261003")
        self.assertEqual((result.run_id, result.themes[0].theme_code), ("frozen", "885001.TI"))

    def test_only_884_885_886_attributions_from_frozen_candidates_are_aggregated(self):
        extras = [self.attribution(A, code, rank) for rank, code in enumerate(
            ("884001.TI", "886001.TI", "700001.TI", "881001.TI", "883926.TI",
             "861001.TI", "custom-static"), 2)]
        extras.append(self.attribution(C, "885777.TI", 1))
        self.create_run(extra_attributions=extras)
        result = self.service.build("20261003")
        self.assertEqual({theme.theme_code for theme in result.themes},
                         {"884001.TI", "885001.TI", "886001.TI"})

    def test_replaced_run_bypasses_existing_three_second_result(self):
        self.create_run()
        first = self.service.build("20261003")
        self.create_run("replacement", theme="886007.TI", third=True)
        replacement = self.service.build("20261003")
        self.assertEqual((first.run_id, replacement.run_id), ("frozen", "replacement"))
        self.assertEqual((replacement.candidate_count, replacement.themes[0].theme_code), (3, "886007.TI"))
        with self.db._connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM opening_premarket_run WHERE run_id='frozen'")
                             .fetchone()[0], "SUPERSEDED")

    def test_result_cache_uses_resolved_minute_and_expires_at_three_seconds(self):
        self.create_run()
        first = self.service.build("20261003", "09:30")
        self.clock.now += timedelta(seconds=1)
        self.clock.seconds = 2.999
        hit = self.service.build("20261003", "09:31")
        self.assertEqual(first.generated_at, hit.generated_at)
        self.assertEqual(hit.snapshot_time, "09:30")
        self.clock.seconds = 3
        refreshed = self.service.build("20261003", "09:31")
        self.assertNotEqual(first.generated_at, refreshed.generated_at)
        later = self.service.build("20261003", "09:32")
        self.assertEqual(later.themes[0].level, 2)

    def test_partial_quotes_keep_missing_contributors_and_label_data_health(self):
        self.create_run(third=True)
        self.provider.quotes.pop(B)
        result = self.service.build("20261003")
        self.assertEqual(result.data_health, .3333)
        theme = result.themes[0]
        self.assertEqual((theme.data_health, theme.valid_quote_count), (.3333, 1))
        self.assertIn("数据不足", theme.risk_tags)
        self.assertEqual({row.stock_code for row in theme.contributors if not row.has_quote}, {B, C})
        self.assertEqual((result.acceleration_theme_codes, result.breadth_theme_codes), ((), ()))

    def test_cached_serialized_result_is_detached_from_caller_mutation(self):
        self.create_run()
        result = self.service.build("20261003")
        payload = result.to_dict()
        payload["themes"][0]["contributors"][0]["reason_codes"].append("caller mutation")
        cached = self.service.build("20261003")
        self.assertEqual(cached.themes[0].contributors[0].reason_codes,
                         ("MULTI_POOL_SUPPORT", "STRONG_RECENT_THEME"))

    def test_expired_results_release_quotes_from_old_snapshots(self):
        self.create_run()
        old_quote = self.provider.previous[A]
        retained = weakref.ref(old_quote)
        self.service.build("20261003", "09:30")
        self.provider.previous = {}
        del old_quote
        self.clock.seconds = 3
        self.service.build("20261003", "09:32")
        self.assertIsNone(retained())

    def test_cache_provenance_changes_immediately_without_fabricating_lag(self):
        self.create_run()
        self.service.build("20261003")
        self.provider.cache_status = "stale"
        stale_cache = self.service.build("20261003")
        self.assertEqual(stale_cache.cache_status, "stale")
        self.assertNotIn("行情滞后", stale_cache.themes[0].risk_tags)
        self.provider.stale_quote = True
        lagging = self.service.build("20261003")
        self.assertIn("行情滞后", lagging.themes[0].risk_tags)

    def test_history_uses_same_aggregation_and_cannot_write_database(self):
        self.create_run(trade_date="20261002")
        original, writes = self.db._connect, []

        @contextmanager
        def read_only():
            with original() as conn:
                def authorize(action, arg1, arg2, dbname, trigger):
                    if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                        writes.append((action, arg1))
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                conn.set_authorizer(authorize)
                yield conn

        with patch.object(self.db, "_connect", read_only):
            result = self.service.build("20261002")
        self.assertEqual((result.mode, result.themes[0].level), ("historical", 2))
        self.assertEqual(writes, [])
        self.assertEqual(self.provider.calls[0][1], "20261002")

    def test_live_mode_and_generated_timestamp_follow_shanghai_clock(self):
        self.create_run()
        self.clock.now = datetime.fromisoformat("2026-10-02T23:32:00+00:00")
        result = self.service.build("20261003")
        self.assertEqual(result.mode, "realtime")
        self.assertEqual(result.generated_at, "2026-10-03T07:32:00+08:00")

    def test_midnight_changes_mode_even_while_result_cache_is_within_ttl(self):
        self.create_run()
        self.clock.now = datetime.fromisoformat("2026-10-03T23:59:59+08:00")
        self.assertEqual(self.service.build("20261003").mode, "realtime")
        self.clock.now += timedelta(seconds=1)
        self.clock.seconds = 1
        self.assertEqual(self.service.build("20261003").mode, "historical")

    def test_build_never_invokes_premarket_feature_or_attribution_functions(self):
        self.create_run()
        forbidden, called = {"features.py", "attribution.py", "snapshot_service.py"}, []
        previous_profile = sys.getprofile()

        def profile(frame, event, arg):
            path = Path(frame.f_code.co_filename)
            if event == "call" and path.parent.name == "opening_strength" and path.name in forbidden:
                called.append((path.name, frame.f_code.co_name))

        try:
            sys.setprofile(profile)
            result = self.service.build("20261003")
        finally:
            sys.setprofile(previous_profile)
        self.assertEqual(called, [])
        self.assertEqual(result.theme_count, 1)

    def test_real_provider_excludes_premarket_and_preserves_missing_previous_rankings(self):
        self.create_run()
        fetcher = Fetcher({A: record(("09:31", 103)), B: record(("09:31", 101))})
        provider = OpeningQuoteProvider(fetcher, clock=self.clock)
        result = OpeningDashboardService(self.db, provider, clock=self.clock).build("20261003")
        self.assertEqual(result.available_times, ("09:31",))
        self.assertEqual(result.themes[0].level, 2)
        self.assertIsNone(result.themes[0].momentum_1m)
        self.assertIsNone(result.themes[0].breadth_delta_1m)
        self.assertEqual((result.acceleration_theme_codes, result.breadth_theme_codes), ((), ()))

    def test_premarket_only_real_provider_returns_data_unavailable(self):
        self.create_run()
        provider = OpeningQuoteProvider(Fetcher({A: record(), B: record()}), clock=self.clock)
        with self.assertRaises(OpeningDashboardError) as raised:
            OpeningDashboardService(self.db, provider, clock=self.clock).build("20261003")
        self.assertEqual(raised.exception.code, "QUOTE_DATA_UNAVAILABLE")

    def test_quote_failure_logs_date_and_run_without_upstream_details(self):
        self.create_run()
        fetcher = Fetcher({})
        fetcher.error = RuntimeError("secret api-key upstream /private/path")
        provider = OpeningQuoteProvider(fetcher, clock=self.clock)
        service = OpeningDashboardService(self.db, provider, clock=self.clock)
        with self.assertLogs("opening_strength.dashboard_service", level="WARNING") as captured:
            with self.assertRaises(OpeningDashboardError) as raised:
                service.build("20261003")
        self.assertEqual(raised.exception.code, "QUOTE_PROVIDER_FAILED")
        output = " ".join(captured.output)
        self.assertIn("20261003", output)
        self.assertIn("frozen", output)
        self.assertIn("QUOTE_PROVIDER_FAILED", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("/private/path", output)
