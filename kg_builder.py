# -*- coding: utf-8 -*-
"""
知识图谱构建器：bootstrap（首次构建）+ 统计报告 + 族群初探。

P1 范围（DESIGN-knowledge-graph.md §五）：
  幂等门 → SECTOR 节点（观察池全集）→ 主源/验证源归属边（confidence 分级）
  → STOCK 节点 → kg_snapshot + 统计报告 → NetworkX Louvain 族群初探

周维护 diff（kg_update）属 P2，本文件不含。
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from database import Database
from kg_sources import IfindMembersAdapter, IfindStockConceptAdapter, Pair

# confidence 分级：双源命中 1.0 / 仅主源 0.8 / 仅验证源 0.6
CONF_BOTH = 1.0
CONF_PRIMARY_ONLY = 0.8
CONF_VERIFY_ONLY = 0.6


def _sid(stock_code: str) -> str:
    return f"STOCK:{stock_code}"


def _sec(sector_code: str) -> str:
    return f"SECTOR:{sector_code}"


def _sector_type(code: str) -> str:
    pfx = code[:3]
    if pfx == "884":
        return "industry"
    if pfx in ("885", "886"):
        return "concept"
    return "other"


def kg_bootstrap(db: Database, force: bool = False, refetch: bool = False,
                 skip_verify: bool = False) -> Dict:
    """
    首次构建知识图谱。
    :param force: 已有快照时清空 4 张 kg 表重建
    :param refetch: 强制重拉接口2（否则当天快照直接复用）
    :param skip_verify: 跳过接口1交叉验证源
    :return: 统计 dict（同时写入 kg_snapshot.stats_json）
    """
    # —— 幂等门 ——
    if db.has_kg_snapshot():
        if not force:
            print("[KG] 已存在图谱快照，拒绝重复构建（--force 可清空重建）")
            return {"error": "snapshot_exists"}
        print("[KG] --force：清空 kg 表重建...")
        db.clear_kg_tables()

    today = datetime.now().strftime("%Y%m%d")
    t0 = datetime.now()

    # —— 1. SECTOR 节点：观察池全集（图谱求全，与监控层资格规则解耦但带标记）——
    sector_codes = db.get_observe_concept_codes()
    concept_names = _load_concept_names(db)
    members_map = db.get_concept_members_map(sector_codes)
    sector_nodes = []
    for cc in sector_codes:
        n_members = len(members_map.get(cc, []))
        sector_nodes.append({
            "node_id": _sec(cc),
            "node_type": "sector",
            "code": cc,
            "name": concept_names.get(cc, cc),
            "sector_type": _sector_type(cc),
            "props_json": json.dumps({
                "member_count": n_members,
                "monitorable": bool(config.is_monitorable_member_count(n_members)),
            }, ensure_ascii=False),
            "first_seen": today,
            "last_seen": today,
        })
    db.save_kg_nodes(sector_nodes)
    print(f"[KG] SECTOR 节点 {len(sector_nodes)} 个入库")

    # —— 2. 主源归属对（接口2，优先复用当天快照）——
    primary: List[Pair] = IfindMembersAdapter(db, refetch=refetch).fetch_pairs()

    # —— 3. 验证源归属对（接口1，可跳过）——
    verify: List[Pair] = []
    if not skip_verify:
        verify = IfindStockConceptAdapter(db).fetch_pairs()

    # —— 4. 合并建边（confidence 分级）——
    stock_names: Dict[str, str] = {}
    primary_pairs: Set[Tuple[str, str]] = set()
    for stock, sector, props in primary:
        primary_pairs.add((stock, sector))
        if props.get("stock_name"):
            stock_names[stock] = props["stock_name"]

    verify_pairs: Set[Tuple[str, str]] = set()
    verify_sector_names: Dict[str, str] = {}   # 接口1 返回的概念名（补建字典外板块节点用）
    for stock, sector, props in verify:
        verify_pairs.add((stock, sector))
        if props.get("concept_name"):
            verify_sector_names[sector] = props["concept_name"]
        if props.get("stock_name") and stock not in stock_names:
            stock_names[stock] = props["stock_name"]

    both = primary_pairs & verify_pairs
    primary_only = primary_pairs - verify_pairs
    verify_only = verify_pairs - primary_pairs
    print(f"[KG] 归属对合并：双源 {len(both)} | 仅主源 {len(primary_only)} | 仅验证源 {len(verify_only)}")

    edges = []
    for stock, sector in both:
        edges.append(_edge(stock, sector, "ifind_p03473", today, CONF_BOTH))
    for stock, sector in primary_only:
        edges.append(_edge(stock, sector, "ifind_p03473", today, CONF_PRIMARY_ONLY))
    for stock, sector in verify_only:
        edges.append(_edge(stock, sector, "ifind_concept", today, CONF_VERIFY_ONLY, only_source=True))
    db.save_kg_edges(edges)
    print(f"[KG] 边 {len(edges)} 条入库")

    # —— 5. STOCK 节点（两源并集反推）——
    all_stocks = {s for s, _ in primary_pairs} | {s for s, _ in verify_pairs}
    stock_nodes = [{
        "node_id": _sid(s),
        "node_type": "stock",
        "code": s,
        "name": stock_names.get(s, ""),
        "sector_type": None,
        "props_json": "{}",
        "first_seen": today,
        "last_seen": today,
    } for s in sorted(all_stocks)]
    db.save_kg_nodes(stock_nodes)
    print(f"[KG] STOCK 节点 {len(stock_nodes)} 个入库")

    # —— 5.5 补建字典外板块节点 ——
    # 接口1 可能返回 ths_concept_dict 里没有的概念码（885517/885733 等），
    # 这些码没有 SECTOR 节点 → 边会变孤儿（JOIN 丢失）。从边涉及的全部板块码补齐，
    # 名称优先用接口1 返回的 concept_name，props 标记 in_dict=false 供后续清洗。
    all_edge_sectors = {sec for _, sec in primary_pairs} | {sec for _, sec in verify_pairs}
    known_sectors = {n["node_id"] for n in sector_nodes}
    missing = sorted(s for s in all_edge_sectors if _sec(s) not in known_sectors)
    if missing:
        extra_nodes = [{
            "node_id": _sec(s),
            "node_type": "sector",
            "code": s,
            "name": verify_sector_names.get(s, s),
            "sector_type": _sector_type(s),
            "props_json": json.dumps({"member_count": 0, "in_dict": False}, ensure_ascii=False),
            "first_seen": today,
            "last_seen": today,
        } for s in missing]
        db.save_kg_nodes(extra_nodes)
        print(f"[KG] 补建字典外板块节点 {len(extra_nodes)} 个（props.in_dict=false，"
              f"例：{missing[:3]}）")
    sector_nodes.extend(
        {"node_id": _sec(s), "code": s, "name": verify_sector_names.get(s, s)}
        for s in missing
    )
    # 合入名称映射，让报告/族群里也显示这些板块名（而非问号/裸代码）
    for s in missing:
        concept_names[s] = verify_sector_names.get(s, s)

    # —— 6. 统计报告 + 快照 ——
    stats = _build_stats(db, sector_codes, primary_pairs, verify_pairs,
                         both, primary_only, verify_only)
    stats["node_count"] = len(sector_nodes) + len(stock_nodes)
    stats["edge_count"] = len(edges)
    stats["added_edges"] = len(edges)
    stats["removed_edges"] = 0
    stats["sources"] = ["ifind_p03473"] + ([] if skip_verify else ["ifind_concept"])
    db.save_kg_snapshot(today, stats, stats["sources"])

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"[KG] 构建完成，耗时 {elapsed:.1f}s")
    _print_report(db, stats, sector_codes, concept_names)
    _explore_communities(db, concept_names)
    return stats


def _edge(stock: str, sector: str, source: str, valid_from: str,
          confidence: float, only_source: bool = False) -> Dict:
    props = {}
    if only_source:
        props["only_source"] = True
    return {
        "src_id": _sid(stock),
        "dst_id": _sec(sector),
        "edge_type": "BELONGS_TO",
        "source": source,
        "valid_from": valid_from,
        "valid_to": None,
        "props_json": json.dumps(props, ensure_ascii=False) if props else None,
        "confidence": confidence,
    }


# ============ P2：周维护（diff 引擎） ============

# per-pair 状态：(primary_hit, verify_hit) → 边的 source/confidence
_STATE_BOTH = ("ifind_p03473", CONF_BOTH)
_STATE_PRIMARY = ("ifind_p03473", CONF_PRIMARY_ONLY)
_STATE_VERIFY = ("ifind_concept", CONF_VERIFY_ONLY)


def kg_update(db: Database, skip_verify: bool = False, force: bool = False) -> Dict:
    """
    周维护：拉两源最新归属 → 与当前 open 边按 (股,板块) 对做状态 diff →
    开/关边 + 升降级 + 变更日志 + 周快照。幂等：当日已有快照则拒绝（--force 重做）。

    状态机（old_state → new_state，均基于 primary/verify 两集合的命中组合）：
      none→X           建边（edge_added）
      X→none           关边（edge_removed）
      primary↔verify   关旧建新（source 变化）
      单源→both        关旧建 conf=1.0（edge_upgraded）
      both→单源        关旧建对应单源边（edge_downgraded）
    所有关闭只置 valid_to（历史可回放），从不删除。
    """
    today = datetime.now().strftime("%Y%m%d")

    # —— 幂等门 ——
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM kg_snapshot WHERE snapshot_id=?", (today,)).fetchone()[0]
    if n and not force:
        print(f"[KG-UPDATE] {today} 快照已存在，拒绝重复（--force 重做）")
        return {"error": "snapshot_exists"}

    # —— 1. 拉两源最新 ——
    primary_pairs_raw = IfindMembersAdapter(db, refetch=False).fetch_pairs()
    verify_pairs_raw = [] if skip_verify else IfindStockConceptAdapter(db).fetch_pairs()
    new_primary = {(s, c) for s, c, _ in primary_pairs_raw}
    new_verify = {(s, c) for s, c, _ in verify_pairs_raw}
    verify_sector_names = {c: p.get("concept_name", "") for _, c, p in verify_pairs_raw if p.get("concept_name")}

    # —— 2. 当前 open 边 → per-pair 旧状态 ——
    # 存储形态：一个 (股,板块) 对只有一条 open 边（bootstrap 聚合存储、update 关旧开新均保持）。
    # 旧状态从边自身的 confidence 恢复：1.0=both / 0.8=仅主源 / 0.6=仅验证源。
    # （不能按 source 字段恢复——双源边的 source 是主源，会把 both 误读成 primary。）
    _CONF_TO_STATE = {CONF_BOTH: _STATE_BOTH, CONF_PRIMARY_ONLY: _STATE_PRIMARY,
                      CONF_VERIFY_ONLY: _STATE_VERIFY}
    open_edges = db.get_kg_open_edges()
    old_by_pair: Dict[Tuple[str, str], Tuple[int, tuple]] = {}   # pair -> (edge_id, state)
    for e in open_edges:
        stock = e["src_id"].split(":", 1)[1]
        sector = e["dst_id"].split(":", 1)[1]
        old_by_pair[(stock, sector)] = (e["edge_id"], _CONF_TO_STATE.get(e["confidence"]))

    # —— 3. 状态机 diff ——
    all_pairs = set(old_by_pair) | new_primary | new_verify
    to_add: List[Dict] = []            # 新建边
    to_close: List[int] = []           # 关闭的 edge_id
    changes: List[Dict] = []           # kg_change 日志
    n_added = n_removed = n_up = n_down = n_src = 0

    def _pair_state(pr: bool, vf: bool):
        if pr and vf:
            return _STATE_BOTH
        if pr:
            return _STATE_PRIMARY
        if vf:
            return _STATE_VERIFY
        return None

    for pair in all_pairs:
        stock, sector = pair
        old_entry = old_by_pair.get(pair)
        old_state = old_entry[1] if old_entry else None
        new_state = _pair_state(pair in new_primary, pair in new_verify)
        if old_state == new_state:
            continue
        detail = json.dumps({"stock": stock, "sector": sector}, ensure_ascii=False)
        # 关闭旧边（若有）
        old_ids = [old_entry[0]] if old_entry else []
        to_close.extend(old_ids)
        if old_state is None:
            src, conf = new_state
            to_add.append(_edge(stock, sector, src, today, conf, only_source=(conf == CONF_VERIFY_ONLY)))
            changes.append({"change_date": today, "node_id": _sid(stock), "edge_id": None,
                            "change_type": "edge_added", "detail_json": detail})
            n_added += 1
        elif new_state is None:
            for eid in old_ids:
                changes.append({"change_date": today, "node_id": _sid(stock), "edge_id": eid,
                                "change_type": "edge_removed", "detail_json": detail})
            n_removed += 1
        else:
            src, conf = new_state
            to_add.append(_edge(stock, sector, src, today, conf, only_source=(conf == CONF_VERIFY_ONLY)))
            if new_state == _STATE_BOTH:
                ctype, n_up = "edge_upgraded", n_up + 1
            elif old_state == _STATE_BOTH:
                ctype, n_down = "edge_downgraded", n_down + 1
            else:                       # primary ↔ verify 互换（源变化）
                ctype, n_src = "edge_source_changed", n_src + 1
            for eid in old_ids:
                changes.append({"change_date": today, "node_id": _sid(stock), "edge_id": eid,
                                "change_type": ctype, "detail_json": detail})

    print(f"[KG-UPDATE] diff：新增 {n_added} | 移除 {n_removed} | 升级 {n_up} | 降级 {n_down} | 源切换 {n_src}")

    # —— 4. 落库 ——
    db.close_kg_edges(to_close, today)
    if to_add:
        db.save_kg_edges(to_add)
        # 新边涉及的节点若不存在则补建（新股票/字典外新板块）
        _ensure_nodes(db, new_primary | new_verify, verify_sector_names, today, changes)

    # —— 5. 节点维护：touch last_seen + 停用长期未确认节点 ——
    active_node_ids = {_sid(s) for s, _ in (new_primary | new_verify)} | \
                      {_sec(c) for _, c in (new_primary | new_verify)}
    db.touch_kg_nodes(sorted(active_node_ids), today)
    stale_before = (datetime.now() - timedelta(days=28)).strftime("%Y%m%d")
    stale = db.deactivate_stale_kg_nodes(stale_before)
    for nid in stale:
        changes.append({"change_date": today, "node_id": nid, "edge_id": None,
                        "change_type": "node_deactivated",
                        "detail_json": json.dumps({"reason": "28天未确认"})})

    # —— 6. 变更日志 + 快照 ——
    db.save_kg_changes(changes)
    stats = {
        "node_count": len(db.get_kg_nodes()),
        "edge_count": len(db.get_kg_open_edges()),
        "added_edges": n_added, "removed_edges": n_removed,
        "upgraded": n_up, "downgraded": n_down, "source_changed": n_src,
        "nodes_deactivated": len(stale),
        "changes_logged": len(changes),
    }
    db.save_kg_snapshot(today, stats, ["ifind_p03473"] + ([] if skip_verify else ["ifind_concept"]))
    print(f"[KG-UPDATE] 完成：open 边 {stats['edge_count']}，变更日志 {len(changes)} 条")
    _summarize_changes(changes)
    return stats


def _ensure_nodes(db: Database, pairs: Set, verify_sector_names: Dict[str, str],
                  today: str, changes: List[Dict]):
    """确保 pairs 涉及的节点都存在（新股票建节点、字典外板块补建），缺失记录 node_added。"""
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        existing = {r[0] for r in conn.execute("SELECT node_id FROM kg_node")}
    stocks = {s for s, _ in pairs}
    sectors = {c for _, c in pairs}
    new_nodes = []
    for s in sorted(stocks):
        if _sid(s) not in existing:
            new_nodes.append({"node_id": _sid(s), "node_type": "stock", "code": s,
                              "name": "", "sector_type": None, "props_json": "{}",
                              "first_seen": today, "last_seen": today})
    for c in sorted(sectors):
        if _sec(c) not in existing:
            new_nodes.append({"node_id": _sec(c), "node_type": "sector", "code": c,
                              "name": verify_sector_names.get(c, c), "sector_type": _sector_type(c),
                              "props_json": json.dumps({"member_count": 0, "in_dict": False}),
                              "first_seen": today, "last_seen": today})
    if new_nodes:
        db.save_kg_nodes(new_nodes)
        for n in new_nodes:
            changes.append({"change_date": today, "node_id": n["node_id"], "edge_id": None,
                            "change_type": "node_added",
                            "detail_json": json.dumps({"code": n["code"], "name": n["name"]})})
        print(f"[KG-UPDATE] 补建节点 {len(new_nodes)} 个")


def _summarize_changes(changes: List[Dict]):
    """打印变更摘要（新进板块的股票 / 被剔除的股票 top 示例）。"""
    added = [c for c in changes if c["change_type"] == "edge_added"]
    removed = [c for c in changes if c["change_type"] == "edge_removed"]
    if added:
        print(f"  新增归属示例：{[json.loads(c['detail_json']) for c in added[:3]]}")
    if removed:
        print(f"  移除归属示例：{[json.loads(c['detail_json']) for c in removed[:3]]}")


def _build_stats(db: Database, sector_codes: List[str],
                 primary: Set, verify: Set, both: Set,
                 primary_only: Set, verify_only: Set) -> Dict:
    """统计：平均归属数 / 孤立板块 / 置信分布 / 枢纽板块 top10。"""
    # 按 (去重后股票,板块) 统计每股归属数
    union_pairs = primary | verify
    per_stock: Dict[str, int] = defaultdict(int)
    per_sector: Dict[str, int] = defaultdict(int)
    for s, c in union_pairs:
        per_stock[s] += 1
        per_sector[c] += 1
    avg_per_stock = (sum(per_stock.values()) / len(per_stock)) if per_stock else 0

    isolated = [c for c in sector_codes if per_sector.get(c, 0) == 0]

    # 枢纽板块（去重后归属股票数 top10）
    hubs = sorted(per_sector.items(), key=lambda kv: -kv[1])[:10]

    both_ratio = len(both) / len(primary) if primary else 0
    return {
        "stock_count": len(per_stock),
        "avg_pairs_per_stock": round(avg_per_stock, 2),
        "isolated_sector_count": len(isolated),
        "isolated_sectors": isolated[:20],
        "confidence_dist": {
            "both": len(both), "primary_only": len(primary_only),
            "verify_only": len(verify_only),
            "both_ratio_vs_primary": round(both_ratio, 4),
        },
        "hub_sectors_top10": [[c, n] for c, n in hubs],
    }


def _print_report(db: Database, stats: Dict, sector_codes: List[str],
                  concept_names: Dict[str, str]):
    print("\n" + "=" * 62)
    print("知识图谱 P1 构建报告")
    print("=" * 62)
    print(f"节点：SECTOR {len(sector_codes)} + STOCK {stats['stock_count']}")
    print(f"边：{stats['edge_count']} 条（双源 {stats['confidence_dist']['both']}"
          f" / 仅主源 {stats['confidence_dist']['primary_only']}"
          f" / 仅验证源 {stats['confidence_dist']['verify_only']}）")
    print(f"平均每股归属板块数：{stats['avg_pairs_per_stock']}（预期 ~13）")
    print(f"孤立板块：{stats['isolated_sector_count']} 个"
          + (f"  例：{stats['isolated_sectors'][:5]}" if stats['isolated_sector_count'] else ""))
    ratio = stats['confidence_dist']['both_ratio_vs_primary']
    print(f"双源交叉验证率（双源/主源）：{ratio:.1%}（设计假设 >60%）")
    print("枢纽板块 Top10（去重后归属股票数）：")
    for c, n in stats['hub_sectors_top10']:
        print(f"  {c} {concept_names.get(c, '?'):<12} {n}")
    print("=" * 62 + "\n")


def _explore_communities(db: Database, concept_names: Dict[str, str]):
    """族群初探：NetworkX Louvain 社区发现（板块投影图视角的解释见设计文档 §9.1）。

    直接在全量二部图上跑 Louvain（股-板块），大社区即"族群"；
    打印 top5 社区的板块构成，人工判断是否符合盘面直觉（半导体系/医药系聚堆）。
    """
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities
    except ImportError:
        print("[KG] networkx 未安装，跳过族群初探（pip install networkx）")
        return

    edges = db.get_kg_open_edges()
    G = nx.Graph()
    for e in edges:
        G.add_edge(e["src_id"], e["dst_id"])
    print(f"[KG] 族群初探：{G.number_of_nodes()} 节点 / {G.number_of_edges()} 边，"
          f"连通分量 {nx.number_connected_components(G)} 个")

    communities = louvain_communities(G, seed=42)
    # 按"板块数"排序看大族（社区里既有股也有板块，板块数代表族群骨架）
    scored = []
    for comm in communities:
        sectors = [nid.split(":", 1)[1] for nid in comm if nid.startswith("SECTOR:")]
        stocks = [nid for nid in comm if nid.startswith("STOCK:")]
        scored.append((len(sectors), len(stocks), sectors))
    scored.sort(reverse=True)

    print(f"[KG] Louvain 社区数：{len(communities)}，Top5 族群：")
    for i, (n_sec, n_stock, sectors) in enumerate(scored[:5], 1):
        names = [concept_names.get(c, c) for c in sectors[:8]]
        more = f" ...等{n_sec}个板块" if n_sec > 8 else ""
        print(f"  #{i} 板块{n_sec} 股票{n_stock}：{'、'.join(names)}{more}")
    print()


def _load_concept_names(db: Database) -> Dict[str, str]:
    import sqlite3
    names = {}
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT concept_code, concept_name FROM ths_concept_dict"):
            names[row["concept_code"]] = row["concept_name"]
    return names
