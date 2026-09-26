# -*- coding: utf-8 -*-
"""监控板块勾选域。表归属：watched_concepts。"""

from datetime import datetime
from typing import List

import config


class WatchedMixin:
    """watched_concepts 读写（持久化选择 + 成员数资格规则）"""

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
