# -*- coding: utf-8 -*-
"""
知识图谱分析层（P3）：ρ 边权计算 + 族群发现 + 枢纽/联动查询。

ρ（corr_20d）= 个股日收益 vs 所属板块指数日收益 的 20 日滚动 Pearson 相关系数，
挂在 kg_edge.corr_20d 上——静态归属（confidence）之外的动态关联强度。

数据源：
  - 个股收益：本地 daily_kline（近 ~30 自然日的 close 序列）
  - 板块收益：iFinD 接口3（.TI 指数 close 序列，7 批拉全量板块）
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import Database


# ============ ρ 边权计算 ============

def compute_corr_20d(db: Database, window: int = 20, min_obs: int = 8) -> Dict:
    """
    计算全部 open 边的 20 日滚动相关系数并写回 kg_edge.corr_20d。
    :param window: 滚动窗口（交易日）
    :param min_obs: 有效收益样本下限（不足则 corr=NULL）。
                    注意 daily_kline 同步断续时窗口内交易日可能仅 ~10 个，
                    pct_change 再减 1 → 实际样本约 9，故默认放宽到 8。
    :return: 统计 {edges, updated, avg_abs_corr, coverage}
    """
    from ifind_client import IFindClient

    t0 = time.time()
    # 窗口锚定本地 daily_kline 最新交易日（本地个股数据可能滞后于自然日，
    # 板块指数与个股窗口必须对齐同一区间，否则日期交集为空 → corr 全空）
    latest = db.get_latest_trade_date()
    if not latest:
        print("[KG-CORR] daily_kline 无数据，跳过")
        return {"edges": 0, "updated": 0, "coverage": 0, "avg_abs_corr": None, "window": window}
    latest_h = f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"          # YYYYMMDD → YYYY-MM-DD
    latest_d = datetime.strptime(latest, "%Y%m%d")
    start_h = (latest_d - timedelta(days=int(window * 1.8))).strftime("%Y-%m-%d")
    start_c = start_h.replace("-", "")

    # —— 1. 板块指数收益（接口3，全量观察池板块分批，窗口对齐本地数据）——
    sector_codes = db.get_observe_concept_codes()
    client = IFindClient()
    sector_ret: Dict[str, pd.Series] = {}   # {sector_code: 收益序列(index=日期)}
    for i in range(0, len(sector_codes), 100):
        batch = sector_codes[i:i + 100]
        resp = client.get_history_quotation(batch, start_h, latest_h, indicators="close")
        if resp.get("errorcode") not in (0, None):
            continue
        for item in resp.get("tables", []):
            cc = item.get("thscode", "")
            closes = item.get("table", {}).get("close")
            times = item.get("time", [])
            if not closes or not isinstance(closes, list) or len(closes) < 2:
                continue
            s = pd.Series([c if c is not None else np.nan for c in closes],
                          index=times, dtype=float).ffill()
            sector_ret[cc] = s.pct_change().iloc[1:] * 100   # 日收益 %
    print(f"[KG-CORR] 窗口 [{start_c}..{latest}]，板块指数收益 {len(sector_ret)}/{len(sector_codes)} 个，"
          f"耗时 {time.time()-t0:.1f}s")

    # —— 2. 个股收益（本地 daily_kline，一次全量）——
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute(
            "SELECT code, trade_date, close FROM daily_kline WHERE trade_date >= ? AND trade_date <= ?",
            (start_c, latest)).fetchall()
    stock_df = pd.DataFrame(rows, columns=["code", "date", "close"])
    stock_ret: Dict[str, pd.Series] = {}
    if not stock_df.empty:
        stock_df = stock_df.sort_values(["code", "date"])
        stock_df["ret"] = stock_df.groupby("code")["close"].pct_change() * 100
        for code, g in stock_df.groupby("code"):
            stock_ret[code] = pd.Series(g["ret"].values, index=g["date"].values)
    print(f"[KG-CORR] 个股收益 {len(stock_ret)} 只（daily_kline [{start_c}..{latest}] {len(rows)} 行）")

    # —— 3. 逐边算 Pearson（向量化：按边循环 + 序列对齐，窗口取最近 window 日）——
    edges = db.get_kg_open_edges()
    updates: List[Dict] = []
    corr_vals: List[float] = []
    for e in edges:
        stock = e["src_id"].split(":", 1)[1]
        sector = e["dst_id"].split(":", 1)[1]
        sr, kr = stock_ret.get(stock), sector_ret.get(sector)
        if sr is None or kr is None:
            updates.append({"edge_id": e["edge_id"], "corr_20d": None})
            continue
        # 对齐两序列（指数日期是 YYYY-MM-DD，个股是 YYYYMMDD）。
        # 先按日期 inner join 去掉 NaN，再 tail(window)——顺序不能反：
        # 分别截尾会让两条长度不同的序列窗口起点错位，交集被砍（实测 10 天砍到 4 天）。
        s = sr.copy()
        s.index = s.index.astype(str).str.replace("-", "", regex=False)
        k = kr.copy()
        k.index = k.index.astype(str).str.replace("-", "", regex=False)
        joined = pd.concat([s, k], axis=1, join="inner").dropna().tail(window)
        if len(joined) < min_obs or joined.iloc[:, 0].std() == 0 or joined.iloc[:, 1].std() == 0:
            updates.append({"edge_id": e["edge_id"], "corr_20d": None})
            continue
        c = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
        corr_vals.append(abs(c))
        updates.append({"edge_id": e["edge_id"], "corr_20d": round(c, 4)})

    db.update_kg_edge_corr(updates)
    stats = {
        "edges": len(edges),
        "updated": len(corr_vals),
        "coverage": round(len(corr_vals) / len(edges), 4) if edges else 0,
        "avg_abs_corr": round(float(np.mean(corr_vals)), 4) if corr_vals else None,
        "window": window,
    }
    print(f"[KG-CORR] 完成：{stats['updated']}/{stats['edges']} 条边有 corr"
          f"（覆盖 {stats['coverage']:.1%}，平均|ρ|={stats['avg_abs_corr']}），"
          f"总耗时 {time.time()-t0:.1f}s")
    return stats


# ============ 族群发现 ============

def detect_communities(db: Database, use_corr: bool = True, min_confidence: float = 0.6) -> Dict:
    """
    Louvain 社区发现（股-板块二部图全量，边权=|corr|，未算 corr 时退化为 1），
    结果按规模重排编号写入 kg_community（覆盖当日）。
    """
    import networkx as nx
    from networkx.algorithms.community import louvain_communities

    edges = db.get_kg_open_edges()
    G = nx.Graph()
    for e in edges:
        if e["confidence"] < min_confidence:
            continue
        w = abs(e["corr_20d"]) if (use_corr and e["corr_20d"] is not None) else 1.0
        G.add_edge(e["src_id"], e["dst_id"], weight=w)
    communities = louvain_communities(G, weight="weight", seed=42)
    scored = sorted(communities, key=len, reverse=True)

    today = datetime.now().strftime("%Y%m%d")
    rows = []
    for cid, comm in enumerate(scored, 1):
        for nid in comm:
            rows.append({"community_id": cid, "node_id": nid,
                         "node_type": "stock" if nid.startswith("STOCK:") else "sector"})
    db.replace_kg_communities(today, rows)
    stats = {"communities": len(scored), "members": len(rows),
             "top_sizes": [len(c) for c in scored[:5]]}
    print(f"[KG-COMM] 族群 {len(scored)} 个已写入 kg_community（{today}），"
          f"Top5 规模 {stats['top_sizes']}")
    return stats


# ============ 枢纽 / 联动查询 ============

def hub_sectors(db: Database, top_n: int = 10) -> List[Dict]:
    """枢纽板块：open 边数（去重归属股票数）TopN。"""
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT n.code, n.name, COUNT(*) AS degree
            FROM kg_edge e JOIN kg_node n ON e.dst_id = n.node_id
            WHERE e.valid_to IS NULL GROUP BY e.dst_id ORDER BY degree DESC LIMIT ?
        """, (top_n,)).fetchall()
    return [dict(r) for r in rows]


def locate_sectors(db: Database, stock_codes: List[str], min_hits: int = 2,
                   top_n: int = 20, order: str = "lift") -> Dict:
    """
    组合定位：一批股票 → 它们共同指向的概念/行业板块（双指标排序）。

    指标：
      hits       命中数——组合中有多少只股属于该板块（最大公约数，找主要题材）
      lift       富集倍数——组内命中率 / 该板块成员占全市场比例。
                 消除"融资融券/深股通"类大基数枢纽板块的命中噪音，找真正异常聚集的小圈子。
    :param stock_codes: 股票代码列表（带后缀；图谱中不存在的自动记入 unresolved）
    :param order: lift（默认，富集倍数降序）| hits（命中数降序）
    """
    import sqlite3
    stock_codes = list(dict.fromkeys(c.strip() for c in stock_codes if c and c.strip()))
    if not stock_codes:
        return {"total": 0, "matched": 0, "unresolved": [], "sectors": []}

    sids = [f"STOCK:{c}" for c in stock_codes]
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        total_stocks = conn.execute(
            "SELECT COUNT(*) c FROM kg_node WHERE node_type='stock' AND is_active=1").fetchone()["c"]
        placeholders = ",".join("?" for _ in sids)
        rows = conn.execute(f"""
            SELECT n.code AS sector_code, n.name AS sector_name, n.sector_type,
                   COUNT(DISTINCT e.src_id) AS hits,
                   json_extract(n.props_json, '$.member_count') AS members,
                   AVG(e.corr_20d) AS avg_corr
            FROM kg_edge e JOIN kg_node n ON e.dst_id = n.node_id
            WHERE e.valid_to IS NULL AND e.src_id IN ({placeholders})
            GROUP BY e.dst_id
            HAVING hits >= ?
        """, (*sids, min_hits)).fetchall()

    n_group = len(sids)
    sectors, matched_sids = [], set()
    for r in rows:
        members = r["members"] or 0
        lift = round(r["hits"] / n_group * total_stocks / members, 1) if members > 0 else None
        sectors.append({
            "sector_code": r["sector_code"], "sector_name": r["sector_name"],
            "sector_type": r["sector_type"], "hits": r["hits"],
            "group_ratio": round(r["hits"] / n_group, 3),
            "members": members, "lift": lift,
            "avg_corr": round(r["avg_corr"], 3) if r["avg_corr"] is not None else None,
        })
    sectors.sort(key=lambda s: (-(s["lift"] or 0), -s["hits"]) if order == "lift" else (-s["hits"], -(s["lift"] or 0)))

    # 未命中任何图谱板块的输入代码（新股/停牌/写错）
    with sqlite3.connect(db.db_path) as conn:
        ph2 = ",".join("?" for _ in sids)
        matched_sids = {r[0] for r in conn.execute(
            f"SELECT DISTINCT src_id FROM kg_edge WHERE src_id IN ({ph2}) AND valid_to IS NULL", sids)}
    return {"total": n_group, "matched": len(matched_sids),
            "unresolved": [c for c in stock_codes if f"STOCK:{c}" not in matched_sids],
            "sectors": sectors[:top_n]}


def classify_hits(db: Database, stock_codes: List[str], min_hits: int = 2,
                  top_n: int = 30, order: str = "lift") -> List[Dict]:
    """
    强势归类（KG 版）：一批命中股 → 板块富集归类，含每个板块的命中股明细与每股 ρ。

    与 locate_sectors 的分工：locate 是"探查"（板块级聚合，双指标表），
    classify 是"归类"（喂全市场强势归类页，要每股归属明细 + 勾选标记）。

    成员数口径：优先 props.member_count（概念字典快照），缺失则用图谱度数
    （KG open 边计数）兜底——14 个字典外板块（如 机器人概念）由此获得 lift。
    """
    import sqlite3
    stock_codes = list(dict.fromkeys(c.strip() for c in stock_codes if c and c.strip()))
    if not stock_codes:
        return []
    sids = [f"STOCK:{c}" for c in stock_codes]
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        total_stocks = conn.execute(
            "SELECT COUNT(*) c FROM kg_node WHERE node_type='stock' AND is_active=1").fetchone()["c"]
        # 全部板块的图谱度数（lift 兜底口径 + 也用于展示成员数）
        degrees = {r["dst_id"]: r["c"] for r in conn.execute(
            "SELECT dst_id, COUNT(*) c FROM kg_edge WHERE valid_to IS NULL GROUP BY dst_id")}
        placeholders = ",".join("?" for _ in sids)
        rows = conn.execute(f"""
            SELECT e.src_id, e.corr_20d, n.node_id, n.code, n.name, n.sector_type,
                   json_extract(n.props_json, '$.member_count') AS props_members
            FROM kg_edge e JOIN kg_node n ON e.dst_id = n.node_id
            WHERE e.valid_to IS NULL AND e.src_id IN ({placeholders})
        """, sids).fetchall()

    by_sector: Dict[str, Dict] = {}
    for r in rows:
        d = by_sector.setdefault(r["node_id"], {
            "sector_code": r["code"], "sector_name": r["name"], "sector_type": r["sector_type"],
            "members": r["props_members"] or degrees.get(r["node_id"], 0),
            "hits": []})
        d["hits"].append({"code": r["src_id"].split(":", 1)[1],
                          "corr_20d": round(r["corr_20d"], 3) if r["corr_20d"] is not None else None})

    n_group = len(sids)
    out = []
    for d in by_sector.values():
        if len(d["hits"]) < min_hits:
            continue
        members = d["members"] or 0
        d["hit_count"] = len(d["hits"])
        d["lift"] = round(d["hit_count"] / n_group * total_stocks / members, 1) if members > 0 else None
        out.append(d)
    out.sort(key=lambda s: (-(s["lift"] or 0), -s["hit_count"]) if order == "lift"
             else (-s["hit_count"], -(s["lift"] or 0)))
    return out[:top_n]




def linked_stocks(db: Database, stock_code: str, top_n: int = 10,
                  min_corr: float = 0.3) -> List[Dict]:
    """
    联动股：与目标股共享板块的其他股票。
    排序分 = Σ(共享板块的 corr_20d)，corr 缺失按 0 计；同时返回共享板块明细。
    """
    mine = db.get_kg_edges_for_stock(stock_code)
    if not mine:
        return []
    sector_ids = {f"SECTOR:{e['sector_code']}": e for e in mine}

    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in sector_ids)
        rows = conn.execute(f"""
            SELECT e2.src_id, e2.corr_20d, e2.dst_id, n.code, n.name
            FROM kg_edge e2 JOIN kg_node n ON e2.src_id = n.node_id
            WHERE e2.dst_id IN ({placeholders}) AND e2.src_id != ? AND e2.valid_to IS NULL
        """, (*sector_ids.keys(), f"STOCK:{stock_code}")).fetchall()

    agg: Dict[str, Dict] = defaultdict(lambda: {"name": "", "shared": 0, "score": 0.0, "via": []})
    for r in rows:
        d = agg[r["code"]]
        d["name"] = r["name"]
        d["shared"] += 1
        c = r["corr_20d"] or 0.0
        d["score"] += abs(c)
        sec_name = sector_ids[r["dst_id"]]["sector_name"]
        d["via"].append({"sector": sec_name, "corr": r["corr_20d"]})
    result = sorted(agg.items(), key=lambda kv: (-kv[1]["score"], -kv[1]["shared"]))[:top_n]
    return [{"code": k, **v, "score": round(v["score"], 3)} for k, v in result]
