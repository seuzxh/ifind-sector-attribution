"""Primitive-mapping persistence and atomic premarket snapshot publication."""

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo


class FrozenRunExistsError(ValueError):
    """Publication was refused because the date already has a frozen run."""


def _now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _require_status(conn, run_id, status):
    row = conn.execute("SELECT * FROM opening_premarket_run WHERE run_id=?", (run_id,)).fetchone()
    if row is None or row["status"] not in status:
        raise ValueError("Invalid opening run state")
    return row


class OpeningStrengthMixin:
    def create_opening_run(self, run: Mapping) -> None:
        if run.get("status", "RUNNING") != "RUNNING":
            raise ValueError("New opening run must be RUNNING")
        fields = ("run_id", "trade_date", "model_version", "config_version", "started_at")
        with self._connect() as conn:
            conn.execute("""INSERT INTO opening_premarket_run
                            (run_id, trade_date, model_version, config_version, started_at,
                             status, override_reason) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?)""",
                         (*[run[field] for field in fields], run.get("override_reason")))

    def _save_opening_rows(self, run_id, table, fields, rows):
        # Prepare all rows before entering a write transaction; JSON rejects NaN.
        values = [(run_id, *[row[field] for field in fields]) for row in rows]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _require_status(conn, run_id, {"RUNNING"})
            conn.executemany(f"INSERT INTO {table} (run_id, {', '.join(fields)}) "
                             f"VALUES ({', '.join('?' for _ in range(len(fields) + 1))})", values)

    def save_opening_candidates(self, run_id: str, rows: Sequence[Mapping]) -> None:
        self._save_opening_rows(run_id, "opening_candidate_snapshot",
                                ("stock_code", "stock_name", "source_pool_id", "source_rank", "source_score"),
                                [{**row, "source_score": row.get("source_score")} for row in rows])

    def save_opening_memberships(self, run_id: str, rows: Sequence[Mapping]) -> None:
        self._save_opening_rows(run_id, "opening_membership_snapshot",
                                ("stock_code", "theme_code", "theme_name", "theme_type", "source"), rows)

    def save_opening_attributions(self, run_id: str, rows: Sequence[Mapping]) -> None:
        values = [{**row, "reason_codes_json": _json(row["reason_codes"]),
                   "evidence_json": _json(row["evidence"])} for row in rows]
        self._save_opening_rows(run_id, "opening_attribution_snapshot",
                                ("stock_code", "theme_code", "theme_name", "theme_type", "rank",
                                 "raw_score", "weight", "confidence", "reason_codes_json", "evidence_json"),
                                values)

    def mark_opening_run_validated(self, run_id: str, metrics: Mapping) -> None:
        fields = ("hub_member_date", "candidate_count", "mapped_count", "coverage_ratio",
                  "history_coverage_ratio")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _require_status(conn, run_id, {"RUNNING"})
            conn.execute(f"UPDATE opening_premarket_run SET status='VALIDATED', "
                         f"{', '.join(field + '=?' for field in fields)}, validated_at=? WHERE run_id=?",
                         (*[metrics[field] for field in fields], metrics.get("validated_at", _now()), run_id))

    def freeze_opening_run(self, run_id: str, replace_existing: bool,
                           publication_policy: Callable[[str, bool], str] | None = None) -> None:
        """Run an optional primitive publication policy under the write lock.

        The policy receives the trade date and whether a frozen run exists.
        It returns the publication timestamp, or raises to reject publication.
        """
        with self._connect() as conn:
            # Lock before reading to serialize concurrent replacement decisions.
            conn.execute("BEGIN IMMEDIATE")
            row = _require_status(conn, run_id, {"VALIDATED"})
            old = conn.execute("SELECT run_id FROM opening_premarket_run "
                               "WHERE trade_date=? AND status='FROZEN'", (row["trade_date"],)).fetchone()
            frozen_at = publication_policy(row["trade_date"], old is not None) if publication_policy else _now()
            if old is not None:
                if not replace_existing:
                    raise FrozenRunExistsError("Trade date already has a frozen opening run")
                conn.execute("UPDATE opening_premarket_run SET status='SUPERSEDED' WHERE run_id=?",
                             (old["run_id"],))
            conn.execute("UPDATE opening_premarket_run SET status='FROZEN', frozen_at=? WHERE run_id=?",
                         (frozen_at, run_id))

    def fail_opening_run(self, run_id: str, failure_code: str, failure_detail: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _require_status(conn, run_id, {"RUNNING", "VALIDATED"})
            conn.execute("UPDATE opening_premarket_run SET status='FAILED', failure_code=?, "
                         "failure_detail=? WHERE run_id=?", (failure_code[:64], failure_detail[:256], run_id))

    def get_frozen_opening_run(self, trade_date: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM opening_premarket_run WHERE trade_date=? AND status='FROZEN'",
                               (trade_date,)).fetchone()
            return dict(row) if row else None

    def get_latest_frozen_opening_run(self, on_or_before: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM opening_premarket_run "
                "WHERE trade_date<=? AND status='FROZEN' "
                "ORDER BY trade_date DESC LIMIT 1",
                (on_or_before,),
            ).fetchone()
            return dict(row) if row else None

    def get_opening_candidates(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM opening_candidate_snapshot "
                    "WHERE run_id=? ORDER BY stock_code, source_pool_id", (run_id,))]

    def get_opening_memberships(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM opening_membership_snapshot "
                    "WHERE run_id=? ORDER BY stock_code, theme_code", (run_id,))]

    def get_opening_attributions(self, run_id: str, stock_code: str | None = None) -> list[dict]:
        condition, values = (" AND stock_code=?", (run_id, stock_code)) if stock_code is not None else ("", (run_id,))
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM opening_attribution_snapshot WHERE run_id=?" + condition +
                                " ORDER BY stock_code, rank, theme_code", values)
            result = []
            for row in rows:
                item = dict(row)
                item["reason_codes"] = json.loads(item.pop("reason_codes_json"))
                item["evidence"] = json.loads(item.pop("evidence_json"))
                result.append(item)
            return result
