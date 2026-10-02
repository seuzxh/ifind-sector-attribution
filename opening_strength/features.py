"""Completed daily returns for premarket theme strength and synchrony."""

from datetime import datetime, timedelta
from math import fsum, isfinite, prod
from statistics import StatisticsError, correlation

from .models import DEFAULT_ATTRIBUTION_CONFIG, HistoricalFeatures


RETURN_WINDOWS = (1, 3, 5, 20)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if isfinite(value) else None


def compounded_return(changes, window):
    """Compound the latest N finite percentage changes, or mark missing."""
    valid = [number for value in changes if (number := _number(value)) is not None]
    if len(valid) < window:
        return None
    return prod(1 + value / 100 for value in valid[-window:]) - 1


def rank_percentiles(returns):
    """Average ranks, ascending from weakest to strongest; singleton is 1."""
    ordered = sorted(returns, key=returns.get)
    count = len(ordered)
    if count == 1:
        return {ordered[0]: 1.0}
    result = {}
    start = 0
    while start < count:
        end = start + 1
        while end < count and returns[ordered[end]] == returns[ordered[start]]:
            end += 1
        percentile = ((start + end - 1) / 2) / (count - 1)
        result.update((code, percentile) for code in ordered[start:end])
        start = end
    return result


def _date(value):
    return datetime.strptime(value.replace("-", ""), "%Y%m%d").strftime("%Y%m%d")


def _preceding_weekday(trade_date):
    """Conservative default; inject an exchange calendar for holiday gaps."""
    completed = datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=1)
    while completed.weekday() >= 5:
        completed -= timedelta(days=1)
    return completed.strftime("%Y%m%d")


def _parse_table(entry, start, end):
    """Parse the hub's thscode/time/table arrays; first duplicate date wins."""
    times, table = entry.get("time"), entry.get("table")
    if not isinstance(times, list) or not isinstance(table, dict):
        return None
    fields = {"preClose": "pre_close", "close": "close", "changeRatio": "change_ratio"}
    if any(not isinstance(table.get(field), list) or len(table[field]) != len(times)
           for field in fields):
        return None
    records = {}
    for index, value in enumerate(times):
        try:
            day = _date(value)
        except (AttributeError, ValueError, TypeError):
            continue
        if start <= day <= end and day not in records:
            records[day] = {"code": entry["thscode"], "trade_date": day,
                            **{target: _number(table[field][index])
                               for field, target in fields.items()}}
    return records


class DailyHistoryFeatureProvider:
    """Fetch daily cache data anchored to one completed session.

    completed_session_resolver receives YYYYMMDD and returns YYYYMMDD or
    YYYY-MM-DD. The default preceding weekday is deliberately conservative
    on exchange holidays; a calendar adapter can resolve the exact session.
    """

    def __init__(self, db, client, config=DEFAULT_ATTRIBUTION_CONFIG,
                 completed_session_resolver=None):
        self.db = db
        self.client = client
        self.config = config
        self.completed_session_resolver = completed_session_resolver or _preceding_weekday

    def build(self, trade_date, candidates, memberships):
        trade_day = datetime.strptime(_date(trade_date), "%Y%m%d")
        completed_session = _date(self.completed_session_resolver(trade_day.strftime("%Y%m%d")))
        start = (trade_day - timedelta(days=60)).strftime("%Y%m%d")
        end = (trade_day - timedelta(days=1)).strftime("%Y%m%d")
        candidate_codes = {candidate.stock_code for candidate in candidates}
        pairs = {(relation.stock_code, relation.theme_code) for relation in memberships
                 if relation.stock_code in candidate_codes}
        themes = sorted({theme for _, theme in pairs})
        codes = sorted(candidate_codes | set(themes))
        series = {}
        for offset in range(0, len(codes), 50):
            batch = codes[offset:offset + 50]
            try:
                response = self.client.get_history_quotation(
                    batch, datetime.strptime(start, "%Y%m%d").date().isoformat(),
                    datetime.strptime(end, "%Y%m%d").date().isoformat(),
                    indicators="preClose,close,changeRatio")
            except Exception:
                # Upstream failure deliberately prevents reuse of stale cache.
                continue
            if (not isinstance(response, dict) or response.get("errorcode", 0) != 0
                    or not isinstance(response.get("tables"), list)):
                continue
            fetched = {}
            for entry in response["tables"]:
                if not isinstance(entry, dict) or entry.get("thscode") not in batch:
                    continue
                records = _parse_table(entry, start, end)
                if records:
                    target = fetched.setdefault(entry["thscode"], {})
                    for day, record in records.items():
                        target.setdefault(day, record)
            for code, records in fetched.items():
                cached = {}
                for record in self.db.get_daily_kline(code, start, end):
                    try:
                        day = _date(record["trade_date"])
                    except (ValueError, TypeError, AttributeError):
                        continue
                    if start <= day <= end:
                        cached.setdefault(day, {**record, "trade_date": day})
                saved = []
                for day, record in records.items():
                    # Preserve cached OHLC/volume fields when refreshing three indicators.
                    merged = {**cached.get(day, {}), **record}
                    cached[day] = merged
                    saved.append(merged)
                self.db.save_daily_kline(saved)
                # This response must establish freshness; cache cannot fill its endpoint.
                endpoint = records.get(completed_session)
                if endpoint is None or _number(endpoint.get("change_ratio")) is None:
                    continue
                series[code] = {day: number for day, record in sorted(cached.items())
                                if day <= completed_session
                                and (number := _number(record.get("change_ratio"))) is not None}

        window_ranks = []
        for window in RETURN_WINDOWS:
            returns = {theme: value for theme in themes
                       if (value := compounded_return(list(series.get(theme, {}).values()), window))
                       is not None}
            window_ranks.append(rank_percentiles(returns))
        recent = {theme: (fsum(weight * ranks[theme] for weight, ranks in
                             zip(self.config.return_window_weights, window_ranks))
                          if all(theme in ranks for ranks in window_ranks) else None)
                  for theme in themes}
        synchrony = {}
        for stock, theme in sorted(pairs):
            stock_series, theme_series = series.get(stock, {}), series.get(theme, {})
            common = sorted(stock_series.keys() & theme_series.keys())[-self.config.sync_window:]
            value = None
            if (completed_session in stock_series and completed_session in theme_series
                    and len(common) >= self.config.sync_min_observations):
                try:
                    corr = correlation([stock_series[day] for day in common],
                                       [theme_series[day] for day in common])
                    value = max(0.0, min(1.0, (corr + 1) / 2)) if isfinite(corr) else None
                except StatisticsError:
                    pass
            synchrony[(stock, theme)] = value
        all_values = [*recent.values(), *synchrony.values()]
        coverage = sum(value is not None for value in all_values) / len(all_values) if all_values else 0.0
        return HistoricalFeatures(recent, synchrony, coverage)
