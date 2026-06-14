# -*- coding: utf-8 -*-
"""
盘前板块筛选

基于近 N 个交易日（默认5日）的累计涨幅：
  步骤1：对所有 A 股概念算 5d 涨幅（成分股均值），取前 20 板块
  步骤2：对选出的每个板块，取其成分股 5d 涨幅前 30

结果存入 watchlist 表，供实时监控聚焦使用。
"""

import os
import sys
from typing import Dict, List

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from database import Database
from core_calculator import calc_period_return_df


def run_prescreen(
    db: Database,
    calc_date: str,
    period_days: int = None,
    top_sector: int = None,
    top_stock: int = None,
    min_member: int = None,
) -> Dict:
    """
    执行盘前筛选。

    :param db: Database 实例
    :param calc_date: 筛选日期 YYYYMMDD（须已 daily 入库 K 线）
    :param period_days: 累计涨幅天数，默认 config.PRESCREEN_PERIOD_DAYS
    :param top_sector: 选出的板块数，默认 config.PRESCREEN_TOP_SECTOR
    :param top_stock: 每板块成分股数，默认 config.PRESCREEN_TOP_STOCK
    :param min_member: 板块最少成分股数，默认 config.PRESCREEN_MIN_MEMBER
    :return: {date, sector_count, stock_count, sectors: [...]}
    """
    period_days = period_days or config.PRESCREEN_PERIOD_DAYS
    top_sector = top_sector or config.PRESCREEN_TOP_SECTOR
    top_stock = top_stock or config.PRESCREEN_TOP_STOCK
    min_member = min_member if min_member is not None else config.PRESCREEN_MIN_MEMBER

    print(f"[PRESCREEN] 开始筛选 date={calc_date} period={period_days}d "
          f"top_sector={top_sector} top_stock={top_stock}")

    # 1. 算每只股票的 N 日累计涨幅
    return_df = calc_period_return_df(db, calc_date, period_days)
    if return_df.empty:
        return {"error": f"日期 {calc_date} 无足够历史 K 线（需 ≥{period_days+1} 个交易日）"}
    stock_return = dict(zip(return_df["code"], return_df["change_ratio"]))
    print(f"[PRESCREEN] {len(stock_return)} 只股票有 {period_days}d 涨幅数据")

    # 2. 算每个 A 股概念的 N 日涨幅（成分股均值）
    concept_codes = db.get_a_share_concept_codes()
    concept_names = _load_concept_names(db)

    sector_returns = []
    members_map = {}  # 缓存板块成分股，供步骤2复用
    for cc in concept_codes:
        members = db.get_concept_members(cc)
        if not members or len(members) < min_member:
            continue
        codes = [m["stock_code"] for m in members]
        member_returns = [
            stock_return[c] for c in codes
            if c in stock_return and not pd.isna(stock_return[c])
        ]
        if len(member_returns) < min_member:
            continue
        sector_5d = sum(member_returns) / len(member_returns)
        sector_returns.append({
            "concept_code": cc,
            "concept_name": concept_names.get(cc, cc),
            "sector_5d_return": round(sector_5d, 4),
            "member_count": len(member_returns),
        })
        members_map[cc] = members

    sector_returns.sort(key=lambda x: x["sector_5d_return"], reverse=True)
    selected_sectors = sector_returns[:top_sector]
    print(f"[PRESCREEN] 板块筛选完成：{len(sector_returns)} 个有效板块中选出 {len(selected_sectors)} 个")
    for s in selected_sectors[:5]:
        print(f"  #{selected_sectors.index(s)+1} {s['concept_name']:<14} 5d={s['sector_5d_return']:+.2f}%")

    # 3. 每个板块选成分股前 N
    watchlist_records = []
    result_sectors = []
    for rank_sec, sec in enumerate(selected_sectors, 1):
        cc = sec["concept_code"]
        members = members_map[cc]
        # 该板块成分股的 5d 涨幅
        member_with_return = []
        for m in members:
            r = stock_return.get(m["stock_code"])
            if r is not None and not pd.isna(r):
                member_with_return.append({
                    "stock_code": m["stock_code"],
                    "stock_name": m.get("stock_name", ""),
                    "stock_5d_return": round(float(r), 4),
                })
        member_with_return.sort(key=lambda x: x["stock_5d_return"], reverse=True)
        top_members = member_with_return[:top_stock]

        sector_entry = {
            "concept_code": cc,
            "concept_name": sec["concept_name"],
            "sector_5d_return": sec["sector_5d_return"],
            "rank_sector": rank_sec,
            "member_count": sec["member_count"],
            "stocks": top_members,
        }
        result_sectors.append(sector_entry)

        # 平铺为 watchlist 表记录
        for rank_st, m in enumerate(top_members, 1):
            watchlist_records.append({
                "concept_code": cc,
                "concept_name": sec["concept_name"],
                "sector_5d_return": sec["sector_5d_return"],
                "rank_sector": rank_sec,
                "stock_code": m["stock_code"],
                "stock_name": m["stock_name"],
                "stock_5d_return": m["stock_5d_return"],
                "rank_stock": rank_st,
            })

    # 4. 存入 watchlist 表
    db.save_watchlist(calc_date, watchlist_records)
    total_stocks = len(set(r["stock_code"] for r in watchlist_records))
    print(f"[PRESCREEN] watchlist 已保存：{len(selected_sectors)} 板块，"
          f"{len(watchlist_records)} 条成分股记录（去重 {total_stocks} 只）")

    return {
        "date": calc_date,
        "period_days": period_days,
        "sector_count": len(selected_sectors),
        "stock_count": total_stocks,
        "sectors": result_sectors,
    }


def _load_concept_names(db: Database) -> Dict[str, str]:
    """加载概念代码→名称映射"""
    import sqlite3
    names = {}
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT concept_code, concept_name FROM ths_concept_dict"):
            names[row["concept_code"]] = row["concept_name"]
    return names
