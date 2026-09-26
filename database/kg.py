# -*- coding: utf-8 -*-
"""知识图谱域。表归属：kg_node / kg_edge / kg_snapshot / kg_change / kg_community。"""

import json
from datetime import datetime
from typing import Dict, List, Optional


class KgraphMixin:
    """KG 5 表读写（构建 P1 / 周维护 P2 / 族群与 ρ 边权 P3）"""

    def clear_kg_tables(self):
        """清空全部 KG 表（kg_init --force 重建用）。"""
        with self._connect() as conn:
            for t in ("kg_change", "kg_snapshot", "kg_edge", "kg_node"):
                conn.execute(f"DELETE FROM {t}")

    def has_kg_snapshot(self) -> bool:
        """是否已存在图谱快照（kg_init 幂等门）。"""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM kg_snapshot").fetchone()
            return row[0] > 0

    def save_kg_nodes(self, rows: List[Dict]):
        """批量写入节点。rows: [{node_id, node_type, code, name, sector_type, props_json, first_seen, last_seen}]"""
        with self._connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO kg_node
                   (node_id, node_type, code, name, sector_type, props_json, first_seen, last_seen, is_active)
                   VALUES (:node_id, :node_type, :code, :name, :sector_type, :props_json, :first_seen, :last_seen, 1)""",
                rows,
            )

    def save_kg_edges(self, rows: List[Dict]):
        """批量写入边。rows: [{src_id, dst_id, edge_type, source, valid_from, valid_to, props_json, confidence}]"""
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO kg_edge
                   (src_id, dst_id, edge_type, source, valid_from, valid_to, props_json, confidence)
                   VALUES (:src_id, :dst_id, :edge_type, :source, :valid_from, :valid_to, :props_json, :confidence)""",
                rows,
            )

    def get_kg_nodes(self, node_type: Optional[str] = None) -> List[Dict]:
        """读取节点（可选按类型过滤）。"""
        with self._connect() as conn:
            if node_type:
                cursor = conn.execute(
                    "SELECT * FROM kg_node WHERE node_type = ? AND is_active = 1", (node_type,))
            else:
                cursor = conn.execute("SELECT * FROM kg_node WHERE is_active = 1")
            return [dict(r) for r in cursor.fetchall()]

    def get_kg_open_edges(self) -> List[Dict]:
        """读取全部当前生效边（valid_to IS NULL）。"""
        with self._connect() as conn:
            cursor = conn.execute("SELECT * FROM kg_edge WHERE valid_to IS NULL")
            return [dict(r) for r in cursor.fetchall()]

    def close_kg_edges(self, edge_ids: List[int], valid_to: str):
        """批量关闭边（kg_update diff 用）：valid_to 置为指定日期。"""
        if not edge_ids:
            return
        with self._connect() as conn:
            conn.executemany(
                "UPDATE kg_edge SET valid_to = ? WHERE edge_id = ?",
                [(valid_to, eid) for eid in edge_ids],
            )

    def touch_kg_nodes(self, node_ids: List[str], last_seen: str):
        """批量刷新节点 last_seen（kg_update 确认节点仍存在）。"""
        if not node_ids:
            return
        with self._connect() as conn:
            conn.executemany(
                "UPDATE kg_node SET last_seen = ? WHERE node_id = ?",
                [(last_seen, nid) for nid in node_ids],
            )

    def deactivate_stale_kg_nodes(self, before_date: str) -> List[str]:
        """
        将 last_seen 早于 before_date 且仍 active 的节点置为 is_active=0（不物理删）。
        :return: 被停用的 node_id 列表（供 kg_change 记录）
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT node_id FROM kg_node WHERE is_active = 1 AND last_seen < ?",
                (before_date,)).fetchall()
            ids = [r["node_id"] for r in rows]
            if ids:
                conn.executemany(
                    "UPDATE kg_node SET is_active = 0 WHERE node_id = ?",
                    [(i,) for i in ids])
            return ids

    def save_kg_changes(self, rows: List[Dict]):
        """批量写入变更日志。rows: [{change_date, node_id, edge_id, change_type, detail_json}]"""
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO kg_change
                   (change_date, node_id, edge_id, change_type, detail_json)
                   VALUES (:change_date, :node_id, :edge_id, :change_type, :detail_json)""",
                rows,
            )

    # ========== 知识图谱 P3：族群 / ρ 边权 ==========
    def replace_kg_communities(self, calc_date: str, rows: List[Dict]):
        """覆盖写入当日族群（kg_corr 后重算用）。rows: [{community_id, node_id, node_type}]"""
        with self._connect() as conn:
            conn.execute("DELETE FROM kg_community WHERE calc_date = ?", (calc_date,))
            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO kg_community (calc_date, community_id, node_id, node_type) "
                    "VALUES (:calc_date, :community_id, :node_id, :node_type)",
                    [{"calc_date": calc_date, **r} for r in rows],
                )

    def get_latest_kg_communities(self) -> List[Dict]:
        """读取最近一次族群结果：[{community_id, node_id, node_type}]"""
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(calc_date) FROM kg_community").fetchone()
            if not row or not row[0]:
                return []
            cursor = conn.execute(
                "SELECT community_id, node_id, node_type FROM kg_community "
                "WHERE calc_date = ? ORDER BY community_id", (row[0],))
            return [dict(r) for r in cursor.fetchall()]

    def update_kg_edge_corr(self, pairs: List[Dict]):
        """批量更新 open 边的 corr_20d。pairs: [{edge_id, corr_20d}]"""
        if not pairs:
            return
        with self._connect() as conn:
            conn.executemany(
                "UPDATE kg_edge SET corr_20d = ? WHERE edge_id = ? AND valid_to IS NULL",
                [(p["corr_20d"], p["edge_id"]) for p in pairs],
            )

    def get_kg_edges_for_stock(self, stock_code: str) -> List[Dict]:
        """读取个股全部生效边（含板块信息与 corr）。"""
        with self._connect() as conn:
            cursor = conn.execute(
                """SELECT e.edge_id, e.confidence, e.corr_20d, e.valid_from,
                          s.code AS sector_code, s.name AS sector_name, s.sector_type
                   FROM kg_edge e
                   JOIN kg_node s ON e.dst_id = s.node_id
                   WHERE e.src_id = ? AND e.valid_to IS NULL""",
                (f"STOCK:{stock_code}",))
            return [dict(r) for r in cursor.fetchall()]

    def get_kg_edges_for_sector(self, sector_code: str) -> List[Dict]:
        """读取板块全部生效边（含股票信息与 corr）。"""
        with self._connect() as conn:
            cursor = conn.execute(
                """SELECT e.edge_id, e.confidence, e.corr_20d, e.valid_from,
                          t.code AS stock_code, t.name AS stock_name
                   FROM kg_edge e
                   JOIN kg_node t ON e.src_id = t.node_id
                   WHERE e.dst_id = ? AND e.valid_to IS NULL""",
                (f"SECTOR:{sector_code}",))
            return [dict(r) for r in cursor.fetchall()]

    def save_kg_snapshot(self, snapshot_id: str, stats: Dict, sources: List[str]):
        """写入图谱快照（含统计 json）。"""
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO kg_snapshot
                   (snapshot_id, built_at, node_count, edge_count, added_edges, removed_edges, sources, stats_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    datetime.now().isoformat(timespec="seconds"),
                    stats.get("node_count", 0),
                    stats.get("edge_count", 0),
                    stats.get("added_edges", 0),
                    stats.get("removed_edges", 0),
                    json.dumps(sources, ensure_ascii=False),
                    json.dumps(stats, ensure_ascii=False),
                ),
            )
