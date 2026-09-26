# -*- coding: utf-8 -*-
"""Database 核心：连接管理、建表初始化、按领域 Mixin 组装"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import config
from ifind_sector_hub import SectorStore

from .custom_group import CustomGroupMixin
from .kg import KgraphMixin
from .kline import KlineMixin
from .maintenance import MaintenanceMixin
from .results import ResultsMixin
from .schema import DDL
from .sector_tables import SectorTablesMixin
from .watched import WatchedMixin


class Database(
    SectorTablesMixin,
    WatchedMixin,
    KgraphMixin,
    KlineMixin,
    ResultsMixin,
    CustomGroupMixin,
    MaintenanceMixin,
):
    """SQLite 数据库操作类（领域读写方法在各 Mixin 中，按表归属分模块）"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DB_PATH
        # 确保数据库所在目录存在（DB_PATH 默认在 data/ 下，仓库不包含该目录）
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        # 板块三表（字典/成分股/映射）归 ifind-sector-hub 组件管理（同库文件，幂等建表）
        self.sector_store = SectorStore(self.db_path)
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
        """初始化数据库表结构（板块三表已由 ifind-sector-hub 组件建表，此处只建 monitor 私有表）"""
        with self._connect() as conn:
            # 读多写少的看板服务使用 WAL：读请求不再被 daily 写事务阻塞。
            # synchronous=NORMAL 是连接级配置，已在 _connect 中为每个连接设置。
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(DDL)
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
