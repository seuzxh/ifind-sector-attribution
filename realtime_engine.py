# -*- coding: utf-8 -*-
"""
盘中实时引擎（基于分时数据）

数据源：kline-fetcher 的 TrendFetcher（中焯行情 API），获取每只股票的完整分时序列
（集合竞价 pre_market + 盘中逐分钟 trading）。

核心设计：
  1. 分时序列缓存：按 (mode_key, trade_date) 缓存完整序列，TTL 内不重拉网络
  2. snapshot_time 切片：时间条拖到某时刻，截取 trading[:snapshot_time] 用末点算指标
     - 历史日期：全天缓存，拖动滑块纯内存切片
     - 当日实时：TTL 到期才刷新序列
  3. 指标算法（基于分时末点）：
     - 涨幅 change_ratio = (last - pre_close) / pre_close × 100
     - body（开盘至今）= (last - open) / open × 100，open = pre_market[-1].ref_price
     - 涨速 speed（1min 滚动末点）= (last[-1] - last[-2]) / last[-2] × 100
     - 加速 acceleration = speed[-1] - speed[-2]
  4. watchlist 模式：仅拉盘前筛出的成分股（~279 只，1.5s），聚焦监控
"""

import os
import sys
import time
from datetime import datetime
from typing import List, Dict, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from database import Database
from intraday_fetcher import IntradayFetcher
from core_calculator import calc_all_sectors_strength
from stock_scorer import (
    score_members,
    compute_body_ratio,
    compute_speed,
    compute_speed_series,
    compute_acceleration,
)


class RealtimeEngine:
    """盘中实时板块强度引擎（分时数据版）"""

    def __init__(self):
        self.db = Database()
        self.fetcher = IntradayFetcher()
        # 成分股映射缓存（永久缓存，进程生命周期内不变）
        self._members_map = None
        self._concept_names = None
        self._stock_names = None

    def _ensure_maps(self):
        """懒加载成分股映射、概念名、股票名（首次调用构建）"""
        if self._members_map is not None:
            return
        print("[REALTIME] 构建成分股映射缓存...")
        import sqlite3
        concept_codes = self.db.get_a_share_concept_codes()
        members_map = {}
        for cc in concept_codes:
            members = self.db.get_concept_members(cc)
            if members:
                members_map[cc] = [m["stock_code"] for m in members]
        self._members_map = members_map

        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            self._concept_names = {
                row["concept_code"]: row["concept_name"]
                for row in conn.execute("SELECT concept_code, concept_name FROM ths_concept_dict")
            }
            self._stock_names = {}
            for row in conn.execute(
                "SELECT stock_code, stock_name FROM concept_members "
                "WHERE member_date = (SELECT MAX(member_date) FROM concept_members)"
            ):
                if row["stock_code"] not in self._stock_names:
                    self._stock_names[row["stock_code"]] = row["stock_name"]
        print(f"[REALTIME] 缓存就绪：{len(members_map)} 个概念")

    # ========== 序列缓存 ==========
    # _series_cache[cache_key] = {
    #     "trade_date": "20260612",
    #     "series": { code: {pre_close, open, trading:[...]}, ... },
    #     "codes": [...],            # 拉取时的代码范围（watchlist 或全市场）
    #     "fetched_at": timestamp,
    #     "latest_time": "15:00",    # 序列中最新的分钟点
    #     "available_times": ["09:30", "09:31", ...],  # 时间轴（去重升序）
    # }
    _series_cache: Dict[str, dict] = {}

    @classmethod
    def _cache_key(cls, trade_date: str, mode_key: str) -> str:
        return f"{trade_date}:{mode_key}"

    @classmethod
    def _is_cache_fresh(cls, cache_key: str, ttl: float) -> bool:
        rec = cls._series_cache.get(cache_key)
        return bool(rec) and (time.time() - rec["fetched_at"]) < ttl

    def _ensure_series(
        self,
        trade_date: str,
        codes: List[str],
        mode_key: str,
        is_today: bool,
    ) -> Optional[dict]:
        """
        确保序列缓存就绪。当日实时按 TTL 刷新；历史日期全天缓存（长有效）。
        """
        cache_key = self._cache_key(trade_date, mode_key)
        # 历史日期：有缓存就用（全天数据不变），无则拉一次
        if not is_today:
            if cache_key in self._series_cache:
                return self._series_cache[cache_key]
        else:
            # 当日实时：TTL 内复用
            ttl = config.INTRADAY_CACHE_TTL
            if self._is_cache_fresh(cache_key, ttl):
                return self._series_cache[cache_key]

        # 拉取（date=None 表示当日实时；历史传 YYYYMMDD）
        date_arg = None if is_today else trade_date
        print(f"[REALTIME] 拉取分时序列：{len(codes)} 只股票（{mode_key}）"
              f"{' 当日实时' if is_today else f' 历史 {trade_date}'}")
        t0 = time.time()
        series = self.fetcher.fetch_batch(codes, date=date_arg)
        if not series:
            print("[REALTIME] 分时序列为空")
            return None

        # 构建时间轴（trading + pre_market 时间点的并集，去重升序）
        # 集合竞价期间 trading 为空，需纳入 pre_market 时间点，否则时间轴为空
        times_set = set()
        for rec in series.values():
            for p in rec.get("trading", []):
                times_set.add(p["time"])
            for p in rec.get("pre_market", []):
                times_set.add(p["time"])
        available_times = sorted(times_set)
        latest_time = available_times[-1] if available_times else None

        rec = {
            "trade_date": trade_date,
            "series": series,
            "codes": codes,
            "fetched_at": time.time(),
            "latest_time": latest_time,
            "available_times": available_times,
        }
        self._series_cache[cache_key] = rec
        print(f"[REALTIME] 序列就绪：{len(series)} 只，{len(available_times)} 个时间点，"
              f"最新 {latest_time}，耗时 {time.time()-t0:.1f}s")
        return rec

    # ========== 切片 → 指标 ==========
    @staticmethod
    def _slice_to_snapshot(trading: list, snapshot_time: Optional[str]) -> list:
        """
        截取 trading 中 time <= snapshot_time 的子序列。
        snapshot_time 为 None 或超出范围时返回全部（=最新）。
        """
        if not snapshot_time or not trading:
            return trading
        # 已按时间正序，找 <= snapshot_time 的前缀
        sliced = [p for p in trading if p["time"] <= snapshot_time]
        return sliced if sliced else trading[:1]  # 至少保留一点

    def _build_indicator_df(
        self,
        series: Dict[str, dict],
        snapshot_time: Optional[str],
    ) -> pd.DataFrame:
        """
        把分时序列按 snapshot_time 切片，计算每只股票的指标，返回 DataFrame。
        列：[code, change_ratio, speed, body, acceleration]

        两阶段处理（按 snapshot_time 严格判断归属）：
          - snapshot_time 落在盘中（trading 有 <= 该时刻的点）：用连续竞价切片算全部指标
          - snapshot_time 落在集合竞价（trading 无 <= 该时刻的点，但 pre_market 有）：
            仅用 pre_market 末点 ref_price 算 change_ratio，speed/body/acceleration 置 0
        """
        rows = []
        for code, rec in series.items():
            pre_close = rec.get("pre_close")
            if not pre_close or pre_close == 0:
                continue

            # 严格按 snapshot_time 切 trading（不兜底回退，否则会把 9:20 误判成 9:30）
            trading = rec.get("trading", [])
            if snapshot_time:
                sliced = [p for p in trading if p["time"] <= snapshot_time]
            else:
                sliced = trading  # 无 snapshot_time = 取最新

            if sliced:
                # —— 盘中：用连续竞价序列算全部指标 ——
                last_prices = [p["last_price"] for p in sliced]
                last = last_prices[-1]
                change_ratio = (last / pre_close - 1) * 100
                body = compute_body_ratio(rec.get("open"), last)
                speed = compute_speed(last_prices)
                speed_series = compute_speed_series(last_prices)
                acceleration = compute_acceleration(speed_series)
            else:
                # —— 集合竞价：仅用 pre_market 末点 ref_price 算涨跌幅 ——
                pm = rec.get("pre_market") or []
                pm_sliced = self._slice_to_snapshot(pm, snapshot_time) if snapshot_time else pm
                if not pm_sliced:
                    continue
                last = pm_sliced[-1]["ref_price"]
                change_ratio = (last / pre_close - 1) * 100
                # 此阶段无连续价格序列，speed/body/acceleration 无意义，置 0
                body = 0.0
                speed = 0.0
                acceleration = 0.0

            rows.append({
                "code": code,
                "change_ratio": float(change_ratio),
                "speed": float(speed),
                "body": float(body),
                "acceleration": float(acceleration),
            })

        return pd.DataFrame(rows)

    # ========== 看板计算 ==========
    def compute_dashboard(
        self,
        trade_date: str = None,
        snapshot_time: str = None,
        top_n: int = 10,
        watchlist_mode: bool = True,
        watchlist_date: str = None,
    ) -> Dict:
        """
        计算完整看板数据（基于分时序列切片）。

        :param trade_date: 交易日 YYYYMMDD（默认今天）
        :param snapshot_time: 截止时刻 HH:MM（如 "09:50"），None=最新
        :param top_n: 返回前/后 N 个板块
        :param watchlist_mode: 是否聚焦 watchlist（默认 True）
        :param watchlist_date: watchlist 日期，默认最近一次
        """
        self._ensure_maps()

        today_str = datetime.now().strftime("%Y%m%d")
        if trade_date is None:
            trade_date = today_str
        trade_date = trade_date.replace("-", "")
        is_today = (trade_date == today_str)

        # watchlist 模式：限定拉取范围 + 板块范围
        watchlist_concepts = None
        if watchlist_mode:
            wl_date = watchlist_date or self.db.get_latest_watchlist_date()
            if not wl_date:
                return {"error": "watchlist 模式但当日未跑 prescreen，请先盘前筛选", "trade_date": trade_date}
            watchlist_concepts = self.db.get_watchlist_concepts(wl_date)
            codes = self.db.get_watchlist_stock_codes(wl_date)
            if not watchlist_concepts or not codes:
                return {"error": f"watchlist 为空（{wl_date}），请先盘前筛选", "trade_date": trade_date}
            mode_key = f"watchlist:{wl_date}"
            print(f"[REALTIME] watchlist 模式：{len(watchlist_concepts)} 板块，{len(codes)} 只股票")
        else:
            codes = self.db.get_all_member_stock_codes()
            mode_key = "market"

        # 1. 确保序列缓存
        cache_rec = self._ensure_series(trade_date, codes, mode_key, is_today)
        if cache_rec is None:
            return {"error": "无分时数据（可能非交易时段或 API 不可达）", "trade_date": trade_date}

        # 2. 校正 snapshot_time（历史时刻回看）
        available_times = cache_rec["available_times"]
        latest_time = cache_rec["latest_time"]
        if snapshot_time and snapshot_time != "latest":
            # 如果指定时刻早于最早点，回退到最新
            if available_times and snapshot_time < available_times[0]:
                snapshot_time = None
        else:
            snapshot_time = None  # "latest" 或 None 都按最新处理

        # 3. 切片算指标
        rt_df = self._build_indicator_df(cache_rec["series"], snapshot_time)
        if rt_df.empty:
            return {"error": "指标计算为空", "trade_date": trade_date}

        # 4. 算板块强度
        members_map = self._members_map
        if watchlist_mode and watchlist_concepts:
            members_map = {cc: self._members_map.get(cc, []) for cc in watchlist_concepts
                           if cc in self._members_map}
        strength_df = calc_all_sectors_strength(rt_df, members_map)
        if strength_df.empty:
            return {"error": "板块强度计算为空", "trade_date": trade_date}

        # 5. top / bottom 板块
        strength_sorted = strength_df.sort_values("score", ascending=False)
        top_df = strength_sorted.head(top_n)
        bottom_df = strength_sorted.tail(top_n).iloc[::-1]

        # 6. 板块成分股排名
        stock_name_map = self._stock_names or {}

        def _build_sector_entry(row, asc):
            cc = row["concept_code"]
            members = self._members_map.get(cc, [])
            member_df = rt_df[rt_df["code"].isin(members)].copy()
            members_top10 = []
            if not member_df.empty:
                scored = score_members(member_df)
                picked = scored.tail(10).iloc[::-1] if asc else scored.head(10)
                members_top10 = [
                    {
                        "code": r["code"],
                        "name": stock_name_map.get(r["code"], ""),
                        "change_ratio": round(float(r["change_ratio"]), 2),
                        "speed": round(float(r["speed"]), 2),
                        "body": round(float(r["body"]), 2),
                        "acceleration": round(float(r["acceleration"]), 2),
                        "limit": int(r["limit_score"]),
                        "score": round(float(r["score"]), 4),
                    }
                    for _, r in picked.iterrows()
                ]
            return {
                "concept_code": cc,
                "concept_name": self._concept_names.get(cc, cc),
                "score": round(float(row["score"]), 4),
                "s1_return": round(float(row["s1_return"]), 2),
                "s2_breadth": round(float(row["s2_breadth"]), 4),
                "member_count": int(row["member_count"]),
                "members_top10": members_top10,
            }

        top_sectors = [_build_sector_entry(row, asc=False) for _, row in top_df.iterrows()]
        bottom_sectors = [_build_sector_entry(row, asc=True) for _, row in bottom_df.iterrows()]

        # 7. 市场统计（基于切片末点）
        market_stats = self._market_stats(rt_df)

        return {
            "trade_date": trade_date,
            "snapshot_time": snapshot_time or latest_time,
            "latest_time": latest_time,
            "available_times": available_times,
            "is_today": is_today,
            "watchlist_mode": watchlist_mode,
            "market_stats": market_stats,
            "top_sectors": top_sectors,
            "bottom_sectors": bottom_sectors,
        }

    @staticmethod
    def _market_stats(rt_df: pd.DataFrame) -> Dict:
        def _is_limit(code, chg):
            pure = code.split(".")[0]
            if code.endswith(".BJ"):
                return chg >= 29.0
            if pure.startswith(("300", "688")):
                return chg >= 19.5
            return chg >= 9.8

        chg = rt_df["change_ratio"]
        return {
            "stock_count": int(len(rt_df)),
            "market_avg_change": round(float(chg.mean()), 2),
            "up_count": int((chg > 0).sum()),
            "down_count": int((chg < 0).sum()),
            "flat_count": int((chg == 0).sum()),
            "limit_up_count": int(sum(
                1 for code, c in zip(rt_df["code"], chg) if pd.notna(c) and _is_limit(code, c)
            )),
        }


# ========== 全局入口（带缓存） ==========
_engine_instance = None


def get_realtime_dashboard(
    trade_date: str = None,
    snapshot_time: str = None,
    watchlist_mode: bool = True,
    watchlist_date: str = None,
    top_n: int = 10,
) -> Dict:
    """获取实时看板（分时数据版）。序列缓存在 engine 内部按 TTL 管理。"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = RealtimeEngine()
    return _engine_instance.compute_dashboard(
        trade_date=trade_date,
        snapshot_time=snapshot_time,
        top_n=top_n,
        watchlist_mode=watchlist_mode,
        watchlist_date=watchlist_date,
    )


def clear_cache():
    """清空分时序列缓存（调试/切日用）"""
    RealtimeEngine._series_cache.clear()
