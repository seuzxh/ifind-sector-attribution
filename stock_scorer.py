# -*- coding: utf-8 -*-
"""
个股综合评分器（实时模式用）

四维加权评分：
  - 涨幅（changeRatio，相对昨收）权重 0.4
  - 涨速（近3分钟涨幅）权重 0.2
  - 实体涨幅（最后一根K线 close-open/open）权重 0.2
  - 是否涨停（按板块判定）权重 0.2

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


def compute_body_ratio(open_price: float, close_price: float) -> float:
    """
    实体涨幅 = (close - open) / open × 100
    反映最后一根K线的实体大小（剔除跳空）。
    """
    if not open_price or open_price == 0 or pd.isna(open_price) or pd.isna(close_price):
        return 0.0
    return (close_price - open_price) / open_price * 100


def compute_speed(close_series: list) -> float:
    """
    涨速 = (当前close − 3分钟前close) / 3分钟前close × 100
    :param close_series: 按时间正序的 close 序列（至少1个元素）
    """
    if not close_series or len(close_series) < 1:
        return 0.0
    # 取最后有效值
    valid = [c for c in close_series if c is not None and not pd.isna(c) and c > 0]
    if len(valid) < 2:
        return 0.0
    current = valid[-1]
    # 3分钟前的close（倒数第4个，不够则取最早的）
    idx = max(0, len(valid) - 4)
    base = valid[idx]
    if base == 0:
        return 0.0
    return (current - base) / base * 100


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

    :param member_data: 成分股数据，必须含列：
        - code: 股票代码
        - change_ratio: 实时涨幅 %
        - speed: 涨速 %
        - body: 实体涨幅 %
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
