# -*- coding: utf-8 -*-
"""
盘中实时引擎

从 iFinD 接口4（high_frequency）拉取全市场 A 股的 1min K 线，
构造实时涨跌幅 DataFrame，计算板块强度与成分股综合评分。

实时模式不走多周期融合（无历史1min数据），只用当日 1d 强度。
"""

import os
import sys
import time
from datetime import datetime
from typing import List, Dict, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from ifind_client import IFindClient
from database import Database
from core_calculator import calc_all_sectors_strength
from stock_scorer import score_members, compute_body_ratio, compute_speed


# 实时拉取的批次大小（接口4 单次代码数）
REALTIME_BATCH_SIZE = 50


class RealtimeEngine:
    """盘中实时板块强度引擎"""

    def __init__(self):
        self.client = IFindClient()
        self.db = Database()
        # 成分股映射缓存（永久缓存，进程生命周期内不变）
        self._members_map = None
        self._concept_names = None

    def _ensure_members_map(self):
        """懒加载成分股映射（只在首次调用时构建）"""
        if self._members_map is None:
            print("[REALTIME] 构建成分股映射缓存...")
            concept_codes = self.db.get_a_share_concept_codes()
            members_map = {}
            for cc in concept_codes:
                members = self.db.get_concept_members(cc)
                if members:
                    members_map[cc] = [m["stock_code"] for m in members]
            self._members_map = members_map
            # 概念名映射
            self._concept_names = {}
            import sqlite3
            with sqlite3.connect(self.db.db_path) as conn:
                conn.row_factory = sqlite3.Row
                for row in conn.execute("SELECT concept_code, concept_name FROM ths_concept_dict"):
                    self._concept_names[row["concept_code"]] = row["concept_name"]
            print(f"[REALTIME] 缓存就绪：{len(members_map)} 个概念")

    def fetch_realtime_data(
        self,
        trade_date: str = None,
        start_time: str = "09:30",
        end_time: str = None,
    ) -> pd.DataFrame:
        """
        拉取全市场 A 股的实时 1min K 线，构造 realtime_df。

        :param trade_date: 交易日，格式 YYYYMMDD 或 YYYY-MM-DD；默认今天
        :param start_time: 起始时间 HH:MM，默认 09:30
        :param end_time: 结束时间 HH:MM，默认当前时刻
        :return: DataFrame，列 [code, change_ratio, speed, body, open, close, stock_name]
        """
        # 日期处理
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y-%m-%d")
        else:
            trade_date = trade_date.replace("-", "-") if "-" not in trade_date else trade_date
            if len(trade_date) == 8:
                trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

        if end_time is None:
            end_time = datetime.now().strftime("%H:%M")

        start_full = f"{trade_date} {start_time}:00"
        end_full = f"{trade_date} {end_time}:59"

        # 全市场 A 股代码
        all_codes = self.db.get_all_member_stock_codes()
        print(f"[REALTIME] 拉取 {len(all_codes)} 只股票 {trade_date} {start_time}~{end_time}")

        # 分批调用接口4
        records = []
        total_batches = (len(all_codes) + REALTIME_BATCH_SIZE - 1) // REALTIME_BATCH_SIZE
        for i in range(0, len(all_codes), REALTIME_BATCH_SIZE):
            batch = all_codes[i:i + REALTIME_BATCH_SIZE]
            try:
                resp = self.client.get_high_frequency(
                    batch, start_full, end_full,
                    indicators="open,high,low,close,changeRatio"
                )
                if resp.get("errorcode") != 0:
                    # 整批失败，跳过
                    continue
                for item in resp.get("tables", []):
                    code = item.get("thscode", "")
                    rec = self._parse_one_stock(code, item)
                    if rec:
                        records.append(rec)
            except Exception as e:
                print(f"[REALTIME] 批次 {i//REALTIME_BATCH_SIZE+1}/{total_batches} 失败: {e}")
                continue

            if (i // REALTIME_BATCH_SIZE) % 20 == 0:
                print(f"[REALTIME] 进度 {min(i+REALTIME_BATCH_SIZE, len(all_codes))}/{len(all_codes)}")

        if not records:
            print("[REALTIME] 未拉取到任何数据")
            return pd.DataFrame()

        df = pd.DataFrame(records)
        print(f"[REALTIME] 解析完成：{len(df)} 只股票")
        return df

    def _parse_one_stock(self, code: str, item: dict) -> Optional[dict]:
        """
        解析单只股票的接口4响应，提取涨幅/涨速/实体涨幅。
        """
        tbl = item.get("table", {})
        times = item.get("time", [])
        if not times:
            return None

        change_ratios = tbl.get("changeRatio", [])
        opens = tbl.get("open", [])
        closes = tbl.get("close", [])

        if not change_ratios or not closes:
            return None

        # 取最后一根有效K线
        # 涨幅：最后一根的 changeRatio（相对昨收）
        change_ratio = self._last_valid(change_ratios)
        if change_ratio is None or pd.isna(change_ratio):
            return None

        last_open = self._last_valid(opens)
        last_close = self._last_valid(closes)

        # 实体涨幅（最后一根K线）
        body = compute_body_ratio(last_open, last_close)

        # 涨速（近3分钟）
        speed = compute_speed(
            [c for c in closes if c is not None and not pd.isna(c)]
        )

        return {
            "code": code,
            "change_ratio": float(change_ratio),
            "speed": float(speed),
            "body": float(body),
            "open": float(last_open) if last_open else 0.0,
            "close": float(last_close) if last_close else 0.0,
        }

    @staticmethod
    def _last_valid(series: list):
        """取列表中最后一个非 None/非 NaN 值"""
        for v in reversed(series):
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                return v
        return None

    def compute_dashboard(
        self,
        trade_date: str = None,
        start_time: str = "09:30",
        end_time: str = None,
        top_n: int = 10,
    ) -> Dict:
        """
        计算完整看板数据：top/bottom 板块 + top 板块的成分股排名。

        :return: dict {
            trade_date, snapshot_time, market_stats,
            top_sectors: [{concept_code, concept_name, score, s1, s2, members_top10: [...]}],
            bottom_sectors: [{concept_code, concept_name, score, s1, s2}]
        }
        """
        self._ensure_members_map()

        # 1. 拉实时数据
        rt_df = self.fetch_realtime_data(trade_date, start_time, end_time)
        if rt_df.empty:
            return {"error": "无实时数据（可能非交易时段）", "trade_date": trade_date}

        # 2. 算板块强度
        strength_df = calc_all_sectors_strength(rt_df, self._members_map)
        if strength_df.empty:
            return {"error": "板块强度计算为空", "trade_date": trade_date}

        # 3. top / bottom 板块
        strength_sorted = strength_df.sort_values("score", ascending=False)
        top_df = strength_sorted.head(top_n)
        bottom_df = strength_sorted.tail(top_n).iloc[::-1]  # 最弱在前

        # 4. top 板块的成分股排名
        top_sectors = []
        for _, row in top_df.iterrows():
            cc = row["concept_code"]
            members = self._members_map.get(cc, [])
            # 该板块有实时数据的成分股
            member_df = rt_df[rt_df["code"].isin(members)].copy()
            if member_df.empty:
                continue
            scored = score_members(member_df)
            top10 = scored.head(10)
            top_sectors.append({
                "concept_code": cc,
                "concept_name": self._concept_names.get(cc, cc),
                "score": round(float(row["score"]), 4),
                "s1_return": round(float(row["s1_return"]), 2),
                "s2_breadth": round(float(row["s2_breadth"]), 4),
                "member_count": int(row["member_count"]),
                "members_top10": [
                    {
                        "code": r["code"],
                        "change_ratio": round(float(r["change_ratio"]), 2),
                        "speed": round(float(r["speed"]), 2),
                        "body": round(float(r["body"]), 2),
                        "limit": int(r["limit_score"]),
                        "score": round(float(r["score"]), 4),
                    }
                    for _, r in top10.iterrows()
                ],
            })

        bottom_sectors = [
            {
                "concept_code": row["concept_code"],
                "concept_name": self._concept_names.get(row["concept_code"], row["concept_code"]),
                "score": round(float(row["score"]), 4),
                "s1_return": round(float(row["s1_return"]), 2),
                "s2_breadth": round(float(row["s2_breadth"]), 4),
            }
            for _, row in bottom_df.iterrows()
        ]

        # 市场统计
        market_stats = {
            "stock_count": int(len(rt_df)),
            "market_avg_change": round(float(rt_df["change_ratio"].mean()), 2),
            "up_count": int((rt_df["change_ratio"] > 0).sum()),
            "down_count": int((rt_df["change_ratio"] < 0).sum()),
            "flat_count": int((rt_df["change_ratio"] == 0).sum()),
            "limit_up_count": int(rt_df.apply(
                lambda r: 1 if r["change_ratio"] >= (
                    29.0 if r["code"].endswith(".BJ")
                    else (19.5 if r["code"].split(".")[0].startswith(("300","688")) else 9.8)
                ) else 0, axis=1
            ).sum()),
        }

        return {
            "trade_date": trade_date,
            "snapshot_time": datetime.now().strftime("%H:%M:%S"),
            "market_stats": market_stats,
            "top_sectors": top_sectors,
            "bottom_sectors": bottom_sectors,
        }


# 全局引擎实例 + 缓存（避免每次请求重建）
_engine_instance = None
_last_dashboard = None
_last_dashboard_time = 0
CACHE_TTL = 10  # 秒


def get_realtime_dashboard(
    trade_date: str = None,
    start_time: str = "09:30",
    end_time: str = None,
    use_cache: bool = True,
) -> Dict:
    """
    获取实时看板数据（带内存缓存）。
    若距上次拉取 < CACHE_TTL 秒，直接返回缓存（避免高频请求打爆 iFinD）。
    """
    global _engine_instance, _last_dashboard, _last_dashboard_time

    now = time.time()
    if use_cache and _last_dashboard is not None and (now - _last_dashboard_time) < CACHE_TTL:
        return _last_dashboard

    if _engine_instance is None:
        _engine_instance = RealtimeEngine()

    result = _engine_instance.compute_dashboard(trade_date, start_time, end_time)
    if "error" not in result:
        _last_dashboard = result
        _last_dashboard_time = now
    return result
