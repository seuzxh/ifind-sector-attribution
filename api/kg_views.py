# -*- coding: utf-8 -*-
"""知识图谱视图组装：节点解析 / 族群与变更查询 / cytoscape 图供数（从原 api_server handler 下沉）"""

import sqlite3
from collections import defaultdict as _dd
from typing import Dict, List


def resolve_kg_node(db, code: str):
    """解析输入为 kg_node（股票或板块），找不到返回 None。"""
    key = code.strip().upper()
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cands = ([key] if key.endswith(".TI") else [key + ".TI"]) if key[:3] in ("884", "885", "886") \
            else ([key] if "." in key else [key + s for s in (".SH", ".SZ", ".BJ")])
        for c in cands:
            row = conn.execute(
                "SELECT node_id, node_type, code, name FROM kg_node WHERE code = ? AND is_active = 1",
                (c,)).fetchone()
            if row:
                return dict(row)
    return None


def get_kg_changes_view(db, date: str = None, change_type: str = None, limit: int = 200) -> dict:
    """变更日志查询组装（默认最近日期）。"""
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not date:
            row = conn.execute("SELECT MAX(change_date) FROM kg_change").fetchone()
            date = row[0] if row else None
        if not date:
            return {"date": None, "count": 0, "changes": []}
        sql = "SELECT * FROM kg_change WHERE change_date = ?"
        params: list = [date]
        if change_type:
            sql += " AND change_type = ?"
            params.append(change_type)
        sql += " ORDER BY rowid LIMIT ?"
        params.append(min(limit, 1000))
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return {"date": date, "count": len(rows), "changes": rows}


def get_kg_communities_view(db, only_sector: bool = True) -> dict:
    """板块族群视图（最近一次 kg_corr 的 Louvain 结果，按规模降序）。"""
    rows = db.get_latest_kg_communities()
    if not rows:
        return {"error": "族群未计算，先运行 python main.py kg_corr"}
    node_names = {}
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT node_id, name, node_type FROM kg_node"):
            node_names[r["node_id"]] = (r["name"], r["node_type"])
    groups: Dict[int, List[Dict]] = {}
    for r in rows:
        name, ntype = node_names.get(r["node_id"], (r["node_id"], r["node_type"]))
        if only_sector and ntype != "sector":
            continue
        groups.setdefault(r["community_id"], []).append(
            {"node_id": r["node_id"], "name": name, "type": ntype})
    out = [{"community_id": cid, "size": len(members), "members": members}
           for cid, members in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    return {"count": len(out), "communities": out}


def get_kg_locate_groups_view(db) -> dict:
    """自选分组列表（组合定位下拉用；名称已 TRIM，同花顺导出名常带尾随空格）。"""
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT group_id AS id, TRIM(group_name) AS name, COUNT(*) AS count
            FROM custom_group GROUP BY group_id, TRIM(group_name) ORDER BY count DESC
        """).fetchall()
    return {"groups": [dict(r) for r in rows]}


# —— 图供数（cytoscape elements 格式：{nodes:[{data}], edges:[{data}]}）——

def _comm_color_map(db) -> Dict[str, int]:
    return {r["node_id"]: r["community_id"] for r in db.get_latest_kg_communities()}


def build_projection_graph(db, min_jaccard: float = 0.3) -> dict:
    """板块投影图：节点=板块（族群着色分组 + 成分数 + 平均关联度），边=成分重叠 Jaccard≥阈值。"""
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        sectors = [dict(r) for r in conn.execute(
            "SELECT node_id, code, name, sector_type FROM kg_node "
            "WHERE node_type='sector' AND is_active=1")]
        rows = conn.execute(
            "SELECT src_id, dst_id, corr_20d FROM kg_edge WHERE valid_to IS NULL").fetchall()
    members: Dict[str, set] = _dd(set)
    corr_sum = _dd(float)
    corr_n = _dd(int)
    for r in rows:
        members[r["dst_id"]].add(r["src_id"])
        if r["corr_20d"] is not None:
            corr_sum[r["dst_id"]] += abs(r["corr_20d"])
            corr_n[r["dst_id"]] += 1
    comm = _comm_color_map(db)

    nodes = [{
        "data": {
            "id": s["node_id"], "label": s["name"] or s["code"],
            "kind": "sector", "sector_type": s["sector_type"],
            "member_count": len(members.get(s["node_id"], ())),
            "avg_corr": round(corr_sum[s["node_id"]] / corr_n[s["node_id"]], 3) if corr_n[s["node_id"]] else None,
            **({"community": comm[s["node_id"]]} if s["node_id"] in comm else {}),
        }
    } for s in sectors]

    edges = []
    ids = [s["node_id"] for s in sectors]
    for i in range(len(ids)):
        mi = members.get(ids[i], set())
        if not mi:
            continue
        for j in range(i + 1, len(ids)):
            mj = members.get(ids[j], set())
            if not mj:
                continue
            inter = len(mi & mj)
            if not inter:
                continue
            jac = inter / len(mi | mj)
            if jac >= min_jaccard:
                edges.append({"data": {
                    "id": f"{ids[i]}~{ids[j]}", "source": ids[i], "target": ids[j],
                    "weight": round(jac, 3), "overlap": inter,
                }})
    return {"node_count": len(nodes), "edge_count": len(edges),
            "elements": {"nodes": nodes, "edges": edges}}


def build_star_graph(db, code: str, limit: int = 20) -> dict:
    """个股星型图：中心=个股，邻接=归属板块（按|ρ|），外圈=联动股（按评分 TopN）。"""
    from kg_analysis import linked_stocks
    node = resolve_kg_node(db, code)
    if not node or node["node_type"] != "stock":
        return {"error": f"未找到个股 {code}"}
    mine = db.get_kg_edges_for_stock(node["code"])
    linked = linked_stocks(db, node["code"], top_n=limit)

    nodes = [{"data": {"id": node["node_id"], "label": node["name"] or node["code"],
                       "kind": "stock", "code": node["code"]}}]
    edges = []
    for e in sorted(mine, key=lambda x: -(abs(x["corr_20d"]) if x["corr_20d"] is not None else -1)):
        sid = f"SECTOR:{e['sector_code']}"
        nodes.append({"data": {"id": sid, "label": e["sector_name"], "kind": "sector"}})
        edges.append({"data": {"id": f"E{e['edge_id']}", "source": node["node_id"], "target": sid,
                               "weight": round(abs(e["corr_20d"] or 0), 3),
                               "corr": e["corr_20d"], "confidence": e["confidence"]}})
    for lk in linked:
        nid = f"STOCK:{lk['code']}"
        nodes.append({"data": {"id": nid, "label": lk["name"] or lk["code"],
                               "kind": "stock_linked", "score": lk["score"]}})
        edges.append({"data": {"id": f"L{lk['code']}", "source": node["node_id"], "target": nid,
                               "weight": round(lk["score"], 3), "score": lk["score"],
                               "shared": lk["shared"]}})
    return {"code": node["code"], "elements": {"nodes": nodes, "edges": edges}}


def build_sector_graph(db, code: str, limit: int = 30) -> dict:
    """板块展开图：中心=板块，邻接=成分股（按|ρ| TopN 截断）。"""
    node = resolve_kg_node(db, code)
    if not node or node["node_type"] != "sector":
        return {"error": f"未找到板块 {code}"}
    edges = db.get_kg_edges_for_sector(node["code"])
    edges.sort(key=lambda e: -(abs(e["corr_20d"]) if e["corr_20d"] is not None else -1))
    top = edges[:limit]
    nodes = [{"data": {"id": node["node_id"], "label": node["name"] or node["code"],
                       "kind": "sector", "code": node["code"], "member_count": len(edges)}}]
    g_edges = []
    for e in top:
        nid = f"STOCK:{e['stock_code']}"
        nodes.append({"data": {"id": nid, "label": e["stock_name"] or e["stock_code"],
                               "kind": "stock", "code": e["stock_code"]}})
        g_edges.append({"data": {"id": f"E{e['edge_id']}", "source": node["node_id"], "target": nid,
                                 "weight": round(abs(e["corr_20d"] or 0), 3),
                                 "corr": e["corr_20d"], "confidence": e["confidence"]}})
    return {"code": node["code"], "total_members": len(edges), "shown": len(top),
            "elements": {"nodes": nodes, "edges": g_edges}}
