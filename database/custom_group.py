# -*- coding: utf-8 -*-
"""自选股分组域。表归属：custom_group。"""

from typing import Dict, List


class CustomGroupMixin:
    """自选股分组读写（同花顺 custom_block 导入）"""

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
            return m
