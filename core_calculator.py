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
    market_return: float
) -> Optional[Dict]:
    """
    计算单个概念板块的强度评分（三维）
    
    :param daily_df: 某交易日的全部日K数据，columns=[code, trade_date, change_ratio, ...]
    :param concept_code: 概念板块代码
    :param member_codes: 该概念的成分股代码列表
    :param market_return: 全市场平均涨幅（基准）
    :return: 评分字典或 None
    """
    concept_df = daily_df[daily_df["code"].isin(member_codes)].copy()
    if concept_df.empty:
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
    weights: Dict[str, float] = None
) -> pd.DataFrame:
    """
    计算所有概念板块的强度评分并排名
    
    :param daily_df: 某交易日的全部日K数据
    :param members_map: {concept_code: [stock_code, ...]}
    :param weights: 三维权重，默认取 config.SCORE_WEIGHTS
    :return: 按 rank 排序的 DataFrame
    """
    weights = weights or config.SCORE_WEIGHTS
    market_return = daily_df["change_ratio"].mean()

    results = []
    for concept_code, member_codes in members_map.items():
        r = calc_sector_strength(daily_df, concept_code, member_codes, market_return)
        if r:
            results.append(r)

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


def calc_multi_period_score(
    db,
    calc_date: str,
    members_map: Dict[str, List[str]],
    period_weights: Dict[str, float] = None
) -> pd.DataFrame:
    """
    多周期评分融合（1日 + 5日 + 20日）
    
    :param db: Database 实例
    :param calc_date: 计算日期
    :param members_map: 成分股映射
    :param period_weights: 周期权重，默认 config.PERIOD_WEIGHTS
    :return: 融合评分后的 DataFrame
    """
    period_weights = period_weights or config.PERIOD_WEIGHTS
    date_obj = datetime.strptime(calc_date, "%Y-%m-%d")

    # 获取各周期的日K数据
    period_data = {}
    for period_name, days in [("1d", 1), ("5d", 5), ("20d", 20)]:
        start = (date_obj - timedelta(days=days * 2)).strftime("%Y-%m-%d")
        # 从数据库获取数据
        df = pd.DataFrame(db.get_daily_kline_by_date(calc_date))
        if not df.empty:
            period_data[period_name] = df

    # 计算各周期评分
    scores = {}
    for period_name, df in period_data.items():
        scores[period_name] = calc_all_sectors_strength(df, members_map)

    # 以 1d 为基准合并
    base = scores.get("1d", pd.DataFrame()).copy()
    if base.empty:
        return base

    for period_name in ["5d", "20d"]:
        if period_name in scores and not scores[period_name].empty:
            merged = scores[period_name][["concept_code", "score"]].rename(
                columns={"score": f"score_{period_name}"}
            )
            base = base.merge(merged, on="concept_code", how="left")

    # 填充缺失值
    for col in ["score_5d", "score_20d"]:
        if col not in base.columns:
            base[col] = 0.0
        base[col] = base[col].fillna(0.0)

    # 融合评分
    base["score_final"] = (
        period_weights["1d"] * base["score"] +
        period_weights["5d"] * base["score_5d"] +
        period_weights["20d"] * base["score_20d"]
    )

    # 重新排名
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
        concepts = db.get_stock_concepts(stock_code, calc_date)
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
