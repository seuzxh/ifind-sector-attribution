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

    # 多周期个股涨幅：
    #   - 当日涨幅：直接读 daily_kline.change_ratio（当日真实涨跌幅）
    #   - 3日/5日累计涨幅：_load_stock_period_returns（per-stock 最近日对齐，避免个别缺日股票被丢弃）
    stock_1d = _load_stock_change_ratio(db, end_date)
    stock_3d = _load_stock_period_returns(db, end_date, 3)
    stock_5d = _load_stock_period_returns(db, end_date, 5)

    # 过滤新股（上市不足5交易日），避免连板新股的累计涨幅严重污染板块均值。
    # 与 prescreen 的过滤口径一致（get_new_stock_codes）。
    new_stocks = db.get_new_stock_codes(end_date, min_days=5)
    if new_stocks:
        for d in (stock_3d, stock_5d):
            for code in new_stocks:
                d.pop(code, None)
        print(f"[SECTOR-MANAGE] 过滤新股 {len(new_stocks)} 只（避免污染 3d/5d 板块均值）")

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


def compute_sector_quotes_from_ifind(db: Database) -> List[Dict]:
    """
    直接从 iFinD 接口3（cmd_history_quotation）查询概念指数本身的日K行情，
    算出当日涨幅 / 实体涨幅 / 3日 / 5日累计涨幅。

    与 compute_sector_multi_period_returns（从成分股聚合 daily_kline）的区别：
      - 数据源：iFinD 实时接口（概念指数本身），非本地 daily_kline 成分股聚合
      - 准确性：指数官方值，不受成分股缺数据/新股污染
      - 速度：636 个板块 7 批约 3 秒（成分股聚合要遍历数万条记录）
      - 实时性：盘中调用返回当日实时值

    :return: [{concept_code, concept_name, level, change_ratio, body,
               return_3d, return_5d, member_count, watched}] 按涨幅降序
    """
    import time
    from datetime import datetime, timedelta
    from ifind_client import IFindClient

    concept_codes = db.get_observe_concept_codes()
    if not concept_codes:
        return []
    members_map = db.get_concept_members_map(concept_codes)
    concept_names = _load_concept_names(db)
    watched = set(db.get_watched_concept_codes())

    # 查近 10 个自然日的日K（覆盖 5+ 交易日，够算 1d/3d/5d）
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
    client = IFindClient()

    # 分批查询（100/批），合并结果
    # 解析后的数据：{concept_code: {change_ratio, body, return_3d, return_5d}}
    quotes: Dict[str, Dict] = {}
    batch_size = 100
    t0 = time.time()
    for i in range(0, len(concept_codes), batch_size):
        batch = concept_codes[i:i + batch_size]
        resp = client.get_history_quotation(
            batch, start, today,
            indicators="open,close,preClose,changeRatio",
        )
        if resp.get("errorcode") not in (0, None):
            print(f"[SECTOR-MANAGE] iFinD 批次 {i} 错误: {resp.get('errorcode')}")
            continue
        for item in resp.get("tables", []):
            cc = item.get("thscode", "")
            tbl = item.get("table", {})
            times = item.get("time", [])
            closes = tbl.get("close", [])
            opens = tbl.get("open", [])
            chg_ratios = tbl.get("changeRatio", [])
            if not closes or not chg_ratios:
                continue
            n = len(closes)
            # 当日涨跌幅（最后一根）
            change_ratio = chg_ratios[-1] if chg_ratios[-1] is not None else None
            # 实体涨幅（当日 close vs open）
            body = None
            if n >= 1 and opens and opens[-1] and closes[-1] is not None:
                body = (closes[-1] / opens[-1] - 1) * 100 if opens[-1] != 0 else None
            # 累计涨幅：期末close / 起点close - 1（用 close 序列，比 preClose 更直观）
            def _period_return(days_back: int) -> float | None:
                if n < days_back + 1:
                    return None
                start_close = closes[n - 1 - days_back]
                if start_close and start_close != 0:
                    return (closes[-1] / start_close - 1) * 100
                return None
            quotes[cc] = {
                "change_ratio": round(change_ratio, 4) if change_ratio is not None else None,
                "body": round(body, 4) if body is not None else None,
                "return_3d": round(_period_return(3), 4) if _period_return(3) is not None else None,
                "return_5d": round(_period_return(5), 4) if _period_return(5) is not None else None,
            }
    print(f"[SECTOR-MANAGE] iFinD 行情拉取完成：{len(quotes)}/{len(concept_codes)} 个板块，"
          f"耗时 {time.time()-t0:.1f}s")

    # 组装结果行
    rows = []
    for cc in concept_codes:
        members = members_map.get(cc, [])
        q = quotes.get(cc, {})
        rows.append({
            "concept_code": cc,
            "concept_name": concept_names.get(cc, cc),
            "level": _sector_prefix_label(cc),
            "change_ratio": q.get("change_ratio"),
            "body": q.get("body"),
            "return_3d": q.get("return_3d"),
            "return_5d": q.get("return_5d"),
            "member_count": len(members),
            "watched": cc in watched,
        })
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


def _load_stock_period_returns(db: Database, trade_date: str, days: int) -> Dict[str, float]:
    """
    算个股 N 日累计涨幅 (期末close / 窗口起点pre_close - 1) × 100。
    与 calc_period_return_df 的区别：per-stock 各自取最近交易日对齐（个别股票期末日
    无数据时回退到其最近交易日，而非被全局 end_date 过滤丢弃）。

    窗口起点 = 期末日 - days*2 自然日（覆盖 days 个交易日，与 calc_period_return_df 一致）。
    :return: {stock_code: period_return_pct}
    """
    import sqlite3
    from datetime import datetime, timedelta
    end_obj = datetime.strptime(trade_date, "%Y%m%d")
    start = (end_obj - timedelta(days=days * 2)).strftime("%Y%m%d")
    result: Dict[str, float] = {}
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        # 每只股票各自的期末日（<= trade_date 的最新）+ 窗口起点的 pre_close
        for row in conn.execute(
            """
            WITH latest AS (
                SELECT code, MAX(trade_date) AS end_date
                FROM daily_kline WHERE trade_date <= ? AND trade_date >= ?
                GROUP BY code
            )
            SELECT l.code,
                   l.end_date,
                   (SELECT close FROM daily_kline d WHERE d.code = l.code AND d.trade_date = l.end_date) AS end_close,
                   (SELECT pre_close FROM daily_kline d WHERE d.code = l.code AND d.trade_date = (
                       SELECT MIN(trade_date) FROM daily_kline WHERE code = l.code AND trade_date >= ?
                   )) AS start_pre_close
            FROM latest l
            """,
            (trade_date, start, start),
        ):
            ec, sp = row["end_close"], row["start_pre_close"]
            if ec is not None and sp and sp != 0:
                result[row["code"]] = (ec / sp - 1) * 100
    return result


def _load_stock_body(db: Database, trade_date: str) -> Dict[str, float]:
    """
    读个股实体涨幅 (close - open)/open × 100。
    取每只股票 <= trade_date 的最新一日日K（部分股票期末日可能无数据，回退到其最近交易日）。
    :return: {stock_code: body_ratio}
    """
    import sqlite3
    body = {}
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            """SELECT code, open, close FROM daily_kline
               WHERE (code, trade_date) IN (
                   SELECT code, MAX(trade_date) FROM daily_kline
                   WHERE trade_date <= ? GROUP BY code
               )""",
            (trade_date,),
        ):
            o, c = row["open"], row["close"]
            if o and o != 0 and c is not None:
                body[row["code"]] = (c / o - 1) * 100
    return body


def _load_stock_change_ratio(db: Database, trade_date: str) -> Dict[str, float]:
    """
    读个股涨跌幅（daily_kline.change_ratio，行情接口已算好的真实涨跌幅）。
    取每只股票 <= trade_date 的最新一日（部分股票期末日可能无数据，回退到其最近交易日）。
    :return: {stock_code: change_ratio}
    """
    import sqlite3
    cr = {}
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            """SELECT code, change_ratio FROM daily_kline
               WHERE (code, trade_date) IN (
                   SELECT code, MAX(trade_date) FROM daily_kline
                   WHERE trade_date <= ? GROUP BY code
               )""",
            (trade_date,),
        ):
            v = row["change_ratio"]
            if v is not None:
                cr[row["code"]] = v
    return cr
