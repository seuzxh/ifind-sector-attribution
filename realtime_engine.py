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
        codes_override: List[str] = None,
    ) -> pd.DataFrame:
        """
        拉取实时 1min K 线，构造 realtime_df。

        :param trade_date: 交易日，格式 YYYYMMDD 或 YYYY-MM-DD；默认今天
        :param start_time: 起始时间 HH:MM，默认 09:30
        :param end_time: 结束时间 HH:MM，默认当前时刻
        :param codes_override: 指定股票代码列表（watchlist 模式用，默认全市场 A 股）
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

        # 股票代码：watchlist 模式用指定列表，否则全市场 A 股
        all_codes = codes_override if codes_override else self.db.get_all_member_stock_codes()
        mode_tag = "watchlist" if codes_override else "全市场"
        print(f"[REALTIME] 拉取 {len(all_codes)} 只股票（{mode_tag}） {trade_date} {start_time}~{end_time}")

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
        watchlist_mode: bool = False,
        watchlist_date: str = None,
    ) -> Dict:
        """
        计算完整看板数据：top/bottom 板块 + top 板块的成分股排名。

        :param watchlist_mode: 是否聚焦 watchlist（盘前筛选出的板块+成分股）
        :param watchlist_date: watchlist 日期，默认取最近一次
        :return: dict {
            trade_date, snapshot_time, market_stats,
            top_sectors: [{concept_code, concept_name, score, s1, s2, members_top10: [...]}],
            bottom_sectors: [{concept_code, concept_name, score, s1, s2}]
        }
        """
        self._ensure_members_map()

        # watchlist 模式：限定拉取范围 + 板块范围
        watchlist_concepts = None
        codes_override = None
        if watchlist_mode:
            wl_date = watchlist_date or self.db.get_latest_watchlist_date()
            if not wl_date:
                return {"error": "watchlist 模式但当日未跑 prescreen，请先盘前筛选", "trade_date": trade_date}
            watchlist_concepts = self.db.get_watchlist_concepts(wl_date)
            codes_override = self.db.get_watchlist_stock_codes(wl_date)
            if not watchlist_concepts or not codes_override:
                return {"error": f"watchlist 为空（{wl_date}），请先盘前筛选", "trade_date": trade_date}
            print(f"[REALTIME] watchlist 模式：{len(watchlist_concepts)} 板块，{len(codes_override)} 只股票")

        # 1. 拉实时数据（watchlist 模式只拉 watchlist 股票）
        rt_df = self.fetch_realtime_data(trade_date, start_time, end_time, codes_override=codes_override)
        if rt_df.empty:
            return {"error": "无实时数据（可能非交易时段）", "trade_date": trade_date}

        # 2. 算板块强度
        members_map = self._members_map
        if watchlist_mode and watchlist_concepts:
            # 只对 watchlist 板块算强度
            members_map = {cc: self._members_map.get(cc, []) for cc in watchlist_concepts
                           if cc in self._members_map}
        strength_df = calc_all_sectors_strength(rt_df, members_map)
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
            "watchlist_mode": watchlist_mode,
            "market_stats": market_stats,
            "top_sectors": top_sectors,
            "bottom_sectors": bottom_sectors,
        }


# 全局引擎实例 + 缓存（避免每次请求重建）
_engine_instance = None
# 缓存按 watchlist_mode 分别存（避免切换模式时串数据）
_last_dashboard = {}  # {mode_key: result}
_last_dashboard_time = {}  # {mode_key: timestamp}
CACHE_TTL = 10  # 秒


def get_realtime_dashboard(
    trade_date: str = None,
    start_time: str = "09:30",
    end_time: str = None,
    use_cache: bool = True,
    watchlist_mode: bool = False,
    watchlist_date: str = None,
) -> Dict:
    """
    获取实时看板数据（带内存缓存）。
    若距上次拉取 < CACHE_TTL 秒，直接返回缓存（避免高频请求打爆 iFinD）。
    watchlist 模式与全市场模式分别缓存。
    """
    global _engine_instance

    mode_key = "watchlist" if watchlist_mode else "market"
    now = time.time()
    if use_cache and mode_key in _last_dashboard and (now - _last_dashboard_time.get(mode_key, 0)) < CACHE_TTL:
        return _last_dashboard[mode_key]

    if _engine_instance is None:
        _engine_instance = RealtimeEngine()

    result = _engine_instance.compute_dashboard(
        trade_date, start_time, end_time,
        watchlist_mode=watchlist_mode,
        watchlist_date=watchlist_date,
    )
    if "error" not in result:
        _last_dashboard[mode_key] = result
        _last_dashboard_time[mode_key] = now
    return result
