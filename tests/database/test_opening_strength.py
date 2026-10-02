import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import Database


class OpeningRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(str(Path(self.temp.name) / "snapshot.db"))

    def create(self, run_id="new", trade_date="20261002"):
        self.db.create_opening_run({
            "run_id": run_id, "trade_date": trade_date, "status": "RUNNING",
            "model_version": "model-1", "config_version": "config-1",
            "started_at": "2026-10-02T09:00:00+08:00",
        })

    def validate(self, run_id="new"):
        self.db.mark_opening_run_validated(run_id, {
            "candidate_count": 1, "mapped_count": 1, "coverage_ratio": 1.0,
            "history_coverage_ratio": .75, "hub_member_date": "2026-10-01",
            "validated_at": "2026-10-02T09:01:00+08:00",
        })

    def status(self, run_id):
        with self.db._connect() as conn:
            return conn.execute("SELECT status FROM opening_premarket_run WHERE run_id=?",
                                (run_id,)).fetchone()[0]

    def freeze_old(self):
        self.create("old")
        self.validate("old")
        self.db.freeze_opening_run("old", False)

    def test_four_tables_and_partial_unique_index_exist(self):
        with self.db._connect() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"opening_premarket_run", "opening_candidate_snapshot",
                             "opening_membership_snapshot", "opening_attribution_snapshot"} <= tables)
        self.freeze_old()
        self.create()
        with self.assertRaises(sqlite3.IntegrityError), self.db._connect() as conn:
            conn.execute("UPDATE opening_premarket_run SET status='FROZEN' WHERE run_id='new'")

    def test_full_round_trip_keeps_multi_pool_provenance_and_deterministic_json(self):
        self.create()
        self.db.save_opening_candidates("new", [
            {"stock_code": "600001.SH", "stock_name": "中文股", "source_pool_id": pool,
             "source_rank": rank, "source_score": None}
            for pool, rank in (("hot_stock", 3), ("high_beta", 1))])
        self.db.save_opening_memberships("new", [
            {"stock_code": "600001.SH", "theme_code": "885001.TI", "theme_name": "主题",
             "theme_type": "CONCEPT", "source": "sector_hub_authoritative"}])
        self.db.save_opening_attributions("new", [
            {"stock_code": "600001.SH", "theme_code": "885001.TI", "theme_name": "主题",
             "theme_type": "CONCEPT", "rank": 1, "raw_score": .8, "weight": 1.0,
             "confidence": .9, "reason_codes": ["STRONG_RECENT_THEME"],
             "evidence": {"z": None, "a": "中文"}}])
        self.validate()
        self.db.freeze_opening_run("new", False)
        run = self.db.get_frozen_opening_run("20261002")
        self.assertEqual((run["status"], run["candidate_count"], run["mapped_count"]),
                         ("FROZEN", 1, 1))
        self.assertEqual(run["history_coverage_ratio"], .75)
        self.assertTrue(run["frozen_at"].endswith("+08:00"))
        rows = self.db.get_opening_candidates("new")
        self.assertEqual([(r["source_pool_id"], r["source_rank"]) for r in rows],
                         [("high_beta", 1), ("hot_stock", 3)])
        self.assertEqual(rows[0]["stock_name"], "中文股")
        self.assertEqual(self.db.get_opening_memberships("new")[0]["source"],
                         "sector_hub_authoritative")
        attrs = self.db.get_opening_attributions("new", "600001.SH")
        self.assertEqual(attrs[0]["evidence"], {"z": None, "a": "中文"})
        self.assertEqual(attrs[0]["reason_codes"], ["STRONG_RECENT_THEME"])
        self.assertEqual(self.db.get_opening_attributions("new", "600002.SH"), [])
        with self.db._connect() as conn:
            stored = conn.execute("SELECT evidence_json FROM opening_attribution_snapshot").fetchone()[0]
        self.assertEqual(stored, json.dumps({"a": "中文", "z": None}, ensure_ascii=False, sort_keys=True))

    def test_running_cannot_freeze_and_only_validated_can_freeze(self):
        self.create()
        with self.assertRaises(ValueError):
            self.db.freeze_opening_run("new", False)
        self.assertEqual(self.status("new"), "RUNNING")
        self.validate()
        self.db.freeze_opening_run("new", False)
        self.assertEqual(self.status("new"), "FROZEN")

    def test_replacement_supersedes_old_atomically(self):
        self.freeze_old()
        self.create()
        self.validate()
        self.db.freeze_opening_run("new", True)
        self.assertEqual(self.status("old"), "SUPERSEDED")
        self.assertEqual(self.db.get_frozen_opening_run("20261002")["run_id"], "new")

    def test_replacement_disabled_preserves_old(self):
        from database.opening_strength import FrozenRunExistsError
        self.freeze_old()
        self.create()
        self.validate()
        with self.assertRaises(FrozenRunExistsError):
            self.db.freeze_opening_run("new", False)
        self.assertEqual(self.status("old"), "FROZEN")
        self.assertEqual(self.status("new"), "VALIDATED")

    def test_trigger_abort_rolls_back_supersede_and_freeze(self):
        self.freeze_old()
        self.create()
        self.validate()
        with self.db._connect() as conn:
            conn.execute("""CREATE TRIGGER reject_new_freeze BEFORE UPDATE OF status
                            ON opening_premarket_run WHEN NEW.run_id='new' AND NEW.status='FROZEN'
                            BEGIN SELECT RAISE(ABORT, 'injected freeze failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected freeze failure"):
            self.db.freeze_opening_run("new", True)
        self.assertEqual(self.status("old"), "FROZEN")
        self.assertEqual(self.status("new"), "VALIDATED")
        with self.db._connect() as conn:
            frozen = [r[0] for r in conn.execute(
                "SELECT run_id FROM opening_premarket_run WHERE status='FROZEN'")]
        self.assertEqual(frozen, ["old"])

    def test_snapshot_writes_and_failure_cannot_mutate_frozen_run(self):
        self.freeze_old()
        for save in (self.db.save_opening_candidates, self.db.save_opening_memberships,
                     self.db.save_opening_attributions):
            with self.assertRaises(ValueError):
                save("old", [])
        with self.assertRaises(ValueError):
            self.db.fail_opening_run("old", "ERROR", "detail")
        self.assertEqual(self.status("old"), "FROZEN")

    def test_non_finite_json_rejects_entire_batch(self):
        self.create()
        row = {"stock_code": "600001.SH", "theme_code": "885001.TI", "theme_name": "主题",
               "theme_type": "CONCEPT", "rank": 1, "raw_score": .8, "weight": 1.0,
               "confidence": .9, "reason_codes": [], "evidence": {"bad": float("nan")}}
        with self.assertRaises(ValueError):
            self.db.save_opening_attributions("new", [row])
        self.assertEqual(self.db.get_opening_attributions("new"), [])

    def test_failed_run_retains_inputs_and_never_becomes_validated(self):
        self.create()
        self.db.fail_opening_run("new", "INVALID_INPUT", "safe summary")
        self.assertEqual(self.status("new"), "FAILED")
        self.assertIsNone(self.db.get_frozen_opening_run("20261002"))
        with self.assertRaises(ValueError):
            self.validate()


if __name__ == "__main__":
    unittest.main()
