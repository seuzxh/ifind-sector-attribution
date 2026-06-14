# -*- coding: utf-8 -*-
"""
核心计算引擎
- 板块强度评分（三维：S1涨幅强度、S2上涨广度、S4相对强度）
- 个股 L1 权重归因
- 联动率计算
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from datetime import datetime, timedelta

import config


def zscore(series: pd.Series) -> pd.Series:
    """横截面标准化（Z-score）"""
    std = series.std()
    if std == 0 or pd.isna(std):
        return pd.Series(0, index=series.index)
    return (series - series.mean()) / std


def calc_sector_strength(
    daily_df: pd.DataFrame,
    concept_code: str,
    member_codes: List[str],
    market_return: float,
    min_member_count: int = None
) -> Optional[Dict]:
    """
    计算单个概念板块的强度评分（三维）
    
    :param daily_df: 某交易日的全部日K数据，columns=[code, trade_date, change_ratio, ...]
    :param concept_code: 概念板块代码
    :param member_codes: 该概念的成分股代码列表
    :param market_return: 全市场平均涨幅（基准）
    :param min_member_count: 命中K线的成分股数下限，不足则返回 None（默认取 config.MIN_MEMBER_COUNT）
    :return: 评分字典或 None
    """
    if min_member_count is None:
        min_member_count = getattr(config, "MIN_MEMBER_COUNT", 0)

    concept_df = daily_df[daily_df["code"].isin(member_codes)].copy()
    if len(concept_df) < min_member_count:
        return None

    # S1: 涨幅强度（成分股平均涨幅）
    s1 = concept_df["change_ratio"].mean()

    # S2: 上涨广度（上涨比例 0~1）
    s2 = (concept_df["change_ratio"] > 0).sum() / len(concept_df)

    # S4: 相对强度（vs 全市场）
    s4 = s1 - market_return

    return {
        "concept_code": concept_code,
        "s1_return": round(float(s1), 4),
        "s2_breadth": round(float(s2), 4),
        "s4_relative": round(float(s4), 4),
        "member_count": len(concept_df)
    }


def calc_coherency(daily_df: pd.DataFrame, member_codes: List[str]) -> float:
    """
    计算板块联动率（成分股收益率相关系数均值）
    :param daily_df: 多日K线数据
    :param member_codes: 成分股代码列表
    :return: 联动率 0~1
    """
    member_df = daily_df[daily_df["code"].isin(member_codes)].copy()
    if len(member_df) < 2:
        return 0.0

    # 构建宽表：每行一个日期，每列一个股票
    pivot = member_df.pivot_table(
        index="trade_date", columns="code", values="change_ratio"
    )
    if pivot.shape[1] < 2:
        return 0.0

    # 计算相关系数矩阵，取上三角均值
    corr_matrix = pivot.corr()
    # 提取上三角（排除对角线）
    mask = np.triu(np.ones(corr_matrix.shape), k=1).astype(bool)
    upper_tri = corr_matrix.where(mask)
    coherency = upper_tri.stack().mean()

    return round(float(coherency) if not pd.isna(coherency) else 0.0, 4)


def calc_all_sectors_strength(
    daily_df: pd.DataFrame,
    members_map: Dict[str, List[str]],
    weights: Dict[str, float] = None,
    min_member_count: int = None
) -> pd.DataFrame:
    """
    计算所有概念板块的强度评分并排名
    
    :param daily_df: 某交易日的全部日K数据
    :param members_map: {concept_code: [stock_code, ...]}
    :param weights: 三维权重，默认取 config.SCORE_WEIGHTS
    :param min_member_count: 命中K线的成分股数下限，默认取 config.MIN_MEMBER_COUNT
    :return: 按 rank 排序的 DataFrame
    """
    weights = weights or config.SCORE_WEIGHTS
    if min_member_count is None:
        min_member_count = getattr(config, "MIN_MEMBER_COUNT", 0)
    market_return = daily_df["change_ratio"].mean()

    results = []
    skipped = 0
    for concept_code, member_codes in members_map.items():
        r = calc_sector_strength(daily_df, concept_code, member_codes, market_return, min_member_count)
        if r:
            results.append(r)
        else:
            skipped += 1

    if skipped:
        print(f"[CALC] 成分股命中数 < {min_member_count} 的概念已过滤: {skipped} 个")

    df = pd.DataFrame(results)
    if df.empty:
        return df

    # Z-score 标准化
    df["z_s1"] = zscore(df["s1_return"])
    df["z_s2"] = zscore(df["s2_breadth"])
    df["z_s4"] = zscore(df["s4_relative"])

    # 加权求和
    df["score"] = (
        weights["s1"] * df["z_s1"] +
        weights["s2"] * df["z_s2"] +
        weights["s4"] * df["z_s4"]
    )

    # 排名（1=最强）
    df["rank_1d"] = df["score"].rank(ascending=False, method="min").astype(int)

    return df.sort_values("rank_1d")


def calc_period_return_df(db, calc_date: str, days: int) -> pd.DataFrame:
    """
    构造一个"伪单日"DataFrame，change_ratio 为最近 N 个交易日的累计涨幅。
    累计涨幅 = (期末 close - 期初 preClose) / 期初 preClose * 100
    期初取窗口起点（往前 days*2 自然日以覆盖 days 个交易日）的首个 preClose。

    模块级函数（从 calc_multi_period_score 闭包提取），供 prescreen 等复用。

    :param db: Database 实例
    :param calc_date: 计算日期，YYYYMMDD 或 YYYY-MM-DD
    :param days: 交易日数（如 5 表示 5 日累计涨幅）
    :return: DataFrame [code, trade_date, change_ratio, close]，change_ratio 为累计涨幅 %
    """
    date_str = calc_date.replace("-", "")
    date_obj = datetime.strptime(date_str, "%Y%m%d")

    start = (date_obj - timedelta(days=days * 2)).strftime("%Y%m%d")
    rows = db.get_daily_kline_by_date_range(start, date_str)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["code", "trade_date"])
    first = df.groupby("code").first().reset_index()[["code", "pre_close"]]
    last = df[df["trade_date"] == date_str][["code", "close"]]
    if last.empty:
        return pd.DataFrame()
    merged = first.merge(last, on="code", how="inner")
    merged["change_ratio"] = merged.apply(
        lambda r: (r["close"] / r["pre_close"] - 1) * 100
        if r["pre_close"] and r["pre_close"] != 0 else 0.0,
        axis=1
    )
    merged["trade_date"] = date_str
    return merged[["code", "trade_date", "change_ratio", "close"]]


def calc_multi_period_score(
    db,
    calc_date: str,
    members_map: Dict[str, List[str]],
    period_weights: Dict[str, float] = None
) -> pd.DataFrame:
    """
    多周期评分融合（1日 + 5日 + 20日）

    1d 用当日涨幅；5d/20d 用该周期内的累计涨幅（期末 close / 期初 preClose - 1）。
    各周期分别算 Z-score 加权板块强度，再按 period_weights 融合。

    :param db: Database 实例
    :param calc_date: 计算日期，格式 YYYYMMDD 或 YYYY-MM-DD
    :param members_map: 成分股映射
    :param period_weights: 周期权重，默认 config.PERIOD_WEIGHTS
    :return: 融合评分后的 DataFrame（含 score_1d/score_5d/score_20d/score_final）
    """
    period_weights = period_weights or config.PERIOD_WEIGHTS
    # 兼容两种日期格式
    date_str = calc_date.replace("-", "")
    date_obj = datetime.strptime(date_str, "%Y%m%d")

    # 复用模块级函数（原为闭包，提取后供 prescreen 等复用）
    def _period_return_df(days: int) -> pd.DataFrame:
        return calc_period_return_df(db, calc_date, days)

    # 各周期数据：1d 直接用当日，5d/20d 用累计涨幅
    period_data = {}
    period_data["1d"] = pd.DataFrame(db.get_daily_kline_by_date(date_str))
    period_data["5d"] = _period_return_df(5)
    period_data["20d"] = _period_return_df(20)

    # 计算各周期板块强度
    scores = {}
    for period_name, df in period_data.items():
        if not df.empty:
            scores[period_name] = calc_all_sectors_strength(df, members_map)

    # 以 1d 为基准合并
    base = scores.get("1d", pd.DataFrame()).copy()
    if base.empty:
        return base
    base = base.rename(columns={"score": "score_1d"})

    for period_name in ["5d", "20d"]:
        col = f"score_{period_name}"
        if period_name in scores and not scores[period_name].empty:
            merged = scores[period_name][["concept_code", "score"]].rename(
                columns={"score": col}
            )
            base = base.merge(merged, on="concept_code", how="left")
        else:
            base[col] = 0.0
        base[col] = base[col].fillna(0.0)

    # 融合评分
    base["score_final"] = (
        period_weights["1d"] * base["score_1d"] +
        period_weights["5d"] * base["score_5d"] +
        period_weights["20d"] * base["score_20d"]
    )

    # 重新排名（基于融合分）
    base["rank_1d"] = base["score_final"].rank(ascending=False, method="min").astype(int)

    return base.sort_values("rank_1d")


def l1_stock_attribution(
    stock_code: str,
    stock_return: float,
    concept_weights: Dict[str, float],
    concept_returns: Dict[str, float]
) -> List[Dict]:
    """
    L1 权重归因法
    
    :param stock_code: 股票代码
    :param stock_return: 个股当日涨幅
    :param concept_weights: {concept_code: weight}
    :param concept_returns: {concept_code: change_ratio}
    :return: 按贡献排序的归因列表
    """
    attributions = []
    total_contrib = 0.0

    for concept_code, weight in concept_weights.items():
        concept_ret = concept_returns.get(concept_code, 0.0)
        # 防御 nan（停牌等导致），nan 视为 0
        if pd.isna(concept_ret):
            concept_ret = 0.0
        contrib = weight * concept_ret
        total_contrib += contrib
        attributions.append({
            "concept_code": concept_code,
            "weight": round(weight, 4),
            "concept_return": round(concept_ret, 4),
            "contribution": round(contrib, 4)
        })

    # 按贡献绝对值排序
    attributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)

    # 归一化贡献占比
    if abs(total_contrib) > 1e-8:
        for a in attributions:
            a["contrib_pct"] = round(a["contribution"] / total_contrib, 4)
    else:
        for a in attributions:
            a["contrib_pct"] = 0.0

    return attributions


def calc_portfolio_attribution(
    db,
    holdings: List[Dict],
    calc_date: str
) -> Dict:
    """
    计算组合归因 + 强势板块定位
    
    :param db: Database 实例
    :param holdings: [{stock_code, shares, cost_price}, ...]
    :param calc_date: 计算日期
    :return: 组合归因结果
    """
    # 获取当日板块强度排名
    rankings = db.get_sector_rankings(calc_date)
    top_sectors = [r for r in rankings if r["rank_1d"] <= 10]

    # 组合暴露分析
    exposure = {}
    for h in holdings:
        stock_code = h["stock_code"]
        concepts = db.get_stock_concepts(stock_code)  # 取最新缓存
        for c in concepts:
            code = c["concept_code"]
            if code not in exposure:
                exposure[code] = {"weight": 0.0, "stocks": []}
            exposure[code]["weight"] += h.get("shares", 0) * h.get("cost_price", 0)
            exposure[code]["stocks"].append(stock_code)

    # 找出组合暴露最大的行业
    dominant = None
    if exposure:
        dominant_code = max(exposure.keys(), key=lambda k: exposure[k]["weight"])
        dominant = {
            "concept_code": dominant_code,
            "concept_name": db.get_concept_name(dominant_code),
            "exposure_weight": exposure[dominant_code]["weight"]
        }

    # 匹配强势板块
    alert_sectors = []
    for sector in top_sectors:
        if sector["concept_code"] in exposure:
            alert_sectors.append({
                "concept_code": sector["concept_code"],
                "concept_name": sector["concept_name"],
                "rank": sector["rank_1d"],
                "score": sector["score_final"],
                "exposure_weight": exposure[sector["concept_code"]]["weight"]
            })

    return {
        "calc_date": calc_date,
        "dominant_sector": dominant,
        "alert_sectors": alert_sectors,
        "top_sectors": top_sectors[:5]
    }
