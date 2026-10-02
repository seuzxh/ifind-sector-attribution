"""Fetch and cache complete minute series, then resolve independent stock slices."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from math import isfinite
from threading import Event, Lock
from time import monotonic as system_monotonic
from types import MappingProxyType
from zoneinfo import ZoneInfo

from .realtime_models import StockQuote


SHANGHAI = ZoneInfo("Asia/Shanghai")


class OpeningDashboardError(ValueError):
    """Public domain failure with a safe message and an explicit retry policy."""

    def __init__(self, code: str, message: str, retryable: bool):
        self.code, self.message, self.retryable = code, message, retryable
        super().__init__(message)


@dataclass(frozen=True)
class QuoteSlice:
    available_times: tuple[str, ...]
    snapshot_time: str
    latest_time: str
    quotes: Mapping[str, StockQuote]
    previous_quotes: Mapping[str, StockQuote]
    cache_status: str
    stale_quote: bool = False


@dataclass(frozen=True)
class _Series:
    quotes: Mapping[str, tuple[StockQuote, ...]]
    available_times: tuple[str, ...]


@dataclass
class _CacheEntry:
    series: _Series
    fetched_at: float
    historical: bool
    failed_refresh: bool = False


@dataclass
class _Flight:
    done: Event = field(default_factory=Event)
    entry: _CacheEntry | None = None
    error: OpeningDashboardError | None = None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _normalize(raw: Mapping, codes: tuple[str, ...]) -> _Series:
    series, times = {}, set()
    for code in codes:
        record = raw.get(code)
        if not isinstance(record, Mapping):
            continue
        pre_close = _number(record.get("pre_close"))
        if pre_close is None or pre_close <= 0:
            continue
        points = {}
        # pre_market is deliberately never read, even when trading is empty.
        for point in record.get("trading") or ():
            if not isinstance(point, Mapping):
                continue
            minute = str(point.get("time", ""))[:5]
            try:
                valid_time = datetime.strptime(minute, "%H:%M").strftime("%H:%M") == minute
            except ValueError:
                valid_time = False
            price = _number(point.get("last_price"))
            if not valid_time or minute < "09:30" or price is None:
                continue
            points[minute] = StockQuote(
                code, minute, pre_close, price,
                _number(point.get("avg_price")), _number(point.get("turnover")),
            )
        if points:
            series[code] = tuple(points[minute] for minute in sorted(points))
            times.update(points)
    return _Series(MappingProxyType(series), tuple(sorted(times)))


def _at_minute(series: _Series, minute: str) -> Mapping[str, StockQuote]:
    quotes = {}
    for code, points in series.quotes.items():
        for point in reversed(points):
            if point.quote_time <= minute:
                quotes[code] = point
                break
    return MappingProxyType(quotes)


def _lagging(trade_date: str, latest: str, now: datetime) -> bool:
    if trade_date != now.strftime("%Y%m%d"):
        return False
    start = int(latest[:2]) * 60 + int(latest[3:])
    end = now.hour * 60 + now.minute
    # Count only elapsed continuous-auction minutes, never lunch or closed hours.
    elapsed = sum(max(0, min(end, close) - max(start, opening))
                  for opening, close in ((570, 690), (780, 900)))
    return elapsed > 2


class OpeningQuoteProvider:
    def __init__(self, fetcher=None, *, clock=None, monotonic=None):
        self._fetcher = fetcher
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._monotonic = monotonic or system_monotonic
        self._lock = Lock()
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._flights: dict[tuple[str, str], _Flight] = {}

    def _now(self) -> datetime:
        now = self._clock()
        return (now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now.astimezone(SHANGHAI))

    def _load_series(self, codes: tuple[str, ...], trade_date: str) -> tuple[_Series, str]:
        digest = sha256("\0".join(codes).encode()).hexdigest()
        key = (trade_date, digest)
        historical = trade_date != self._now().strftime("%Y%m%d")
        with self._lock:
            entry = self._cache.get(key)
            if (entry is not None and entry.historical == historical
                    and (historical or self._monotonic() - entry.fetched_at < 15)):
                return entry.series, "stale" if entry.failed_refresh else "hit"
            flight = self._flights.get(key)
            if flight is not None and entry is not None:
                return entry.series, "stale" if entry.failed_refresh else "hit"
            owner = flight is None
            if owner:
                flight = self._flights[key] = _Flight()

        if not owner:
            flight.done.wait()
            if flight.error is not None:
                raise OpeningDashboardError(flight.error.code, flight.error.message,
                                            flight.error.retryable) from None
            return flight.entry.series, "stale" if flight.entry.failed_refresh else "hit"

        # Neither network I/O nor waiting for another key runs under the cache lock.
        try:
            fetcher = self._fetcher
            if fetcher is None:
                from intraday_fetcher import IntradayFetcher
                fetcher = IntradayFetcher()
            raw = fetcher.fetch_batch(list(codes), date=trade_date if historical else None)
            series = _normalize(raw, codes)
            if not series.available_times:
                raise OpeningDashboardError("QUOTE_DATA_UNAVAILABLE", "暂无可用的盘中行情", True)
            new_entry = _CacheEntry(series, self._monotonic(), historical)
            error = None
        except Exception as exc:
            error = (exc if isinstance(exc, OpeningDashboardError) else
                     OpeningDashboardError("QUOTE_PROVIDER_FAILED", "行情获取暂时失败，请稍后重试", True))
            new_entry = None

        with self._lock:
            if new_entry is not None:
                self._cache[key] = new_entry
                flight.entry = new_entry
            elif entry is not None:
                entry.failed_refresh = True
                flight.entry = entry
            else:
                flight.error = error
            del self._flights[key]
            flight.done.set()
        if flight.error is not None:
            raise flight.error from None
        return flight.entry.series, "stale" if flight.entry.failed_refresh else "fresh"

    def load(self, codes: Sequence[str], trade_date: str,
             snapshot_time: str | None = None) -> QuoteSlice:
        series, status = self._load_series(tuple(sorted(set(codes))), trade_date)
        requested = snapshot_time or series.available_times[-1]
        available = tuple(minute for minute in series.available_times if minute <= requested)
        if not available:
            raise OpeningDashboardError("QUOTE_DATA_UNAVAILABLE", "该时点暂无可用的盘中行情", True)
        minute = available[-1]
        quotes = _at_minute(series, minute)
        previous = (quotes if minute == "09:30" else
                    _at_minute(series, available[-2]) if len(available) > 1 else MappingProxyType({}))
        return QuoteSlice(series.available_times, minute, series.available_times[-1], quotes,
                          previous, status, _lagging(trade_date, series.available_times[-1], self._now()))
