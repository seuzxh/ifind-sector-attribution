import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from opening_strength.quote_provider import OpeningDashboardError, OpeningQuoteProvider


SHANGHAI = ZoneInfo("Asia/Shanghai")
A, B, C = "600001.SH", "000001.SZ", "600002.SH"


class Clock:
    def __init__(self, minute="09:32", seconds=0):
        self.now = datetime.fromisoformat(f"2026-10-03T{minute}:00+08:00")
        self.seconds = seconds

    def __call__(self):
        return self.now

    def monotonic(self):
        return self.seconds


def record(*points, pre_close=100):
    return {
        "pre_close": pre_close, "open": 100,
        "trading": [{"time": time, "last_price": price, "avg_price": price - .5,
                     "turnover": 1000, "volume": 10} for time, price in points],
        "pre_market": [{"time": "09:25", "ref_price": 999,
                        "matched_vol": 10, "non_matched_vol_buy": 0,
                        "non_matched_vol_sell": 0}],
    }


class Fetcher:
    def __init__(self, data):
        self.data = data
        self.calls = []
        self.error = None

    def fetch_batch(self, codes, date=None):
        self.calls.append((codes, date))
        if self.error:
            raise self.error
        return self.data


class QuoteProviderTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.fetcher = Fetcher({
            A: record(("09:25", 999), ("09:32", 103), ("09:30", 101)),
            B: record(("09:31", 202), ("09:33", 204), pre_close=200),
        })
        self.provider = OpeningQuoteProvider(
            self.fetcher, clock=self.clock, monotonic=self.clock.monotonic)

    def test_today_fetches_sorted_deduplicated_codes_without_date(self):
        result = self.provider.load([A, B, A], "20261003")
        self.assertEqual(self.fetcher.calls, [([B, A], None)])
        self.assertEqual(result.cache_status, "fresh")

    def test_historical_date_and_distinct_candidate_sets_have_separate_caches(self):
        self.provider.load([A], "20261002")
        self.clock.seconds = 100000
        self.provider.load([A, A], "20261002")
        self.provider.load([B], "20261002")
        self.assertEqual(self.fetcher.calls, [([A], "20261002"), ([B], "20261002")])

    def test_global_union_excludes_premarket_and_resolves_down_per_stock(self):
        result = self.provider.load([A, B], "20261003", "09:32")
        self.assertEqual(result.available_times, ("09:30", "09:31", "09:32", "09:33"))
        self.assertEqual((result.snapshot_time, result.latest_time), ("09:32", "09:33"))
        self.assertEqual((result.quotes[A].last_price, result.quotes[A].quote_time), (103, "09:32"))
        self.assertEqual((result.quotes[B].last_price, result.quotes[B].quote_time), (202, "09:31"))
        self.assertEqual((result.previous_quotes[A].last_price, result.previous_quotes[B].last_price),
                         (101, 202))

    def test_request_between_global_minutes_and_after_last_minute_resolves_down(self):
        self.fetcher.data = {A: record(("09:30", 101), ("09:32", 103))}
        self.assertEqual(self.provider.load([A], "20261003", "09:31").snapshot_time, "09:30")
        self.assertEqual(self.provider.load([A], "20261003", "15:00").snapshot_time, "09:32")

    def test_opening_minute_reuses_current_and_never_uses_future_stock_points(self):
        result = self.provider.load([A, B], "20261003", "09:30")
        self.assertEqual(result.previous_quotes, result.quotes)
        self.assertEqual(set(result.quotes), {A})
        self.assertEqual(result.quotes[A].last_price, 101)

    def test_first_available_minute_after_open_has_no_invented_previous_quote(self):
        self.fetcher.data = {A: record(("09:31", 101))}
        result = self.provider.load([A], "20261003")
        self.assertEqual(result.snapshot_time, "09:31")
        self.assertEqual(result.previous_quotes, {})

    def test_no_resolvable_open_point_returns_safe_data_unavailable_error(self):
        for data, minute in (({}, None), ({A: record(("09:25", 999))}, None),
                             ({A: record(("09:31", 101))}, "09:30")):
            with self.subTest(data=data, minute=minute):
                provider = OpeningQuoteProvider(Fetcher(data), clock=self.clock)
                with self.assertRaises(OpeningDashboardError) as raised:
                    provider.load([A], "20261003", minute)
                self.assertEqual(raised.exception.code, "QUOTE_DATA_UNAVAILABLE")
                self.assertTrue(raised.exception.retryable)

    def test_today_refetches_at_fifteen_seconds_and_code_order_shares_cache(self):
        self.provider.load([A, B], "20261003")
        self.clock.seconds = 14.999
        result = self.provider.load([B, A, A], "20261003")
        self.assertEqual(result.cache_status, "hit")
        self.assertEqual(len(self.fetcher.calls), 1)
        self.clock.seconds = 15
        self.fetcher.data = {A: record(("09:34", 105))}
        result = self.provider.load([A, B], "20261003")
        self.assertEqual((result.snapshot_time, result.quotes[A].last_price), ("09:34", 105))
        self.assertEqual(len(self.fetcher.calls), 2)

    def test_failed_refresh_preserves_success_and_keeps_cache_and_lag_independent(self):
        self.fetcher.data = {A: record(("09:32", 103))}
        self.provider.load([A], "20261003")
        self.clock.seconds = 15
        self.fetcher.error = RuntimeError("secret api-key upstream payload /private/path")
        result = self.provider.load([A], "20261003")
        self.assertEqual(result.cache_status, "stale")
        self.assertFalse(result.stale_quote)
        self.assertEqual(result.quotes[A].last_price, 103)

    def test_empty_refresh_preserves_last_success_from_fetcher_partial_failure(self):
        self.provider.load([A], "20261003")
        self.clock.seconds = 15
        self.fetcher.data = {}
        result = self.provider.load([A], "20261003")
        self.assertEqual(result.cache_status, "stale")
        self.assertEqual(result.quotes[A].last_price, 103)

    def test_first_failure_does_not_expose_upstream_text_and_can_retry(self):
        self.fetcher.error = RuntimeError("secret api-key upstream payload /private/path")
        with self.assertRaises(OpeningDashboardError) as raised:
            self.provider.load([A], "20261003")
        error = raised.exception
        self.assertEqual(error.code, "QUOTE_PROVIDER_FAILED")
        self.assertTrue(error.retryable)
        self.assertNotIn("secret", str(error))
        self.assertNotIn("/private/path", error.message)
        self.fetcher.error = None
        self.assertEqual(self.provider.load([A], "20261003").quotes[A].last_price, 103)

    def test_lag_counts_only_open_minutes_and_has_strict_two_minute_boundary(self):
        for latest, now, lag in (("09:30", "09:32", False), ("09:30", "09:33", True),
                                 ("09:30", "09:25", False), ("11:30", "12:40", False),
                                 ("11:29", "13:01", False), ("11:29", "13:02", True),
                                 ("15:00", "17:00", False), ("14:58", "17:00", False),
                                 ("14:57", "17:00", True)):
            with self.subTest(latest=latest, now=now):
                clock = Clock(now)
                provider = OpeningQuoteProvider(Fetcher({A: record((latest, 101))}), clock=clock)
                result = provider.load([A], "20261003")
                self.assertEqual(result.stale_quote, lag)
                self.assertEqual(result.cache_status, "fresh")

    def test_historical_quotes_are_never_marked_as_live_lag(self):
        self.assertFalse(self.provider.load([A], "20261002").stale_quote)

    def test_live_cache_becomes_a_full_history_fetch_when_the_day_rolls_over(self):
        self.provider.load([A], "20261003")
        self.clock.now = datetime.fromisoformat("2026-10-04T09:00:00+08:00")
        self.fetcher.data = {A: record(("15:00", 105))}
        historical = self.provider.load([A], "20261003")
        self.assertEqual(historical.latest_time, "15:00")
        self.assertEqual(self.fetcher.calls, [([A], None), ([A], "20261003")])
        self.clock.seconds = 100000
        self.provider.load([A], "20261003")
        self.assertEqual(len(self.fetcher.calls), 2)

    def test_shanghai_clock_controls_today_even_when_clock_uses_utc(self):
        self.clock.now = datetime.fromisoformat("2026-10-02T23:31:00+00:00")
        self.provider.load([A], "20261003")
        self.assertEqual(self.fetcher.calls[0][1], None)

    def test_quote_slice_is_immutable_and_bad_numbers_degrade_one_stock(self):
        self.fetcher.data[B] = record(("09:31", float("nan")), pre_close=None)
        result = self.provider.load([A, B], "20261003")
        self.assertEqual(set(result.quotes), {A})
        with self.assertRaises(TypeError):
            result.quotes[A] = result.quotes[A]


class QuoteProviderConcurrencyTests(unittest.TestCase):
    def test_simultaneous_first_loads_share_one_fetch(self):
        clock, start = Clock(), threading.Barrier(7)
        entered, release = threading.Event(), threading.Event()
        fetcher = Fetcher({A: record(("09:30", 101))})
        original = fetcher.fetch_batch

        def fetch(codes, date=None):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test fetch timed out")
            return original(codes, date)

        fetcher.fetch_batch = fetch
        provider = OpeningQuoteProvider(fetcher, clock=clock, monotonic=clock.monotonic)

        def load():
            start.wait(timeout=3)
            return provider.load([A], "20261003")

        with ThreadPoolExecutor(max_workers=6) as executor:
            pending = [executor.submit(load) for _ in range(6)]
            start.wait(timeout=3)
            try:
                self.assertTrue(entered.wait(3))
            finally:
                release.set()
            results = [future.result(timeout=3) for future in pending]
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual([result.quotes[A].last_price for result in results], [101] * 6)

    def test_network_io_does_not_block_an_unrelated_cache_key(self):
        clock, entered, release = Clock(), threading.Event(), threading.Event()
        fetcher = Fetcher({A: record(("09:30", 101)), B: record(("09:30", 202))})
        original = fetcher.fetch_batch

        def fetch(codes, date=None):
            if codes == [A]:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test fetch timed out")
            return original(codes, date)

        fetcher.fetch_batch = fetch
        provider = OpeningQuoteProvider(fetcher, clock=clock)
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = executor.submit(provider.load, [A], "20261003")
            try:
                self.assertTrue(entered.wait(3))
                other = executor.submit(provider.load, [B], "20261003").result(timeout=1)
                self.assertEqual(other.quotes[B].last_price, 202)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=3).quotes[A].last_price, 101)

    def test_concurrent_refresh_reads_last_success_without_second_fetch(self):
        clock, entered, release = Clock(), threading.Event(), threading.Event()
        fetcher = Fetcher({A: record(("09:30", 101))})
        provider = OpeningQuoteProvider(fetcher, clock=clock, monotonic=clock.monotonic)
        provider.load([A], "20261003")
        clock.seconds = 15
        original = fetcher.fetch_batch

        def fetch(codes, date=None):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test fetch timed out")
            return original(codes, date)

        fetcher.fetch_batch = fetch
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = executor.submit(provider.load, [A], "20261003")
            try:
                self.assertTrue(entered.wait(3))
                result = executor.submit(provider.load, [A], "20261003").result(timeout=1)
                self.assertEqual(result.quotes[A].last_price, 101)
            finally:
                release.set()
            pending.result(timeout=3)
        self.assertEqual(len(fetcher.calls), 2)
