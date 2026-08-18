# -*- coding: utf-8 -*-
"""
知识图谱数据源适配层（SourceAdapter 协议 + iFinD 两个内置实现）。

每个数据源实现 fetch_pairs()，统一产出 [(stock_code, sector_code, props)] 全量当前归属，
kg_builder 不感知来源细节——未来接入申万行业/问财概念只需新增 Adapter，不动核心。

内置源：
  - IfindMembersAdapter    接口2（data_pool p03473，板块→成分股）——主源，权威
  - IfindStockConceptAdapter 接口1（个股→概念，仅覆盖 885/886）——交叉验证源
"""

import os
import sys
from datetime import datetime
from typing import Dict, List, Protocol, Tuple, runtime_checkable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import Database


Pair = Tuple[str, str, dict]


@runtime_checkable
class SourceAdapter(Protocol):
    """数据源适配器协议：全量拉取当前归属对。"""
    source: str

    def fetch_pairs(self) -> List[Pair]:
        """返回 [(stock_code, sector_code, props), ...]"""
        ...


class IfindMembersAdapter:
    """
    主源：接口2（板块→成分股）。
    优先复用 concept_members 当天最新快照（零接口调用）；
    快照过期或 --refetch 时调 SyncPipeline._fetch_concept_members_batch 刷新（636 板块 8 并发 ≈1.5min）。
    """
    source = "ifind_p03473"

    def __init__(self, db: Database, refetch: bool = False):
        self.db = db
        self.refetch = refetch

    def fetch_pairs(self) -> List[Pair]:
        today = datetime.now().strftime("%Y%m%d")
        latest = self._latest_snapshot_date()
        if self.refetch or latest != today:
            print(f"[KG-SOURCE] 主源快照 {latest} 过期（或 --refetch），调接口2刷新...")
            from sync_pipeline import SyncPipeline
            pipeline = SyncPipeline()
            member_codes = self.db.get_observe_concept_codes()
            pipeline._fetch_concept_members_batch(member_codes, today)
        else:
            print(f"[KG-SOURCE] 主源复用当天快照 {today}（零接口调用）")

        members_map = self.db.get_concept_members_map(self.db.get_observe_concept_codes())
        snapshot_date = self._latest_snapshot_date()  # 循环外取一次（36万对，循环内查库会卡死）
        pairs: List[Pair] = []
        for sector_code, members in members_map.items():
            for m in members:
                pairs.append((m["stock_code"], sector_code, {
                    "stock_name": m.get("stock_name", ""),
                    "member_date": snapshot_date,
                }))
        print(f"[KG-SOURCE] 主源产出 {len(pairs)} 条归属对")
        return pairs

    def _latest_snapshot_date(self) -> str:
        import sqlite3
        with sqlite3.connect(self.db.db_path) as conn:
            row = conn.execute("SELECT MAX(member_date) FROM concept_members").fetchone()
            return row[0] or ""


class IfindStockConceptAdapter:
    """
    交叉验证源：接口1（个股→同花顺概念）。
    覆盖 885/886 概念板块（不含 884 行业，属该源预期行为，非数据缺失）。
    股票全集从成分股表反查（A 股全市场），55 批 ≈1min。
    """
    source = "ifind_concept"

    def __init__(self, db: Database):
        self.db = db

    def fetch_pairs(self) -> List[Pair]:
        from ifind_client import IFindClient
        date = datetime.now().strftime("%Y-%m-%d")
        stocks = self.db.get_all_member_stock_codes()
        print(f"[KG-SOURCE] 验证源拉取 {len(stocks)} 只个股的概念归属（接口1）...")
        client = IFindClient()
        mappings = client.batch_get_stock_concepts(stocks, date)

        pairs: List[Pair] = []
        for stock_code, concepts in mappings.items():
            for c in concepts:
                cc = c.get("concept_code", "")
                if cc[:3] not in ("885", "886"):
                    continue  # 该源不覆盖 884 行业，跳过非概念板块
                pairs.append((stock_code, cc, {
                    "stock_name": "",
                    "concept_name": c.get("concept_name", ""),
                }))
        print(f"[KG-SOURCE] 验证源产出 {len(pairs)} 条归属对")
        return pairs
