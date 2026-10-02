"""Immutable, I/O-free values for the opening-theme dashboard."""

from dataclasses import dataclass, fields, is_dataclass


def _serialize(value):
    """Round API values without reducing precision of domain calculations."""
    if is_dataclass(value):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


@dataclass(frozen=True)
class AttributedStock:
    stock_code: str
    stock_name: str
    theme_code: str
    theme_name: str
    theme_type: str
    attribution_weight: float
    confidence: float
    reason_codes: tuple[str, ...]
    source_pool_ids: tuple[str, ...]


@dataclass(frozen=True)
class StockQuote:
    stock_code: str
    quote_time: str
    pre_close: float
    last_price: float
    avg_price: float | None
    turnover: float | None


@dataclass(frozen=True)
class StockContribution:
    stock_code: str
    stock_name: str
    attribution_weight: float
    confidence: float
    reason_codes: tuple[str, ...]
    source_pool_ids: tuple[str, ...]
    quote_time: str | None
    pre_close: float | None
    last_price: float | None
    avg_price: float | None
    turnover: float | None
    return_pct: float | None
    contribution: float | None
    positive_contribution: float | None
    has_quote: bool

    def to_dict(self) -> dict:
        return _serialize(self)


@dataclass(frozen=True)
class ThemeSnapshot:
    theme_code: str
    theme_name: str
    theme_type: str
    level: float | None
    momentum_1m: float | None
    up_ratio: float | None
    breadth_delta_1m: float | None
    attributed_stock_count: int
    valid_quote_count: int
    supporting_count: int
    support_weight: float | None
    source_pool_diversity: int
    top1_concentration: float | None
    top3_concentration: float | None
    data_health: float
    risk_tags: tuple[str, ...]
    contributors: tuple[StockContribution, ...]

    @property
    def rankable(self) -> bool:
        return self.level is not None

    def to_dict(self) -> dict:
        return _serialize(self)


@dataclass(frozen=True)
class OpeningDashboard:
    trade_date: str
    run_id: str
    mode: str
    snapshot_time: str
    latest_time: str
    available_times: tuple[str, ...]
    candidate_count: int
    theme_count: int
    data_health: float
    themes: tuple[ThemeSnapshot, ...]
    acceleration_theme_codes: tuple[str, ...]
    breadth_theme_codes: tuple[str, ...]
    generated_at: str
    cache_status: str

    def to_dict(self) -> dict:
        return _serialize(self)
