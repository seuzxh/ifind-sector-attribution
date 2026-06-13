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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
        """
        with self._connect() as conn:
            conn.executescript(ddl)

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

    def get_all_member_stock_codes(self) -> List[str]:
        """
        从成分股表反查全部独立股票代码（全市场股票池）。
        成分股表覆盖主板/创业板/科创板/北交所，作为 daily 同步 K 线的默认代码来源。
        取最新一份快照，避免历史重复。
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT stock_code FROM concept_members
                WHERE member_date = (SELECT MAX(member_date) FROM concept_members)
            """)
            return [row["stock_code"] for row in cursor.fetchall()]

    def get_all_mapped_stock_codes(self) -> List[str]:
        """
        获取 stock_concept_map 中有概念映射的独立股票代码（取最新快照）。
        用于归因计算，避免对全市场无映射股票空查。
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT stock_code FROM stock_concept_map
                WHERE map_date = (SELECT MAX(map_date) FROM stock_concept_map)
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
        """保存板块强度评分"""
        with self._connect() as conn:
            for r in records:
                conn.execute("""
                    INSERT OR REPLACE INTO concept_strength
                    (calc_date, concept_code, s1_return, s2_breadth, s4_relative,
                     score_1d, score_5d, score_20d, score_final, rank_1d, coherency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    r["calc_date"], r["concept_code"], r.get("s1_return"),
                    r.get("s2_breadth"), r.get("s4_relative"),
                    r.get("score_1d"), r.get("score_5d"), r.get("score_20d"),
                    r.get("score_final"), r.get("rank_1d"), r.get("coherency")
                ))

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
