# -*- coding: utf-8 -*-
"""
板块三表域：字典 / 成分股 / 个股映射（委托 ifind-sector-hub 组件）
+ 概念代码池读取（归因池 / 观察池）。
表归属：ths_concept_dict / concept_members / stock_concept_map（组件建表）。
"""

from typing import Dict, List

import config


class SectorTablesMixin:
    """三表委托访问器与概念池读取（方法与拆包前逐一致）"""

    # ========== 概念字典操作 ==========
    def save_concept_dict(self, concepts: List[Dict[str, str]], update_date: str = None):
        """保存概念板块字典（委托 ifind-sector-hub 组件）"""
        self.sector_store.save_concept_dict(concepts, update_date)

    def _migrate_watched_hook(self, conn, removed_codes, old_name_map, new_name_map):
        """字典全量替换时的 watched 勾选迁移（在组件 replace 的同一事务内执行）。"""
        observe_prefixes = getattr(config, "OBSERVE_CONCEPT_PREFIXES", ("884", "885", "886"))
        name_to_new = {}
        for nc, nm in new_name_map.items():
            if nc[:3] in observe_prefixes:
                name_to_new.setdefault(nm, nc)
        migrated, dropped_watched = [], []
        for r in conn.execute("SELECT concept_code FROM watched_concepts").fetchall():
            cc = r["concept_code"]
            if cc in new_name_map:
                continue
            old_name = old_name_map.get(cc, "")
            base_name = old_name.rstrip("Ⅲ").rstrip("Ⅱ").rstrip("Ⅰ").strip()
            target = name_to_new.get(base_name)
            if target:
                migrated.append((cc, target, old_name))
                conn.execute("UPDATE watched_concepts SET concept_code=? WHERE concept_code=?",
                             (target, cc))
            else:
                dropped_watched.append((cc, old_name))
        if dropped_watched:
            ph = ",".join("?" * len(dropped_watched))
            conn.execute(f"DELETE FROM watched_concepts WHERE concept_code IN ({ph})",
                         [c for c, _ in dropped_watched])
        return migrated, dropped_watched

    def refresh_concept_dict_replace(self, boards: List[Dict[str, str]]) -> Dict:
        """
        以 smart_stock_picking 枚举结果为准全量替换板块字典（委托组件，同事务）。
        级联清理 concept_members + watched 勾选按名称迁移（钩子在组件事务内执行）。
        :param boards: [{"concept_code", "concept_name", "category", "level", ...}]
        :return: {added, removed, migrated: [(old, new, name)], dropped_watched: [(code, name)]}
        """
        return self.sector_store.replace_concept_dict(boards, migrate_hook=self._migrate_watched_hook)

    def get_all_concept_codes(self) -> List[str]:
        """获取所有概念代码"""
        return self.sector_store.get_all_concept_codes()

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

    def get_all_member_stock_codes(self) -> List[str]:
        """
        从成分股表反查全部 A 股股票代码（全市场股票池）。委托组件（快照语义不变）。
        """
        return self.sector_store.get_all_member_stock_codes()

    def get_all_mapped_stock_codes(self) -> List[str]:
        """
        获取 stock_concept_map 中有概念映射的 A 股独立股票代码（取最新快照）。委托组件。
        """
        return self.sector_store.get_all_mapped_stock_codes()

    def get_concept_name(self, concept_code: str) -> str:
        """获取概念名称"""
        return self.sector_store.get_concept_name(concept_code)

    # ========== 个股-概念映射操作 ==========
    def save_stock_concept_map(self, mappings: Dict[str, List[Dict[str, str]]], map_date: str):
        """保存个股-概念映射（委托组件）"""
        self.sector_store.save_stock_concept_map(mappings, map_date)

    def get_stock_concepts(self, stock_code: str, map_date: str = None) -> List[Dict]:
        """获取某个股的概念映射（委托组件；不传日期取最新快照）"""
        return self.sector_store.get_stock_concepts(stock_code, map_date)

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

    def get_stock_concepts_reverse_map(self) -> Dict[str, List[str]]:
        """
        全市场「个股 → 板块代码列表」反查映射（get_stock_concepts_from_members 的批量版）。

        语义与单股版一致：每股取其自身最新 member_date 快照，结果应用板块池过滤。
        背景：concept_members 无 stock_code 索引，逐股查询全市场 5500+ 次 ≈ 6 分钟/天；
        本方法用单 SQL（一次全表扫描 + 分组）把归因预处理降到秒级。
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT cm.stock_code, cm.concept_code
                FROM concept_members cm
                JOIN (
                    SELECT stock_code, MAX(member_date) AS md
                    FROM concept_members GROUP BY stock_code
                ) latest ON cm.stock_code = latest.stock_code AND cm.member_date = latest.md
            """)
            reverse: Dict[str, List[str]] = {}
            for row in cursor:
                if config.is_in_sector_pool(row["concept_code"]):
                    reverse.setdefault(row["stock_code"], []).append(row["concept_code"])
            return reverse

    # ========== 概念成分股操作 ==========
    def save_concept_members(self, concept_code: str, members: List[Dict], member_date: str):
        """保存概念板块成分股（委托组件）"""
        self.sector_store.save_concept_members(concept_code, members, member_date)

    def get_concept_members(self, concept_code: str, member_date: str = None) -> List[Dict]:
        """获取概念板块成分股列表（委托组件；不传日期取最新快照）"""
        return self.sector_store.get_concept_members(concept_code, member_date)

    def get_concept_members_map(self, concept_codes: List[str]) -> Dict[str, List[Dict]]:
        """批量读取各概念最新成分股快照（委托组件，单连接批量优化保留）"""
        return self.sector_store.get_concept_members_map(concept_codes)

    # ========== 三表只读访问器（收编原各处裸 SQL，委托组件） ==========
    def get_concept_names(self) -> Dict[str, str]:
        """字典 code → name 全量映射"""
        return self.sector_store.get_concept_names()

    def get_latest_member_date(self) -> str:
        """成分股表最新快照日期，空表返回空串"""
        return self.sector_store.get_latest_member_date()

    def get_latest_member_stock_names(self) -> Dict[str, str]:
        """最新快照股票名映射（首个出现优先）"""
        return self.sector_store.get_latest_member_stock_names()

    def get_all_member_stock_names(self) -> Dict[str, str]:
        """全历史股票名映射 MAX(stock_name)"""
        return self.sector_store.get_all_member_stock_names()

    def get_latest_members_snapshot(self):
        """最新快照全体成分：(snap_date, {concept_code: [(stock_code, stock_name), ...]})"""
        return self.sector_store.get_latest_members_snapshot()
