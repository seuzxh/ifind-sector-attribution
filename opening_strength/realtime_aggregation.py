"""Pure aggregation of frozen attributions and provider-normalized quotes.

The caller slices each stock's quote independently to the requested minute.
At 09:30 the caller supplies the same quotes for the previous-minute baseline.
"""

from collections.abc import Mapping, Sequence
from math import isfinite

from .realtime_models import AttributedStock, StockContribution, StockQuote, ThemeSnapshot


def _return_pct(quote: StockQuote | None) -> float | None:
    if quote is None or not isfinite(quote.pre_close) or quote.pre_close <= 0:
        return None
    if not isfinite(quote.last_price):
        return None
    result = (quote.last_price / quote.pre_close - 1) * 100
    return result if isfinite(result) else None


def _contribution(stock: AttributedStock, quote: StockQuote | None) -> StockContribution:
    return_pct = _return_pct(quote)
    if return_pct is None:
        quote = None
    contribution = stock.attribution_weight * return_pct if return_pct is not None else None
    return StockContribution(
        stock.stock_code, stock.stock_name, stock.attribution_weight, stock.confidence,
        stock.reason_codes, stock.source_pool_ids,
        quote.quote_time if quote else None, quote.pre_close if quote else None,
        quote.last_price if quote else None, quote.avg_price if quote else None,
        quote.turnover if quote else None, return_pct, contribution,
        max(contribution, 0) if contribution is not None else None, quote is not None,
    )


def _level_and_breadth(contributors: Sequence[StockContribution]):
    valid = [row for row in contributors if row.has_quote]
    weight_sum = sum(row.attribution_weight for row in valid)
    level = sum(row.contribution for row in valid) / weight_sum if weight_sum else None
    up_ratio = sum(row.return_pct > 0 for row in valid) / len(valid) if valid else None
    return level, up_ratio


def aggregate_themes(
    attributions: Sequence[AttributedStock],
    quotes: Mapping[str, StockQuote],
    previous_quotes: Mapping[str, StockQuote],
    *,
    stale_quote: bool = False,
) -> tuple[ThemeSnapshot, ...]:
    grouped: dict[tuple[str, str, str], dict[str, AttributedStock]] = {}
    for stock in attributions:
        key = (stock.theme_code, stock.theme_name, stock.theme_type)
        grouped.setdefault(key, {}).setdefault(stock.stock_code, stock)

    themes = []
    for (code, name, theme_type), stocks in sorted(grouped.items()):
        contributors = tuple(sorted(
            (_contribution(stock, quotes.get(stock.stock_code)) for stock in stocks.values()),
            key=lambda row: (not row.has_quote,
                             -row.contribution if row.contribution is not None else 0,
                             row.stock_code),
        ))
        previous = tuple(_contribution(stock, previous_quotes.get(stock.stock_code))
                         for stock in stocks.values())
        level, up_ratio = _level_and_breadth(contributors)
        previous_level, previous_up_ratio = _level_and_breadth(previous)
        momentum = level - previous_level if level is not None and previous_level is not None else None
        breadth_delta = (up_ratio - previous_up_ratio
                         if up_ratio is not None and previous_up_ratio is not None else None)
        valid = [row for row in contributors if row.has_quote]
        supporting = [row for row in valid if row.return_pct > 0]
        weight_sum = sum(row.attribution_weight for row in valid)
        support_weight = sum(row.attribution_weight for row in supporting) / weight_sum if weight_sum else None
        pools = {pool for row in supporting for pool in row.source_pool_ids}
        positives = sorted((row.positive_contribution for row in valid
                            if row.positive_contribution > 0), reverse=True)
        positive_sum = sum(positives)
        top1 = positives[0] / positive_sum if positive_sum else None
        top3 = sum(positives[:3]) / positive_sum if positive_sum else None
        health = len(valid) / len(stocks)
        tags = []
        if len(stocks) == 1:
            tags.append("单股驱动")
        if len(stocks) > 1 and len(supporting) < 2:
            tags.append("低支撑")
        if top1 is not None and top1 >= .70:
            tags.append("高度集中")
        if health < .60:
            tags.append("数据不足")
        if stale_quote:
            tags.append("行情滞后")
        themes.append(ThemeSnapshot(
            code, name, theme_type, level, momentum, up_ratio, breadth_delta,
            len(stocks), len(valid), len(supporting), support_weight, len(pools),
            top1, top3, health, tuple(tags), contributors,
        ))
    return tuple(themes)


def build_rankings(
    themes: Sequence[ThemeSnapshot],
) -> tuple[tuple[ThemeSnapshot, ...], tuple[str, ...], tuple[str, ...]]:
    ordered = tuple(sorted(themes, key=lambda theme: (
        not theme.rankable, -theme.level if theme.level is not None else 0,
        -theme.momentum_1m if theme.momentum_1m is not None else float("inf"),
        theme.theme_code,
    )))
    eligible = [theme for theme in themes
                if theme.valid_quote_count >= 2 and theme.data_health >= .60]
    acceleration = tuple(theme.theme_code for theme in sorted(
        (theme for theme in eligible if theme.momentum_1m is not None),
        key=lambda theme: (-theme.momentum_1m, theme.theme_code),
    ))
    breadth = tuple(theme.theme_code for theme in sorted(
        (theme for theme in eligible if theme.breadth_delta_1m is not None),
        key=lambda theme: (-theme.breadth_delta_1m, theme.theme_code),
    ))
    return ordered, acceleration, breadth
