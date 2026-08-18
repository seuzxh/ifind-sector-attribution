# -*- coding: utf-8 -*-
"""
SQLite 数据库封装
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Any
from contextlib import contextmanager

import config


class Database:
    """SQLite 数据库操作类"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DB_PATH
        # 确保数据库所在目录存在（DB_PATH 默认在 data/ 下，仓库不包含该目录）
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        """上下文管理器管理连接"""
        timeout_ms = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 5000))
        conn = sqlite3.connect(self.db_path, timeout=timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """初始化数据库表结构"""
        ddl = """
        -- 同花顺概念板块字典
        CREATE TABLE IF NOT EXISTS ths_concept_dict (
            concept_code  TEXT PRIMARY KEY,
            concept_name  TEXT NOT NULL,
            full_name     TEXT,
            index_code    TEXT,
            main_code     TEXT,
            thscode       TEXT,
            update_date   TEXT
        );

        -- 个股-概念多对多映射（永久缓存）
        CREATE TABLE IF NOT EXISTS stock_concept_map (
            stock_code    TEXT NOT NULL,
            concept_code  TEXT NOT NULL,
            map_date      TEXT NOT NULL,
            weight        REAL DEFAULT 1.0,
            PRIMARY KEY (stock_code, concept_code, map_date)
        );
        CREATE INDEX IF NOT EXISTS idx_scm_concept ON stock_concept_map(concept_code);

        -- 概念板块成分股（永久缓存）
        CREATE TABLE IF NOT EXISTS concept_members (
            concept_code  TEXT NOT NULL,
            stock_code    TEXT NOT NULL,
            stock_name    TEXT,
            member_date   TEXT NOT NULL,
            PRIMARY KEY (concept_code, stock_code, member_date)
        );
        CREATE INDEX IF NOT EXISTS idx_cm_concept ON concept_members(concept_code);

        -- 日K线行情（个股 + 概念指数）
        CREATE TABLE IF NOT EXISTS daily_kline (
            code          TEXT NOT NULL,
            trade_date    TEXT NOT NULL,
            pre_close     REAL,
            open          REAL,
            high          REAL,
            low           REAL,
            close         REAL,
            change_ratio  REAL,
            volume        REAL,
            amount        REAL,
            PRIMARY KEY (code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_dk_date ON daily_kline(trade_date);

        -- 1min K线行情（盘中用，仅保留最近2个交易日）
        CREATE TABLE IF NOT EXISTS min1_kline (
            code          TEXT NOT NULL,
            trade_time    TEXT NOT NULL,
            open          REAL,
            high          REAL,
            low           REAL,
            close          REAL,
            change_ratio  REAL,
            volume        REAL,
            amount        REAL,
            PRIMARY KEY (code, trade_time)
        );
        CREATE INDEX IF NOT EXISTS idx_m1_code_time ON min1_kline(code, trade_time);

        -- 概念板块强度评分（每日结果）
        CREATE TABLE IF NOT EXISTS concept_strength (
            calc_date      TEXT NOT NULL,
            concept_code   TEXT NOT NULL,
            s1_return      REAL,
            s2_breadth     REAL,
            s4_relative    REAL,
            score_1d       REAL,
            score_5d       REAL,
            score_20d      REAL,
            score_final    REAL,
            rank_1d        INTEGER,
            coherency      REAL,
            PRIMARY KEY (calc_date, concept_code)
        );
        CREATE INDEX IF NOT EXISTS idx_cs_date ON concept_strength(calc_date);

        -- 个股归因结果（每日）
        CREATE TABLE IF NOT EXISTS stock_attribution (
            stock_code     TEXT NOT NULL,
            calc_date      TEXT NOT NULL,
            total_return   REAL,
            top_concept    TEXT,
            top_contrib_pct REAL,
            attribution_json TEXT,
            PRIMARY KEY (stock_code, calc_date)
        );

        -- 自选股分组（同花顺 custom_block 导入，静态手动分组）
        CREATE TABLE IF NOT EXISTS custom_group (
            group_id    TEXT NOT NULL,
            group_name  TEXT NOT NULL,
            stock_code  TEXT NOT NULL,
            PRIMARY KEY (group_id, stock_code)
        );
        CREATE INDEX IF NOT EXISTS idx_cg_group ON custom_group(group_id);

        -- 监控板块持久化选择（读取时还需应用成员数资格规则）
        CREATE TABLE IF NOT EXISTS watched_concepts (
            concept_code  TEXT PRIMARY KEY,
            added_at      TEXT NOT NULL
        );

        -- ========== 知识图谱（KG）：节点 / 双时态边 / 快照 / 变更日志 ==========
        -- 节点统一建模：股票 + 板块。node_id 形如 'STOCK:600519.SH' / 'SECTOR:884091.TI'
        CREATE TABLE IF NOT EXISTS kg_node (
            node_id     TEXT PRIMARY KEY,
            node_type   TEXT NOT NULL,           -- 'stock' | 'sector'
            code        TEXT NOT NULL,           -- 原始代码（600519.SH）
            name        TEXT,                    -- 股票名/板块名
            sector_type TEXT,                    -- 板块专用: 'industry'(884) / 'concept'(885/886)；股票为 NULL
            props_json  TEXT,                    -- 扩展属性（member_count、monitorable 等）
            first_seen  TEXT NOT NULL,           -- 首次进入图谱日期
            last_seen   TEXT NOT NULL,           -- 最近一次确认存在的日期
            is_active   INTEGER DEFAULT 1        -- 长期未被确认置 0，不物理删
        );
        CREATE INDEX IF NOT EXISTS idx_kg_node_type ON kg_node(node_type, is_active);
        CREATE INDEX IF NOT EXISTS idx_kg_node_code ON kg_node(code);

        -- 双时态归属边：valid_to IS NULL 表示当前生效。
        -- 同一对(股,板块)可有多个 source 的生效边并存（多源交叉验证）。
        CREATE TABLE IF NOT EXISTS kg_edge (
            edge_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            src_id      TEXT NOT NULL,           -- 股票节点 'STOCK:xxx'
            dst_id      TEXT NOT NULL,           -- 板块节点 'SECTOR:xxx'
            edge_type   TEXT NOT NULL DEFAULT 'BELONGS_TO',
            source      TEXT NOT NULL,           -- 'ifind_p03473' / 'ifind_concept' / 未来其他源
            valid_from  TEXT NOT NULL,           -- 关系生效日（快照日期）
            valid_to    TEXT,                    -- NULL=生效中；非空=已失效日
            props_json  TEXT,                    -- 边动态属性（corr_20d 等）
            confidence  REAL DEFAULT 1.0         -- 双源 1.0 / 仅主源 0.8 / 仅验证源 0.6
        );
        CREATE INDEX IF NOT EXISTS idx_kg_edge_src ON kg_edge(src_id, valid_to);
        CREATE INDEX IF NOT EXISTS idx_kg_edge_dst ON kg_edge(dst_id, valid_to);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_edge_open
            ON kg_edge(src_id, dst_id, source) WHERE valid_to IS NULL;

        -- 图谱版本快照（每周 kg_update 产出；kg_init 产出首份）
        CREATE TABLE IF NOT EXISTS kg_snapshot (
            snapshot_id  TEXT PRIMARY KEY,       -- '20260714'
            built_at     TEXT NOT NULL,
            node_count   INTEGER,
            edge_count   INTEGER,
            added_edges  INTEGER,
            removed_edges INTEGER,
            sources      TEXT,                   -- 参与源列表 json
            stats_json   TEXT                    -- 度分布/置信分布/族群等统计
        );

        -- 变更日志（P2 周维护 diff 产出；P1 仅建表）
        CREATE TABLE IF NOT EXISTS kg_change (
            change_date TEXT NOT NULL,
            node_id     TEXT NOT NULL,
            edge_id     INTEGER,
            change_type TEXT NOT NULL,           -- 'edge_added' / 'edge_removed' / ...
            detail_json TEXT,
            PRIMARY KEY (change_date, node_id, edge_id)
        );

        -- 板块族群（P3：Louvain 社区发现结果，喂轮动分析/图谱投影图着色）
        CREATE TABLE IF NOT EXISTS kg_community (
            calc_date    TEXT NOT NULL,
            community_id INTEGER NOT NULL,       -- 族群编号（按规模重排，1=最大）
            node_id      TEXT NOT NULL,          -- 成员节点（板块为主，含股票）
            node_type    TEXT NOT NULL,
            PRIMARY KEY (calc_date, node_id)
        );
        CREATE INDEX IF NOT EXISTS idx_kg_comm_date ON kg_community(calc_date, community_id);
        """
        with self._connect() as conn:
            # 读多写少的看板服务使用 WAL：读请求不再被 daily 写事务阻塞。
            # synchronous=NORMAL 是连接级配置，已在 _connect 中为每个连接设置。
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(ddl)
            # P3 迁移：kg_edge 加 corr_20d 实体列（20日滚动相关系数，NULL=未算）。
            # 实体列而非 props_json：联动股查询需按 corr 排序/过滤，实体列可走索引。
            cols = {r[1] for r in conn.execute("PRAGMA table_info(kg_edge)")}
            if "corr_20d" not in cols:
                conn.execute("ALTER TABLE kg_edge ADD COLUMN corr_20d REAL")
            # watched_concepts 首次建表时若为空，灌入 config.SECTOR_POOL_CODES 作种子，
            # 保证上线即有默认监控集（884×259），行为与改造前一致。
            cnt = conn.execute("SELECT COUNT(*) FROM watched_concepts").fetchone()[0]
            if cnt == 0:
                pool = list(getattr(config, "SECTOR_POOL_CODES", set()))
                if pool:
                    now = datetime.now().isoformat(timespec="seconds")
                    conn.executemany(
                        "INSERT OR IGNORE INTO watched_concepts (concept_code, added_at) VALUES (?, ?)",
                        [(c, now) for c in pool],
                    )
                    print(f"[DB] watched_concepts 初始化种子 {len(pool)} 个板块")

    # ========== 概念字典操作 ==========
    def save_concept_dict(self, concepts: List[Dict[str, str]], update_date: str = None):
        """保存概念板块字典"""
        update_date = update_date or datetime.now().strftime("%Y-%m-%d")
        with self._connect() as conn:
            for c in concepts:
                conn.execute("""
                    INSERT OR REPLACE INTO ths_concept_dict
                    (concept_code, concept_name, full_name, index_code, main_code, thscode, update_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    c.get("concept_code"),
                    c.get("short_name", ""),
                    c.get("full_name", ""),
                    c.get("index_code", ""),
                    c.get("main_code", ""),
                    c.get("thscode", ""),
                    update_date
                ))

    def get_all_concept_codes(self) -> List[str]:
        """获取所有概念代码"""
        with self._connect() as conn:
            cursor = conn.execute("SELECT concept_code FROM ths_concept_dict")
            return [row["concept_code"] for row in cursor.fetchall()]

    def get_a_share_concept_codes(self) -> List[str]:
        """
        获取参与 daily 归因的概念代码。
        优先读 watched_concepts 的有效范围（持久化选择 + 成员数资格规则）；
        表空时退回 config 板块池兜底（避免 daily 漏算）。
        """
        watched = self.get_watched_concept_codes()
        if watched:
            return watched
        # 兜底：watched 表空（未配置）→ 用 config 原逻辑
        with self._connect() as conn:
            cursor = conn.execute("SELECT concept_code FROM ths_concept_dict")
            return [
                row["concept_code"] for row in cursor.fetchall()
                if config.is_a_share_concept(row["concept_code"])
                and config.is_in_sector_pool(row["concept_code"])
            ]

    def get_observe_concept_codes(self) -> List[str]:
        """
        获取观察池概念代码全集（884 三级行业 + 885/886 概念板块）。
        按前缀白名单过滤（排除海外），但【不过滤板块池】——这是与 get_a_share_concept_codes 的关键区别。
        用于看板展示（realtime_engine），不参与 daily 归因。
        """
        with self._connect() as conn:
            cursor = conn.execute("SELECT concept_code FROM ths_concept_dict")
            return [
                row["concept_code"] for row in cursor.fetchall()
                if config.is_a_share_concept(row["concept_code"])
                and config.is_in_observe_pool(row["concept_code"])
            ]

    # ========== 监控板块勾选（watched_concepts） ==========
    def get_watched_concept_codes(self) -> List[str]:
        """
        读取监控板块勾选清单（watched_concepts 表）。
        读取持久化选择并应用成员数资格规则，联动看板、scan 和 daily 归因。
        仅返回最新成分股数在监控范围内的板块；表空返回空列表。
        """
        with self._connect() as conn:
            cursor = conn.execute("SELECT concept_code FROM watched_concepts ORDER BY concept_code")
            codes = [row["concept_code"] for row in cursor.fetchall()]
        return self.filter_monitorable_concept_codes(codes)

    def filter_monitorable_concept_codes(self, codes: List[str]) -> List[str]:
        """按最新成分股快照过滤监控板块，保留配置上下限（含边界）内的代码。"""
        unique_codes = list(dict.fromkeys(codes))
        members_map = self.get_concept_members_map(unique_codes)
        return [
            code for code in unique_codes
            if config.is_monitorable_member_count(len(members_map.get(code, [])))
        ]

    def save_watched_concepts(self, codes: List[str]) -> List[str]:
        """
        全量覆盖监控板块勾选清单（事务内 DELETE ALL + 批量 INSERT）。
        :param codes: 勾选的概念代码列表（全量，未在列表中的会被移除）
        :return: 实际保存的有效代码（自动剔除成分股数量越界或无快照的板块）
        """
        eligible_codes = self.filter_monitorable_concept_codes(codes)
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute("DELETE FROM watched_concepts")
            if eligible_codes:
                conn.executemany(
                    "INSERT OR IGNORE INTO watched_concepts (concept_code, added_at) VALUES (?, ?)",
                    [(c, now) for c in eligible_codes],
                )
        return eligible_codes

    # ========== 知识图谱（kg_node / kg_edge / kg_snapshot） ==========
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

    def get_all_member_stock_codes(self) -> List[str]:
        """
        从成分股表反查全部 A 股股票代码（全市场股票池）。
        成分股表覆盖主板/创业板/科创板/北交所，作为 daily 同步 K 线的默认代码来源。
        取最新一份快照，避免历史重复。只返回 A 股（沪深北），过滤海外代码。
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT stock_code FROM concept_members
                WHERE member_date = (SELECT MAX(member_date) FROM concept_members)
                  AND (stock_code LIKE '%.SH' OR stock_code LIKE '%.SZ' OR stock_code LIKE '%.BJ')
            """)
            return [row["stock_code"] for row in cursor.fetchall()]

    def get_all_mapped_stock_codes(self) -> List[str]:
        """
        获取 stock_concept_map 中有概念映射的 A 股独立股票代码（取最新快照）。
        用于归因计算，避免对全市场无映射股票空查。只返回 A 股（沪深北）。
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT stock_code FROM stock_concept_map
                WHERE map_date = (SELECT MAX(map_date) FROM stock_concept_map)
                  AND (stock_code LIKE '%.SH' OR stock_code LIKE '%.SZ' OR stock_code LIKE '%.BJ')
            """)
            return [row["stock_code"] for row in cursor.fetchall()]

    def get_concept_name(self, concept_code: str) -> str:
        """获取概念名称"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT concept_name FROM ths_concept_dict WHERE concept_code = ?",
                (concept_code,)
            )
            row = cursor.fetchone()
            return row["concept_name"] if row else concept_code

    # ========== 个股-概念映射操作 ==========
    def save_stock_concept_map(self, mappings: Dict[str, List[Dict[str, str]]], map_date: str):
        """
        保存个股-概念映射
        :param mappings: {stock_code: [{concept_name, concept_code}, ...]}
        :param map_date: 映射日期
        """
        with self._connect() as conn:
            for stock_code, concepts in mappings.items():
                # 等权分配
                weight = 1.0 / len(concepts) if concepts else 1.0
                for concept in concepts:
                    conn.execute("""
                        INSERT OR REPLACE INTO stock_concept_map
                        (stock_code, concept_code, map_date, weight)
                        VALUES (?, ?, ?, ?)
                    """, (stock_code, concept["concept_code"], map_date, weight))

    def get_stock_concepts(self, stock_code: str, map_date: str = None) -> List[Dict]:
        """
        获取某个股的概念映射
        :param map_date: 映射快照日期；不传则取最新一份（永久缓存语义）
        """
        with self._connect() as conn:
            if map_date is None:
                cursor = conn.execute("""
                    SELECT scm.concept_code, tcd.concept_name, scm.weight
                    FROM stock_concept_map scm
                    JOIN ths_concept_dict tcd ON scm.concept_code = tcd.concept_code
                    WHERE scm.stock_code = ? AND scm.map_date = (
                        SELECT MAX(map_date) FROM stock_concept_map WHERE stock_code = ?
                    )
                """, (stock_code, stock_code))
            else:
                cursor = conn.execute("""
                    SELECT scm.concept_code, tcd.concept_name, scm.weight
                    FROM stock_concept_map scm
                    JOIN ths_concept_dict tcd ON scm.concept_code = tcd.concept_code
                    WHERE scm.stock_code = ? AND scm.map_date = ?
                """, (stock_code, map_date))
            return [
                {"concept_code": row["concept_code"], "concept_name": row["concept_name"], "weight": row["weight"]}
                for row in cursor.fetchall()
            ]

    def get_stock_concepts_from_members(self, stock_code: str) -> List[Dict]:
        """
        从 concept_members 反推个股所属板块（供 884 板块池归因用）。

        背景：884 是行业分类码，个股从不被 API 打上 884 标签
        （stock_concept_map 无 884），但 884 在 concept_members 有成分股数据。
        故通过"个股出现在哪些 884 板块的成分股列表里"反推归属。
        结果应用板块池过滤（is_in_sector_pool）。
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT cm.concept_code, tcd.concept_name
                FROM concept_members cm
                JOIN ths_concept_dict tcd ON cm.concept_code = tcd.concept_code
                WHERE cm.stock_code = ?
                  AND cm.member_date = (
                      SELECT MAX(member_date) FROM concept_members WHERE stock_code = ?
                  )
            """, (stock_code, stock_code))
            return [
                {"concept_code": row["concept_code"],
                 "concept_name": row["concept_name"],
                 "weight": 1.0}
                for row in cursor.fetchall()
                if config.is_in_sector_pool(row["concept_code"])
            ]

    def get_concept_stocks(self, concept_code: str, map_date: str = None) -> List[str]:
        """
        获取某概念包含的所有个股
        :param map_date: 映射快照日期；不传则取最新一份（永久缓存语义）
        """
        with self._connect() as conn:
            if map_date is None:
                cursor = conn.execute("""
                    SELECT stock_code FROM stock_concept_map
                    WHERE concept_code = ? AND map_date = (
                        SELECT MAX(map_date) FROM stock_concept_map WHERE concept_code = ?
                    )
                """, (concept_code, concept_code))
            else:
                cursor = conn.execute("""
                    SELECT stock_code FROM stock_concept_map
                    WHERE concept_code = ? AND map_date = ?
                """, (concept_code, map_date))
            return [row["stock_code"] for row in cursor.fetchall()]

    # ========== 概念成分股操作 ==========
    def save_concept_members(self, concept_code: str, members: List[Dict], member_date: str):
        """保存概念板块成分股"""
        with self._connect() as conn:
            for m in members:
                conn.execute("""
                    INSERT OR REPLACE INTO concept_members
                    (concept_code, stock_code, stock_name, member_date)
                    VALUES (?, ?, ?, ?)
                """, (concept_code, m.get("stock_code"), m.get("stock_name", ""), member_date))

    def get_concept_members(self, concept_code: str, member_date: str = None) -> List[Dict]:
        """
        获取概念板块成分股列表
        :param member_date: 成分股快照日期；不传则取最新一份（永久缓存语义）
        """
        with self._connect() as conn:
            if member_date is None:
                cursor = conn.execute("""
                    SELECT stock_code, stock_name FROM concept_members
                    WHERE concept_code = ? AND member_date = (
                        SELECT MAX(member_date) FROM concept_members WHERE concept_code = ?
                    )
                """, (concept_code, concept_code))
            else:
                cursor = conn.execute("""
                    SELECT stock_code, stock_name FROM concept_members
                    WHERE concept_code = ? AND member_date = ?
                """, (concept_code, member_date))
            return [
                {"stock_code": row["stock_code"], "stock_name": row["stock_name"]}
                for row in cursor.fetchall()
            ]

    def get_concept_members_map(self, concept_codes: List[str]) -> Dict[str, List[Dict]]:
        """批量读取各概念最新成分股快照，避免逐概念建立 SQLite 连接。"""
        if not concept_codes:
            return {}
        placeholders = ",".join("?" for _ in concept_codes)
        sql = f"""
            WITH latest AS (
                SELECT concept_code, MAX(member_date) AS member_date
                FROM concept_members
                WHERE concept_code IN ({placeholders})
                GROUP BY concept_code
            )
            SELECT cm.concept_code, cm.stock_code, cm.stock_name
            FROM concept_members cm
            JOIN latest l
              ON cm.concept_code = l.concept_code
             AND cm.member_date = l.member_date
            ORDER BY cm.concept_code, cm.stock_code
        """
        result: Dict[str, List[Dict]] = {}
        with self._connect() as conn:
            for row in conn.execute(sql, concept_codes):
                result.setdefault(row["concept_code"], []).append({
                    "stock_code": row["stock_code"],
                    "stock_name": row["stock_name"],
                })
        return result

    # ========== 日K线操作 ==========
    def save_daily_kline(self, records: List[Dict]):
        """保存日K线数据"""
        with self._connect() as conn:
            for r in records:
                conn.execute("""
                    INSERT OR REPLACE INTO daily_kline
                    (code, trade_date, pre_close, open, high, low, close, change_ratio, volume, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    r["code"], r["trade_date"], r.get("pre_close"),
                    r.get("open"), r.get("high"), r.get("low"),
                    r.get("close"), r.get("change_ratio"),
                    r.get("volume"), r.get("amount")
                ))

    def get_daily_kline(self, code: str, start_date: str, end_date: str) -> List[Dict]:
        """获取某代码的日K线数据"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT * FROM daily_kline
                WHERE code = ? AND trade_date >= ? AND trade_date <= ?
                ORDER BY trade_date
            """, (code, start_date, end_date))
            return [dict(row) for row in cursor.fetchall()]

    def get_daily_kline_by_date(self, trade_date: str) -> List[Dict]:
        """获取某交易日的全部日K线"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT * FROM daily_kline WHERE trade_date = ?
            """, (trade_date,))
            return [dict(row) for row in cursor.fetchall()]

    def get_latest_trade_date(self, on_or_before: str = None) -> Optional[str]:
        """
        获取 daily_kline 中已入库的最新交易日（YYYYMMDD）。
        :param on_or_before: 若给定，返回 <= 该日期的最新交易日（用于盘前回退定位）。
                             日期格式 YYYYMMDD；None 时返回全局最新。
        :return: YYYYMMDD 字符串，无数据返回 None。
        """
        with self._connect() as conn:
            if on_or_before:
                row = conn.execute(
                    "SELECT MAX(trade_date) FROM daily_kline WHERE trade_date <= ?",
                    (on_or_before,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT MAX(trade_date) FROM daily_kline"
                ).fetchone()
            return row[0] if row else None

    def get_daily_kline_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        """
        获取某日期区间内全部代码的日K线（用于多周期累计涨幅计算）。
        日期格式不限（YYYYMMDD 或 YYYY-MM-DD 均可，按字符串比较）。
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT * FROM daily_kline
                WHERE trade_date >= ? AND trade_date <= ?
                ORDER BY code, trade_date
            """, (start_date, end_date))
            return [dict(row) for row in cursor.fetchall()]

    # ========== 1min K线操作 ==========
    def save_min1_kline(self, records: List[Dict]):
        """保存1min K线数据"""
        with self._connect() as conn:
            for r in records:
                conn.execute("""
                    INSERT OR REPLACE INTO min1_kline
                    (code, trade_time, open, high, low, close, change_ratio, volume, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    r["code"], r["trade_time"], r.get("open"),
                    r.get("high"), r.get("low"), r.get("close"),
                    r.get("change_ratio"), r.get("volume"), r.get("amount")
                ))

    def get_min1_kline(self, code: str, start_time: str, end_time: str) -> List[Dict]:
        """获取某代码的1min K线"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT * FROM min1_kline
                WHERE code = ? AND trade_time >= ? AND trade_time <= ?
                ORDER BY trade_time
            """, (code, start_time, end_time))
            return [dict(row) for row in cursor.fetchall()]

    def clean_old_min1_data(self, keep_days: int = 2):
        """清理过期的1min K线数据"""
        cutoff = datetime.now().strftime("%Y-%m-%d")
        with self._connect() as conn:
            conn.execute("DELETE FROM min1_kline WHERE trade_time < ?", (cutoff,))

    # ========== 板块强度操作 ==========
    def save_concept_strength(self, records: List[Dict]):
        """全量保存某日板块强度评分，先清理该日旧范围，避免残留板块混入。"""
        if not records:
            return
        calc_dates = sorted({r["calc_date"] for r in records})
        with self._connect() as conn:
            conn.executemany(
                "DELETE FROM concept_strength WHERE calc_date = ?",
                [(d,) for d in calc_dates],
            )
            conn.executemany("""
                    INSERT OR REPLACE INTO concept_strength
                    (calc_date, concept_code, s1_return, s2_breadth, s4_relative,
                     score_1d, score_5d, score_20d, score_final, rank_1d, coherency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [(
                    r["calc_date"], r["concept_code"], r.get("s1_return"),
                    r.get("s2_breadth"), r.get("s4_relative"),
                    r.get("score_1d"), r.get("score_5d"), r.get("score_20d"),
                    r.get("score_final"), r.get("rank_1d"), r.get("coherency")
                ) for r in records])

    def get_sector_rankings(self, calc_date: str, top_n: int = None) -> List[Dict]:
        """获取某日的板块强度排名"""
        with self._connect() as conn:
            sql = """
                SELECT cs.*, tcd.concept_name
                FROM concept_strength cs
                JOIN ths_concept_dict tcd ON cs.concept_code = tcd.concept_code
                WHERE cs.calc_date = ?
                ORDER BY cs.rank_1d
            """
            if top_n:
                sql += f" LIMIT {top_n}"
            cursor = conn.execute(sql, (calc_date,))
            return [dict(row) for row in cursor.fetchall()]

    # ========== 个股归因操作 ==========
    def save_stock_attribution(self, records: List[Dict]):
        """保存个股归因结果"""
        with self._connect() as conn:
            for r in records:
                conn.execute("""
                    INSERT OR REPLACE INTO stock_attribution
                    (stock_code, calc_date, total_return, top_concept, top_contrib_pct, attribution_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    r["stock_code"], r["calc_date"], r.get("total_return"),
                    r.get("top_concept"), r.get("top_contrib_pct"),
                    json.dumps(r.get("attributions", []), ensure_ascii=False)
                ))

    def get_stock_attribution(self, stock_code: str, calc_date: str) -> Optional[Dict]:
        """获取个股归因结果"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT * FROM stock_attribution
                WHERE stock_code = ? AND calc_date = ?
            """, (stock_code, calc_date))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                result["attributions"] = json.loads(result.get("attribution_json", "[]"))
                return result
            return None

    # ========== 维护：海外数据清理 ==========
    def purge_overseas_data(self) -> Dict[str, int]:
        """
        删除所有海外数据（非 A股），返回各表删除行数。
        - 概念维度：删除前缀不在 A_SHARE_CONCEPT_PREFIXES 的概念
        - 个股维度：删除代码后缀非 .SH/.SZ/.BJ 的个股
        在单个事务中执行，失败回滚。
        """
        a_prefs = ",".join(f"'{p}'" for p in config.A_SHARE_CONCEPT_PREFIXES)
        code_filter = "code NOT LIKE '%.SH' AND code NOT LIKE '%.SZ' AND code NOT LIKE '%.BJ'"
        stock_filter = "stock_code NOT LIKE '%.SH' AND stock_code NOT LIKE '%.SZ' AND stock_code NOT LIKE '%.BJ'"

        with self._connect() as conn:
            try:
                conn.execute("BEGIN")
                deleted = {}
                # 概念维度（按前缀）
                for t in ["ths_concept_dict", "concept_members", "concept_strength"]:
                    deleted[t] = conn.execute(
                        f"DELETE FROM {t} WHERE substr(concept_code,1,3) NOT IN ({a_prefs})"
                    ).rowcount
                # 个股维度（按后缀）
                deleted["stock_concept_map"] = conn.execute(
                    f"DELETE FROM stock_concept_map WHERE {stock_filter}"
                ).rowcount
                deleted["daily_kline"] = conn.execute(
                    f"DELETE FROM daily_kline WHERE {code_filter}"
                ).rowcount
                deleted["stock_attribution"] = conn.execute(
                    f"DELETE FROM stock_attribution WHERE {stock_filter}"
                ).rowcount
                conn.commit()
                return deleted
            except Exception:
                conn.rollback()
                raise

    def get_new_stock_codes(self, calc_date: str, min_days: int = 5) -> set:
        """
        返回上市不足 min_days 个交易日的股票代码集合（新股）。
        判定：该股票在 daily_kline 的最早出现日期，距 calc_date 不足 min_days 个交易日。

        :param calc_date: 基准日期 YYYYMMDD
        :param min_days: 最小上市交易日数，默认 5
        :return: set of stock_code
        """
        with self._connect() as conn:
            # 取 calc_date 及之前的交易日列表（升序）
            trade_dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT trade_date FROM daily_kline "
                "WHERE trade_date <= ? ORDER BY trade_date", (calc_date,)
            ).fetchall()]
            if len(trade_dates) < min_days:
                return set()  # 历史不足，无法判定，不过滤
            # cutoff：第 (len - min_days) 个交易日（含），早于此日首现才算老股
            cutoff_idx = len(trade_dates) - min_days
            cutoff = trade_dates[cutoff_idx]
            # 首现日期 > cutoff 的股票 = 上市不足 min_days 天
            rows = conn.execute(
                "SELECT code, MIN(trade_date) first_date FROM daily_kline "
                "GROUP BY code HAVING first_date > ?", (cutoff,)
            ).fetchall()
            return {r[0] for r in rows}

    # ========== 自选股分组（custom_group） ==========
    def save_custom_groups(self, rows: List[Dict]):
        """
        覆盖写入自选股分组（先清表再批量插入）。幂等，可重复导入更新。

        :param rows: [{group_id, group_name, stock_code}, ...]
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM custom_group")
            if rows:
                conn.executemany("""
                    INSERT OR REPLACE INTO custom_group
                    (group_id, group_name, stock_code)
                    VALUES (?, ?, ?)
                """, [(r["group_id"], r["group_name"], r["stock_code"]) for r in rows])

    def get_custom_members_map(self) -> Dict[str, List[str]]:
        """返回 {group_id: [stock_code, ...]}，供板块强度计算用"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT group_id, stock_code FROM custom_group ORDER BY group_id, stock_code"
            )
            m: Dict[str, List[str]] = {}
            for row in cursor:
                m.setdefault(row["group_id"], []).append(row["stock_code"])
            return m

    def get_custom_group_names(self) -> Dict[str, str]:
        """返回 {group_id: group_name}，供展示用"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT group_id, group_name FROM custom_group "
                "GROUP BY group_id, group_name"
            )
            return {row["group_id"]: row["group_name"] for row in cursor}

    def get_custom_group_codes_by_name(self, name: str) -> List[str]:
        """按分组名取成分股代码（group_name 容忍首尾空格，同花顺导出名常带尾随空格）。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT DISTINCT stock_code FROM custom_group WHERE TRIM(group_name) = TRIM(?)",
                (name,),
            )
            return [row["stock_code"] for row in cursor]

    def get_custom_all_stock_codes(self) -> List[str]:
        """返回去重后的全部分组股票代码（A 股格式），供分时拉取用"""
        with self._connect() as conn:
            cursor = conn.execute("SELECT DISTINCT stock_code FROM custom_group")
            return [row["stock_code"] for row in cursor]

    def get_stock_to_groups_map(self) -> Dict[str, List[str]]:
        """
        返回反向映射 {stock_code: [group_name, ...]}，供展示个股所属自选分组用。
        一只股票可属于多个分组（custom_group 是多对多）。group_name 去重保序。
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT stock_code, group_name FROM custom_group "
                "ORDER BY stock_code, group_id"
            )
            m: Dict[str, List[str]] = {}
            seen: Dict[str, set] = {}
            for row in cursor:
                code = row["stock_code"]
                name = row["group_name"]
                if code not in m:
                    m[code] = []
                    seen[code] = set()
                if name not in seen[code]:
                    m[code].append(name)
                    seen[code].add(name)
            return m
