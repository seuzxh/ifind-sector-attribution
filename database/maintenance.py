# -*- coding: utf-8 -*-
"""维护域：海外数据清理 / 新股判定。跨多表（只做删改与统计，无领域归属）。"""

from typing import Dict, Set

import config


class MaintenanceMixin:
    """数据库维护操作"""

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

    def get_new_stock_codes(self, calc_date: str, min_days: int = 5) -> Set[str]:
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
