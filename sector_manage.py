# -*- coding: utf-8 -*-
"""
监控板块管理：多周期涨幅计算 + 候选板块列表组装。

现役管理页优先使用 iFinD 概念指数实时行情展示当日涨幅、实体、涨跌家数和涨停家数，
并用 iFinD 历史行情补充 3日/5日累计涨幅及实时缺项 fallback。

候选板块范围 = 观察池内成分股数 10~500（含边界）的 884/885/886 板块。
勾选状态单独读 watched_concepts 表，保存时同样执行数量边界校验。
"""

import os
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from database import Database


def _sector_prefix_label(code: str) -> str:
    """按 concept_code 前缀映射层级标签（项目无显式层级字段，靠前缀区分）。"""
    pfx = (code or "")[:3]
    if pfx == "884":
        return "三级行业"
    if pfx in ("885", "886"):
        return "概念板块"
    return "其他"
def compute_sector_quotes_from_ifind(db: Database) -> List[Dict]:
    """
    从 iFinD 实时行情接口（real_time_quotation）获取概念指数实时涨跌幅/实体涨幅/涨跌家数，
    并用接口3（cmd_history_quotation）补充 3d/5d 累计涨幅（日内不变，低频请求）。

    :return: [{concept_code, concept_name, level, change_ratio, body, rise_count,
               fall_count, limit_up_count, return_3d, return_5d, member_count, watched}]
    """
    import time
    from datetime import datetime, timedelta
    from ifind_client import IFindClient

    concept_codes = db.get_observe_concept_codes()
    if not concept_codes:
        return []
    members_map = db.get_concept_members_map(concept_codes)
    concept_codes = [
        cc for cc in concept_codes
        if config.is_monitorable_member_count(len(members_map.get(cc, [])))
    ]
    members_map = {cc: members_map[cc] for cc in concept_codes}
    concept_names = _load_concept_names(db)
    watched = set(db.get_watched_concept_codes())

    client = IFindClient()
    t0 = time.time()

    # —— 1. 实时行情：changeRatio / open / latest / riseCount / fallCount / upLimitCount ——
    rt = client.batch_get_realtime_quotation(
        concept_codes,
        indicators="changeRatio,open,latest,riseCount,fallCount,upLimitCount",
    )

    # —— 2. 3d/5d 累计涨幅 + 实时接口缺数据时的 fallback：接口3 日K（日内不变）——
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
    period_returns: Dict[str, Dict[str, float]] = {}
    # fallback：接口3 的当日 changeRatio/open/close（给实时接口没覆盖的指数补数据）
    fallback: Dict[str, Dict] = {}
    for i in range(0, len(concept_codes), 100):
        batch = concept_codes[i:i + 100]
        resp = client.get_history_quotation(batch, start, today, indicators="close,open,changeRatio")
        if resp.get("errorcode") not in (0, None):
            continue
        for item in resp.get("tables", []):
            cc = item.get("thscode", "")
            tbl = item.get("table", {})
            closes = tbl.get("close")
            opens = tbl.get("open")
            chgs = tbl.get("changeRatio")
            if not closes or not isinstance(closes, list):
                continue
            n = len(closes)
            def _pr(d: int):
                if n < d + 1 or not closes[n - 1 - d] or closes[n - 1 - d] == 0:
                    return None
                return round((closes[-1] / closes[n - 1 - d] - 1) * 100, 4)
            period_returns[cc] = {"return_3d": _pr(3), "return_5d": _pr(5)}
            # fallback：存当日值（实时接口没覆盖的指数用这补）
            if chgs and isinstance(chgs, list) and chgs[-1] is not None:
                fb_change = round(chgs[-1], 4)
                fb_body = None
                if opens and opens[-1] and closes[-1] is not None and opens[-1] != 0:
                    fb_body = round((closes[-1] / opens[-1] - 1) * 100, 4)
                fallback[cc] = {"change_ratio": fb_change, "body": fb_body}

    print(f"[SECTOR-MANAGE] 实时行情 {len(rt)}/{len(concept_codes)} + 累计涨幅 {len(period_returns)}"
          f" + fallback {len(fallback)}, 耗时 {time.time()-t0:.1f}s")

    # —— 3. 组装结果行（实时接口无数据时用接口3日K fallback）——
    rows = []
    for cc in concept_codes:
        members = members_map.get(cc, [])
        q = rt.get(cc, {})
        pr = period_returns.get(cc, {})
        fb = fallback.get(cc, {})
        open_px = q.get("open")
        latest_px = q.get("latest")
        # 实体涨幅 = (latest - open) / open × 100（盘中用 latest，收盘=close）
        body = None
        if open_px and open_px != 0 and latest_px is not None:
            body = round((latest_px / open_px - 1) * 100, 4)
        # 涨跌幅：优先实时接口，无则用接口3日K fallback
        change_ratio = q.get("changeRatio")
        if change_ratio is None:
            change_ratio = fb.get("change_ratio")
            if body is None:
                body = fb.get("body")
        rows.append({
            "concept_code": cc,
            "concept_name": concept_names.get(cc, cc),
            "level": _sector_prefix_label(cc),
            "change_ratio": round(change_ratio, 4) if change_ratio is not None else None,
            "body": body,
            "rise_count": int(q["riseCount"]) if q.get("riseCount") is not None else None,
            "fall_count": int(q["fallCount"]) if q.get("fallCount") is not None else None,
            "limit_up_count": int(q["upLimitCount"]) if q.get("upLimitCount") is not None else None,
            "return_3d": pr.get("return_3d"),
            "return_5d": pr.get("return_5d"),
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
