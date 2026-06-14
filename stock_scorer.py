# -*- coding: utf-8 -*-
"""
个股综合评分器（实时模式用，基于分时数据）

四维加权评分：
  - 涨幅（change_ratio，相对昨收）权重 0.4
  - 涨速（1min 滚动：末点 last_price 相对前1分钟涨幅）权重 0.2
  - body（开盘至今涨幅：末点 last_price 相对集合竞价开盘价）权重 0.2
  - 是否涨停（按板块判定）权重 0.2

附加展示指标（不进综合分）：
  - acceleration（涨速加速）：speed[t] - speed[t-1]，>0 加速 / <0 减速

涨停规则：
  - 沪深主板（.SH/.SZ 非300/688开头）：≥9.8%
  - 创业板（300xxx）/科创板（688xxx）：≥19.5%
  - 北交所（.BJ）：≥29%
"""

import pandas as pd
import numpy as np


# 权重配置
SCORE_WEIGHTS = {
    "change": 0.4,
    "speed": 0.2,
    "body": 0.2,
    "limit": 0.2,
}

# 涨停阈值（用略低于精确值，规避四舍五入误差）
LIMIT_THRESHOLDS = {
    "main": 9.8,      # 沪深主板
    "gem_star": 19.5, # 创业板/科创板
    "bj": 29.0,       # 北交所
}


def get_board_type(code: str) -> str:
    """
    判断股票所属板块类型。
    :return: "main" 沪深主板 / "gem_star" 创业板或科创板 / "bj" 北交所
    """
    if code.endswith(".BJ"):
        return "bj"
    # 创业板 300xxx（.SZ）、科创板 688xxx（.SH）
    pure = code.split(".")[0]
    if pure.startswith(("300", "688")):
        return "gem_star"
    return "main"


def is_limit_up(code: str, change_ratio: float) -> bool:
    """
    判断是否涨停（基于实时涨跌幅）。
    :param code: 股票代码
    :param change_ratio: 相对昨收的涨跌幅 %
    """
    if change_ratio is None or pd.isna(change_ratio):
        return False
    board = get_board_type(code)
    threshold = LIMIT_THRESHOLDS[board]
    return change_ratio >= threshold


def compute_limit_score(code: str, change_ratio: float) -> float:
    """涨停分：涨停=1.0，未涨停=0.0"""
    return 1.0 if is_limit_up(code, change_ratio) else 0.0


def compute_body_ratio(open_price: float, last_price: float) -> float:
    """
    body（开盘至今涨幅）= (last - open) / open × 100
    反映从集合竞价开盘价到当前价的累计涨幅。
    :param open_price: 集合竞价开盘价（pre_market[-1].ref_price）
    :param last_price: 当前 last_price
    """
    if not open_price or open_price == 0 or pd.isna(open_price) or pd.isna(last_price):
        return 0.0
    return (last_price - open_price) / open_price * 100


def compute_speed_series(last_prices: list) -> list:
    """
    涨速序列（1min 滚动）：speed[t] = (last[t] - last[t-1]) / last[t-1] × 100
    :param last_prices: 按时间正序的 last_price 序列
    :return: 与输入等长的列表，首点为 0.0（无前点）
    """
    n = len(last_prices)
    if n == 0:
        return []
    speeds = [0.0]
    for i in range(1, n):
        prev = last_prices[i - 1]
        cur = last_prices[i]
        if prev and prev > 0 and cur is not None and not pd.isna(cur):
            speeds.append((cur - prev) / prev * 100)
        else:
            speeds.append(0.0)
    return speeds


def compute_speed(last_prices: list) -> float:
    """
    涨速（末点）：speed[-1] = (last[-1] - last[-2]) / last[-2] × 100
    :param last_prices: 按时间正序的 last_price 序列（至少2个元素）
    :return: 末点涨速 %，不足2点返回 0.0
    """
    valid = [p for p in last_prices if p is not None and not pd.isna(p) and p > 0]
    if len(valid) < 2:
        return 0.0
    prev, cur = valid[-2], valid[-1]
    if prev == 0:
        return 0.0
    return (cur - prev) / prev * 100


def compute_acceleration(speed_series: list) -> float:
    """
    涨速加速（末点）：accel[-1] = speed[-1] - speed[-2]
    > 0 = 涨速在加快（加速上涨）；< 0 = 涨速在减缓或掉头。
    :param speed_series: compute_speed_series 的输出（等长于 last_prices）
    :return: 末点加速值，不足2点返回 0.0
    """
    if len(speed_series) < 2:
        return 0.0
    return speed_series[-1] - speed_series[-2]


def _zscore(series: pd.Series) -> pd.Series:
    """Z-score 标准化，常量序列返回全0"""
    std = series.std()
    if std == 0 or pd.isna(std):
        return pd.Series([0.0] * len(series), index=series.index)
    return (series - series.mean()) / std


def score_members(
    member_data: pd.DataFrame,
    weights: dict = None,
) -> pd.DataFrame:
    """
    对某板块的成分股计算综合评分并排名。

    综合分用四维（涨幅/涨速/body/涨停）；acceleration 仅作为展示列返回，不进综合分。

    :param member_data: 成分股数据，必须含列：
        - code: 股票代码
        - change_ratio: 实时涨幅 %（相对昨收）
        - speed: 涨速 %（1min 滚动末点）
        - body: body %（开盘至今涨幅）
        - acceleration: 涨速加速（可选，无则填 0）
        - stock_name: 股票名称（可选，用于展示）
    :param weights: 权重，默认取 SCORE_WEIGHTS
    :return: 按 score 降序排列的 DataFrame，新增列：
        - limit_score: 涨停分
        - score: 综合分
        - rank: 板块内排名（1=最强）
    """
    weights = weights or SCORE_WEIGHTS
    if member_data.empty:
        return member_data

    df = member_data.copy()
    # 填充缺失值
    for col in ["change_ratio", "speed", "body"]:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)
        else:
            df[col] = 0.0
    if "acceleration" not in df.columns:
        df["acceleration"] = 0.0
    df["acceleration"] = df["acceleration"].fillna(0.0)

    # 涨停分（0/1 二值，不参与标准化）
    df["limit_score"] = df.apply(
        lambda r: compute_limit_score(r["code"], r["change_ratio"]), axis=1
    )

    # Z-score 标准化（在同一板块内）
    df["z_change"] = _zscore(df["change_ratio"])
    df["z_speed"] = _zscore(df["speed"])
    df["z_body"] = _zscore(df["body"])

    # 加权综合分
    df["score"] = (
        weights["change"] * df["z_change"]
        + weights["speed"] * df["z_speed"]
        + weights["body"] * df["z_body"]
        + weights["limit"] * df["limit_score"]
    )

    # 排名
    df["rank"] = df["score"].rank(ascending=False, method="min").astype(int)
    return df.sort_values("rank")

