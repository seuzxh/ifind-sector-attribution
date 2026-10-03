"""Read frozen attribution snapshots and build a read-only opening dashboard."""

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from math import isfinite
from threading import Lock
from time import monotonic as system_monotonic
from zoneinfo import ZoneInfo

from config import is_a_share_code, is_a_share_concept

from .quote_provider import OpeningDashboardError, OpeningQuoteProvider
from .realtime_aggregation import aggregate_themes, build_rankings
from .realtime_models import AttributedStock, OpeningDashboard, StockContribution, ThemeSnapshot


SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ResultEntry:
    payload: str
    created_at: float
    quote_identity: tuple


def _deserialize(payload: str) -> OpeningDashboard:
    data = json.loads(payload)
    themes = []
    for theme in data["themes"]:
        contributors = []
        for row in theme["contributors"]:
            row["reason_codes"] = tuple(row["reason_codes"])
            row["source_pool_ids"] = tuple(row["source_pool_ids"])
            contributors.append(StockContribution(**row))
        theme["contributors"] = tuple(contributors)
        theme["risk_tags"] = tuple(theme["risk_tags"])
        themes.append(ThemeSnapshot(**theme))
    data["themes"] = tuple(themes)
    for field in ("available_times", "acceleration_theme_codes", "breadth_theme_codes"):
        data[field] = tuple(data[field])
    return OpeningDashboard(**data)


class OpeningDashboardService:
    def __init__(self, db, quote_provider=None, *, clock=None, monotonic=None):
        self._db = db
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._monotonic = monotonic or system_monotonic
        self._provider = (quote_provider if quote_provider is not None else
                          OpeningQuoteProvider(clock=self._clock, monotonic=self._monotonic))
        self._lock = Lock()
        self._cache: dict[tuple[str, str], _ResultEntry] = {}

    def _now(self):
        now = self._clock()
        return now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now.astimezone(SHANGHAI)

    def build(self, trade_date: str, snapshot_time: str | None = None,
              fallback_to_previous: bool = False) -> OpeningDashboard:
        # Always resolve the current frozen version before consulting a result cache.
        run = self._db.get_frozen_opening_run(trade_date)
        if run is None and fallback_to_previous:
            run = self._db.get_latest_frozen_opening_run(trade_date)
        if run is None or run["status"] != "FROZEN":
            raise OpeningDashboardError("SNAPSHOT_NOT_FOUND", "该日期尚未生成盘前冻结快照", False)
        trade_date = run["trade_date"]
        run_id = run["run_id"]
        names, pools = {}, {}
        for row in self._db.get_opening_candidates(run_id):
            code = row["stock_code"]
            if is_a_share_code(code):
                names.setdefault(code, row["stock_name"])
                pools.setdefault(code, set()).add(row["source_pool_id"])
        codes = tuple(sorted(names))
        attributions = []
        for row in self._db.get_opening_attributions(run_id):
            code, theme = row["stock_code"], row["theme_code"]
            if (code not in names or not is_a_share_concept(theme)
                    or not theme.startswith(("884", "885", "886"))):
                continue
            # Repository reason_codes are already JSON-decoded; retain their order.
            attributions.append(AttributedStock(
                code, names[code], theme, row["theme_name"], row["theme_type"],
                row["weight"], row["confidence"], tuple(row["reason_codes"]), tuple(sorted(pools[code])),
            ))
        member_counts = self._db.get_opening_theme_member_counts(
            [row.theme_code for row in attributions], run["hub_member_date"] or trade_date)
        attributions = [row for row in attributions
                        if member_counts.get(row.theme_code, 0) <= 300]
        try:
            quotes = self._provider.load(codes, trade_date, snapshot_time)
        except OpeningDashboardError as error:
            logger.warning("Opening dashboard unavailable trade_date=%s run_id=%s code=%s",
                           trade_date, run_id, error.code)
            raise
        key = (run_id, quotes.snapshot_time)
        timestamp = self._now()
        mode = "realtime" if trade_date == timestamp.strftime("%Y%m%d") else "historical"
        # Successful refreshes and lag changes must not hide behind a cached result.
        quote_identity = (mode, tuple(sorted(member_counts.items())),
                          quotes.available_times, quotes.latest_time, quotes.stale_quote,
                          tuple(sorted(quotes.quotes.items())), tuple(sorted(quotes.previous_quotes.items())))
        with self._lock:
            now = self._monotonic()
            self._cache = {cached_key: entry for cached_key, entry in self._cache.items()
                           if now - entry.created_at < 3}
            cached = self._cache.get(key)
            if (cached is not None and now - cached.created_at < 3
                    and cached.quote_identity == quote_identity):
                return replace(_deserialize(cached.payload), cache_status=quotes.cache_status)
            aggregated = aggregate_themes(
                attributions, quotes.quotes, quotes.previous_quotes, stale_quote=quotes.stale_quote)
            themes, acceleration, breadth = build_rankings(tuple(
                replace(theme, total_member_count=member_counts.get(theme.theme_code))
                for theme in aggregated))
            valid_codes = {code for code in codes if code in quotes.quotes
                           and isfinite(quotes.quotes[code].pre_close)
                           and quotes.quotes[code].pre_close > 0
                           and isfinite(quotes.quotes[code].last_price)}
            dashboard = OpeningDashboard(
                trade_date, run_id, mode,
                quotes.snapshot_time, quotes.latest_time, quotes.available_times, len(codes), len(aggregated),
                len(valid_codes) / len(codes) if codes else 0.0, themes, acceleration, breadth,
                timestamp.isoformat(timespec="seconds"), quotes.cache_status,
            )
            payload = json.dumps(dashboard.to_dict(), ensure_ascii=False, allow_nan=False)
            self._cache[key] = _ResultEntry(payload, now, quote_identity)
            return _deserialize(payload)
