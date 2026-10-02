import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch
from zoneinfo import ZoneInfo

from database import Database
from opening_strength.attribution import score_attributions
from opening_strength.models import AttributionConfig, HistoricalFeatures


class RecordingDatabase(Database):
    """Record observable lifecycle state after real database writes."""
    def __init__(self, path):
        self.states = []
        super().__init__(path)

    def record(self, run_id):
        with self._connect() as conn:
            status = conn.execute("SELECT status FROM opening_premarket_run WHERE run_id=?",
                                  (run_id,)).fetchone()[0]
        self.states.append(status)

    def create_opening_run(self, run):
        super().create_opening_run(run)
        self.record(run["run_id"])

    def mark_opening_run_validated(self, run_id, metrics):
        super().mark_opening_run_validated(run_id, metrics)
        self.record(run_id)

    def freeze_opening_run(self, run_id, replace_existing, **kwargs):
        super().freeze_opening_run(run_id, replace_existing, **kwargs)
        self.record(run_id)

    def fail_opening_run(self, run_id, code, detail):
        super().fail_opening_run(run_id, code, detail)
        self.record(run_id)


class PoolProvider:
    def __init__(self, rows):
        self.rows = rows
        self.empty_pool = None
        self.error = None

    def resolve(self, spec, trade_date):
        if self.error is not None:
            raise self.error
        return () if spec.pool_id == self.empty_pool else self.rows


class MembershipStore:
    def __init__(self, codes):
        self.codes = codes
        self.names = {"884001.TI": "行业", "885001.TI": "概念一", "886001.TI": "概念二"}

    def get_concept_names(self):
        return self.names

    def get_concept_members_map(self, theme_codes):
        return {theme: [{"stock_code": code} for code in self.codes] for theme in theme_codes}

    def get_latest_member_date(self):
        return "2026-10-01"


class HistoryProvider:
    def __init__(self):
        self.missing = False

    def build(self, trade_date, candidates, memberships):
        if self.missing:
            return HistoricalFeatures({}, {}, 0.0)
        return HistoricalFeatures({m.theme_code: .8 for m in memberships},
                                  {(m.stock_code, m.theme_code): .6 for m in memberships}, 1.0)


class SnapshotServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = RecordingDatabase(str(Path(self.temp.name) / "snapshot.db"))
        self.pool = PoolProvider((("600001.SH", "甲"), ("600002.SH", "乙")))
        self.store = MembershipStore(("600001.SH", "600002.SH"))
        self.history = HistoryProvider()
        self.config = AttributionConfig()
        self.now = datetime(2026, 10, 2, 9, 29, tzinfo=ZoneInfo("Asia/Shanghai"))

    def service(self, clock=None):
        from opening_strength.snapshot_service import OpeningPremarketService
        return OpeningPremarketService(self.db, self.pool, self.store, self.history,
                                       self.config, clock or (lambda: self.now))

    def run_row(self, run_id):
        with self.db._connect() as conn:
            return dict(conn.execute("SELECT * FROM opening_premarket_run WHERE run_id=?",
                                     (run_id,)).fetchone())

    def assert_failure_preserves_old(self, expected_code, configure, scorer=None):
        from opening_strength.snapshot_service import PremarketRunError
        old = self.service().run_and_freeze("20261002")
        old_details = self.db.get_opening_attributions(old.run_id)
        configure()
        context = patch("opening_strength.snapshot_service.score_attributions", scorer) if scorer else None
        if context:
            context.start()
            self.addCleanup(context.stop)
        with self.assertRaises(PremarketRunError) as raised:
            self.service().run_and_freeze("20261002")
        row = self.run_row(raised.exception.run_id)
        self.assertEqual((row["status"], row["failure_code"]), ("FAILED", expected_code))
        self.assertNotEqual(row["run_id"], old.run_id)
        self.assertEqual(self.db.get_frozen_opening_run("20261002")["run_id"], old.run_id)
        self.assertEqual(self.db.get_opening_attributions(old.run_id), old_details)
        return row

    def test_freezes_real_snapshots_with_metrics_provenance_and_exact_states(self):
        result = self.service().run_and_freeze("20261002")
        self.assertEqual(self.db.states, ["RUNNING", "VALIDATED", "FROZEN"])
        self.assertEqual((result.trade_date, result.status, result.candidate_count, result.mapped_count),
                         ("20261002", "FROZEN", 2, 2))
        self.assertEqual((result.coverage_ratio, result.history_coverage_ratio), (1.0, 1.0))
        run = self.run_row(result.run_id)
        self.assertEqual(run["hub_member_date"], "2026-10-01")
        self.assertEqual((run["model_version"], run["config_version"]),
                         ("opening-attribution-v1", "opening-default-v1"))
        self.assertEqual(run["started_at"], "2026-10-02T09:29:00+08:00")
        self.assertEqual(run["validated_at"], "2026-10-02T09:29:00+08:00")
        candidates = self.db.get_opening_candidates(result.run_id)
        self.assertEqual(len(candidates), 6)
        self.assertEqual({r["source_pool_id"] for r in candidates},
                         {"high_beta", "recent_strong", "hot_stock"})
        self.assertEqual({r["source_rank"] for r in candidates if r["stock_code"] == "600002.SH"}, {2})
        members = self.db.get_opening_memberships(result.run_id)
        self.assertEqual(len(members), 6)
        self.assertEqual({r["source"] for r in members}, {"sector_hub_authoritative"})
        attrs = self.db.get_opening_attributions(result.run_id, "600001.SH")
        self.assertEqual([r["theme_code"] for r in attrs], ["884001.TI", "885001.TI", "886001.TI"])
        self.assertEqual([r["rank"] for r in attrs], [1, 2, 3])
        for row in attrs:
            self.assertAlmostEqual(row["raw_score"], .6)
            self.assertAlmostEqual(row["confidence"], .72)
            self.assertAlmostEqual(row["weight"], 1 / 3)
            self.assertEqual(row["reason_codes"],
                             ["LOW_SUPPORT", "MULTI_POOL_SUPPORT", "STRONG_RECENT_THEME"])

    def test_identical_replay_keeps_all_snapshot_values(self):
        first = self.service().run_and_freeze("20261002")
        self.now = self.now.replace(second=30)
        second = self.service().run_and_freeze("20261002")
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(self.run_row(first.run_id)["status"], "SUPERSEDED")
        for getter in (self.db.get_opening_candidates, self.db.get_opening_memberships,
                       self.db.get_opening_attributions):
            clean = lambda run: [{k: v for k, v in row.items() if k != "run_id"} for row in getter(run)]
            self.assertEqual(clean(first.run_id), clean(second.run_id))

    def test_empty_pool_fails_only_new_run(self):
        self.assert_failure_preserves_old("EMPTY_SOURCE_POOL",
                                         lambda: setattr(self.pool, "empty_pool", "recent_strong"))

    def test_pool_failure_codes_distinguish_upstream_invalid_and_empty(self):
        from opening_strength.source_pools import SourcePoolError
        from opening_strength.snapshot_service import PremarketRunError
        for reason, expected in (("upstream_error", "SOURCE_POOL_REQUEST_FAILED"),
                                 ("missing_fields", "INVALID_SOURCE_POOL_RESPONSE"),
                                 ("empty_pool", "EMPTY_SOURCE_POOL")):
            with self.subTest(reason=reason):
                self.pool.error = SourcePoolError("high_beta", reason)
                with self.assertRaises(PremarketRunError) as raised:
                    self.service().run_and_freeze("20261002")
                self.assertEqual(self.run_row(raised.exception.run_id)["failure_code"], expected)

    def test_membership_coverage_0899_fails_only_new_run(self):
        def configure():
            self.pool.rows = tuple((f"{600000 + i}.SH", "股票") for i in range(1000))
            self.store.codes = tuple(f"{600000 + i}.SH" for i in range(899))
        self.assert_failure_preserves_old("LOW_MEMBERSHIP_COVERAGE", configure)

    def test_membership_coverage_0900_is_accepted_and_unmapped_candidate_is_saved(self):
        self.pool.rows = tuple((f"{600000 + i}.SH", "股票") for i in range(10))
        self.store.codes = tuple(f"{600000 + i}.SH" for i in range(9))
        result = self.service().run_and_freeze("20261002")
        self.assertEqual((result.candidate_count, result.mapped_count, result.coverage_ratio), (10, 9, .9))
        self.assertEqual(len(self.db.get_opening_candidates(result.run_id)), 30)
        self.assertEqual(self.db.get_opening_attributions(result.run_id, "600009.SH"), [])

    def test_invalid_attribution_prefix_fails_only_new_run(self):
        def invalid(*args):
            items = score_attributions(*args)
            return (replace(items[0], theme_code="700001.TI"), *items[1:])
        self.assert_failure_preserves_old("INVALID_THEME_CODE", lambda: None, invalid)

    def test_two_industries_fail_only_new_run(self):
        def configure():
            self.store.names["884002.TI"] = "行业二"
            self.config = replace(self.config, max_industries=2)
        self.assert_failure_preserves_old("THEME_LIMIT_EXCEEDED", configure)

    def test_three_concepts_fail_only_new_run(self):
        def configure():
            self.store.names["885002.TI"] = "概念三"
            self.config = replace(self.config, max_concepts=3)
        self.assert_failure_preserves_old("THEME_LIMIT_EXCEEDED", configure)

    def test_weight_sum_outside_tolerance_fails_only_new_run(self):
        def invalid(*args):
            items = score_attributions(*args)
            return (replace(items[0], weight=items[0].weight + .000002), *items[1:])
        self.assert_failure_preserves_old("INVALID_ATTRIBUTION_WEIGHT", lambda: None, invalid)

    def test_missing_model_version_fails_only_new_run(self):
        self.assert_failure_preserves_old("MISSING_RUN_METADATA",
                                         lambda: setattr(self, "config", replace(self.config, model_version="")))

    def test_missing_config_version_fails_only_new_run(self):
        self.assert_failure_preserves_old("MISSING_RUN_METADATA",
                                         lambda: setattr(self, "config", replace(self.config, config_version="")))

    def test_missing_history_freezes_with_degradation_evidence(self):
        self.history.missing = True
        result = self.service().run_and_freeze("20261002")
        self.assertEqual(result.history_coverage_ratio, 0.0)
        for row in self.db.get_opening_attributions(result.run_id):
            self.assertIn("MISSING_HISTORY", row["reason_codes"])
            self.assertEqual(row["evidence"]["missing_features"],
                             ["theme_recent_strength", "stock_theme_sync"])

    def test_at_0930_today_replacement_fails_only_new_run(self):
        self.assert_failure_preserves_old("REPLACEMENT_AFTER_OPEN",
                                         lambda: setattr(self, "now", self.now.replace(hour=9, minute=30)))

    def test_explicit_force_replacement_after_open_records_reason(self):
        old = self.service().run_and_freeze("20261002")
        self.now = self.now.replace(hour=9, minute=30)
        new = self.service().run_and_freeze("20261002", force_replace=True)
        self.assertEqual(self.run_row(old.run_id)["status"], "SUPERSEDED")
        self.assertEqual(self.run_row(new.run_id)["override_reason"], "FORCE_REPLACE")

    def test_historical_replacement_ignores_clock_guard(self):
        old = self.service().run_and_freeze("20261001")
        self.now = self.now.replace(hour=15, minute=0)
        new = self.service().run_and_freeze("20261001")
        self.assertEqual(self.run_row(old.run_id)["status"], "SUPERSEDED")
        self.assertEqual(self.db.get_frozen_opening_run("20261001")["run_id"], new.run_id)

    def test_replacement_checks_clock_again_after_slow_input_work(self):
        from opening_strength.snapshot_service import PremarketRunError
        old = self.service().run_and_freeze("20261002")
        times = iter((self.now, self.now, self.now.replace(minute=30)))
        with self.assertRaises(PremarketRunError) as raised:
            self.service(lambda: next(times)).run_and_freeze("20261002")
        self.assertEqual(self.run_row(raised.exception.run_id)["failure_code"], "REPLACEMENT_AFTER_OPEN")
        self.assertEqual(self.run_row(old.run_id)["status"], "FROZEN")

    def test_write_lock_wait_crossing_0930_rejects_automatic_replacement(self):
        from opening_strength.snapshot_service import PremarketRunError
        old = self.service().run_and_freeze("20261002")
        old_details = self.db.get_opening_attributions(old.run_id)
        self.now = self.now.replace(second=59)
        freeze_waiting = Event()
        blocker_active = Event()
        original_connect = self.db._connect
        original_freeze = self.db.freeze_opening_run

        @contextmanager
        def traced_connect():
            with original_connect() as conn:
                conn.set_trace_callback(lambda sql: freeze_waiting.set()
                                        if sql == "BEGIN IMMEDIATE" and blocker_active.is_set() else None)
                yield conn

        def delayed_freeze(run_id, replace_existing, **kwargs):
            with sqlite3.connect(self.db.db_path, check_same_thread=False) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                blocker_active.set()

                def release_after_cutoff():
                    if not freeze_waiting.wait(2):
                        blocker.rollback()
                        raise AssertionError("Publication did not attempt to acquire the write lock")
                    self.now = self.now.replace(minute=30, second=1)
                    blocker.commit()

                with ThreadPoolExecutor(max_workers=1) as workers:
                    released = workers.submit(release_after_cutoff)
                    try:
                        return original_freeze(run_id, replace_existing, **kwargs)
                    finally:
                        released.result(timeout=3)

        with patch.object(self.db, "_connect", traced_connect), \
                patch.object(self.db, "freeze_opening_run", delayed_freeze):
            with self.assertRaises(PremarketRunError) as raised:
                self.service().run_and_freeze("20261002")
        failed = self.run_row(raised.exception.run_id)
        self.assertEqual((failed["status"], failed["failure_code"]),
                         ("FAILED", "REPLACEMENT_AFTER_OPEN"))
        self.assertIsNone(failed["frozen_at"])
        self.assertEqual(self.run_row(old.run_id)["status"], "FROZEN")
        self.assertEqual(self.db.get_frozen_opening_run("20261002")["run_id"], old.run_id)
        self.assertEqual(self.db.get_opening_attributions(old.run_id), old_details)

    def test_exception_detail_is_bounded_and_never_leaks_raw_response_or_tokens(self):
        row = self.assert_failure_preserves_old("SOURCE_POOL_REQUEST_FAILED", lambda: setattr(
            self.pool, "error", RuntimeError("ACCESS_TOKEN=secret raw_api_response " + "x" * 1000)))
        self.assertLessEqual(len(row["failure_code"]), 64)
        self.assertLessEqual(len(row["failure_detail"]), 256)
        self.assertNotIn("secret", row["failure_detail"])
        self.assertNotIn("raw_api_response", row["failure_detail"])

    def test_failure_record_write_error_still_raises_safe_domain_error(self):
        from opening_strength.snapshot_service import PremarketRunError
        old = self.service().run_and_freeze("20261002")
        self.pool.error = RuntimeError("ACCESS_TOKEN=secret")
        with self.db._connect() as conn:
            conn.execute("""CREATE TRIGGER reject_failure_record BEFORE UPDATE OF status
                            ON opening_premarket_run WHEN NEW.status='FAILED'
                            BEGIN SELECT RAISE(ABORT, 'raw_response=secret'); END""")
        with self.assertRaises(PremarketRunError) as raised:
            self.service().run_and_freeze("20261002")
        self.assertEqual(raised.exception.failure_code, "SOURCE_POOL_REQUEST_FAILED")
        self.assertNotIn("secret", str(raised.exception))
        self.assertIn("Failure status could not be recorded", raised.exception.failure_detail)
        self.assertEqual(self.run_row(old.run_id)["status"], "FROZEN")


if __name__ == "__main__":
    unittest.main()
