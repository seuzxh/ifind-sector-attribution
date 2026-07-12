# -*- coding: utf-8 -*-
"""
监控板块管理：多周期涨幅计算 + 候选板块列表组装。

数据源全部走日K（daily_kline），供管理页展示每个候选板块的：
  当日涨幅 / 实体涨幅 / 3日累计涨幅 / 5日累计涨幅

候选板块范围 = 观察池全集（884 三级行业 + 885/886 概念板块，约 636 个），
即 ths_concept_dict 中所有 A 股前缀且在观察池内的概念。勾选状态单独读 watched_concepts 表。
"""

import os
import sys
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import Database
from core_calculator import calc_period_return_df


def _sector_prefix_label(code: str) -> str:
    """按 concept_code 前缀映射层级标签（项目无显式层级字段，靠前缀区分）。"""
    pfx = (code or "")[:3]
    if pfx == "884":
        return "三级行业"
    if pfx in ("885", "886"):
        return "概念板块"
    return "其他"


def _aggregate_sector_returns(
    stock_returns: Dict[str, float],
    stock_body: Dict[str, float],
    members_map: Dict[str, List[Dict]],
    min_member: int = 1,
) -> Dict[str, Dict[str, float]]:
    """
    把个股级涨幅聚合到板块级（成分股均值）。
    :param stock_returns: {stock_code: change_ratio}
    :param stock_body: {stock_code: body}（实体涨幅，可能为空 dict）
    :param members_map: {concept_code: [{stock_code, stock_name}, ...]}
    :return: {concept_code: {return, body, member_count}}
    """
    result: Dict[str, Dict[str, float]] = {}
    for cc, members in members_map.items():
        codes = [m["stock_code"] for m in members]
        rets = [stock_returns[c] for c in codes
                if c in stock_returns and not pd.isna(stock_returns[c])]
        if len(rets) < min_member:
            continue
        sec_return = sum(rets) / len(rets)
        bodies = [stock_body[c] for c in codes
                  if c in stock_body and not pd.isna(stock_body[c])]
        sec_body = sum(bodies) / len(bodies) if bodies else None
        result[cc] = {"return": sec_return, "body": sec_body, "member_count": len(rets)}
    return result


def compute_sector_multi_period_returns(
    db: Database,
    calc_date: Optional[str] = None,
) -> List[Dict]:
    """
    计算所有候选板块（观察池全集）的多周期涨幅，组装管理页列表。

    :param db: Database 实例
    :param calc_date: 基准日 YYYYMMDD，默认取 DB 最新交易日
    :return: [{concept_code, concept_name, level, change_ratio, body,
               return_3d, return_5d, member_count, watched}] 按涨幅降序
    """
    # 期末交易日（calc_date 当天无K时 calc_period_return_df 自动回退到最近交易日）
    end_date = calc_date or db.get_latest_trade_date()
    if not end_date:
        return []

    # 候选板块全集 + 成分股映射 + 名称 + 勾选状态
    concept_codes = db.get_observe_concept_codes()
    if not concept_codes:
        return []
    members_map = db.get_concept_members_map(concept_codes)
    concept_names = _load_concept_names(db)
    watched = set(db.get_watched_concept_codes())

    # 多周期个股累计涨幅（1d/3d/5d），各调一次 calc_period_return_df
    ret_1d_df = calc_period_return_df(db, end_date, 1)
    ret_3d_df = calc_period_return_df(db, end_date, 3)
    ret_5d_df = calc_period_return_df(db, end_date, 5)

    def _to_dict(df: pd.DataFrame) -> Dict[str, float]:
        if df.empty:
            return {}
        return dict(zip(df["code"], df["change_ratio"]))

    stock_1d = _to_dict(ret_1d_df)
    stock_3d = _to_dict(ret_3d_df)
    stock_5d = _to_dict(ret_5d_df)

    # 当日实体涨幅：用当日日K的 (close - open)/open（从 daily_kline 取）
    stock_body = _load_stock_body(db, end_date)

    # 聚合到板块（成分股均值）
    sec_1d = _aggregate_sector_returns(stock_1d, stock_body, members_map)
    sec_3d = _aggregate_sector_returns(stock_3d, {}, members_map)
    sec_5d = _aggregate_sector_returns(stock_5d, {}, members_map)

    rows = []
    for cc in concept_codes:
        if cc not in members_map or not members_map[cc]:
            continue  # 无成分股的板块不展示
        s1 = sec_1d.get(cc, {})
        rows.append({
            "concept_code": cc,
            "concept_name": concept_names.get(cc, cc),
            "level": _sector_prefix_label(cc),
            "change_ratio": round(s1.get("return"), 4) if s1.get("return") is not None else None,
            "body": round(s1.get("body"), 4) if s1.get("body") is not None else None,
            "return_3d": round(sec_3d.get(cc, {}).get("return"), 4)
                         if sec_3d.get(cc, {}).get("return") is not None else None,
            "return_5d": round(sec_5d.get(cc, {}).get("return"), 4)
                         if sec_5d.get(cc, {}).get("return") is not None else None,
            "member_count": s1.get("member_count") or len(members_map[cc]),
            "watched": cc in watched,
        })

    # 按当日涨幅降序（None 排最后）
    rows.sort(key=lambda r: (r["change_ratio"] is None, -(r["change_ratio"] or 0)))
    return rows


def _load_concept_names(db: Database) -> Dict[str, str]:
    """加载 concept_code → concept_name 映射。"""
    import sqlite3
    names = {}
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT concept_code, concept_name FROM ths_concept_dict"):
            names[row["concept_code"]] = row["concept_name"]
    return names


def _load_stock_body(db: Database, trade_date: str) -> Dict[str, float]:
    """
    读当日日K，算个股实体涨幅 (close - open)/open × 100。
    :return: {stock_code: body_ratio}
    """
    import sqlite3
    body = {}
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            "SELECT code, open, close FROM daily_kline WHERE trade_date = ?", (trade_date,)
        ):
            o, c = row["open"], row["close"]
            if o and o != 0 and c is not None:
                body[row["code"]] = (c / o - 1) * 100
    return body
