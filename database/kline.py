# -*- coding: utf-8 -*-
"""行情域。表归属：daily_kline / min1_kline。"""

from datetime import datetime
from typing import Dict, List, Optional


class KlineMixin:
    """日K / 1min K线读写"""

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
