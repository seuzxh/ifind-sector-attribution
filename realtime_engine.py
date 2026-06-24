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
import threading
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
    # singleflight：同一 cache_key 只允许一个拉取，其他复用（防缓存击穿）
    _series_locks: Dict[str, "threading.Lock"] = {}
    _series_locks_guard = threading.Lock()
    # 后台刷新标志：标记某 cache_key 正在后台刷新，避免重复触发
    _refreshing: Dict[str, bool] = {}
    _refreshing_guard = threading.Lock()
    # 看板结果缓存：compute_dashboard 的完整返回值，按 (mode, date, snapshot_time) 缓存
    # 同一看板+同一时刻的多人请求，第一个算完缓存，其他直接读（0ms，不计算）
    _result_cache: Dict[str, dict] = {}
    _result_locks: Dict[str, "threading.Lock"] = {}
    _result_locks_guard = threading.Lock()
    _RESULT_TTL = 10            # 看板结果 TTL（秒），略小于 series TTL，保证新鲜
    _RESULT_MAX_ENTRIES = 60    # result_cache 最大条数（LRU 防膨胀，拖滑块产生多个时刻）

    @classmethod
    def _cache_key(cls, trade_date: str, mode_key: str) -> str:
        return f"{trade_date}:{mode_key}"

    @classmethod
    def _is_cache_fresh(cls, cache_key: str, ttl: float) -> bool:
        rec = cls._series_cache.get(cache_key)
        return bool(rec) and (time.time() - rec["fetched_at"]) < ttl

    @classmethod
    def _get_series_lock(cls, cache_key: str) -> "threading.Lock":
        """获取 per-key 锁（不存在则创建，线程安全）"""
        with cls._series_locks_guard:
            lk = cls._series_locks.get(cache_key)
            if lk is None:
                lk = threading.Lock()
                cls._series_locks[cache_key] = lk
            return lk

    def _fetch_and_cache(self, trade_date: str, codes: List[str], mode_key: str, is_today: bool, cache_key: str) -> Optional[dict]:
        """实际拉取分时序列并写入缓存（同步，需在锁内或后台线程调用）。"""
        date_arg = None if is_today else trade_date
        print(f"[REALTIME] 拉取分时序列：{len(codes)} 只股票（{mode_key}）"
              f"{' 当日实时' if is_today else f' 历史 {trade_date}'}")
        t0 = time.time()
        series = self.fetcher.fetch_batch(codes, date=date_arg)
        if not series:
            print("[REALTIME] 分时序列为空")
            return None

        # 构建时间轴（trading + pre_market 时间点的并集，去重升序）
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

    def _trigger_bg_refresh(self, trade_date: str, codes: List[str], mode_key: str, is_today: bool, cache_key: str):
        """触发后台异步刷新（stale-while-revalidate）。已有刷新在跑则跳过。"""
        with self._refreshing_guard:
            if self._refreshing.get(cache_key):
                return  # 已有后台刷新在跑
            self._refreshing[cache_key] = True
        # 后台线程拉取，完成后清标志
        def _bg():
            try:
                lk = self._get_series_lock(cache_key)
                with lk:
                    self._fetch_and_cache(trade_date, codes, mode_key, is_today, cache_key)
            finally:
                with self._refreshing_guard:
                    self._refreshing[cache_key] = False
        threading.Thread(target=_bg, daemon=True).start()

    def _ensure_series(
        self,
        trade_date: str,
        codes: List[str],
        mode_key: str,
        is_today: bool,
    ) -> Optional[dict]:
        """
        确保序列缓存就绪。stale-while-revalidate 策略（请求永不阻塞）：
          - 缓存命中（TTL 内）→ 直接返回
          - 缓存过期但有旧数据（仅当日实时）→ 立即返回旧数据 + 后台异步刷新
          - 无缓存（冷启动）→ 同步拉取（singleflight，并发只一个拉）
        历史日期全天缓存（不过期）。
        """
        cache_key = self._cache_key(trade_date, mode_key)
        ttl = config.INTRADAY_CACHE_TTL

        # 1. 快速路径：缓存命中直接返回（无锁）
        if not is_today:
            if cache_key in self._series_cache:
                return self._series_cache[cache_key]
        else:
            if self._is_cache_fresh(cache_key, ttl):
                return self._series_cache[cache_key]

        # 2. 当日实时 + 缓存过期但有旧数据 → stale-while-revalidate
        if is_today and cache_key in self._series_cache:
            old = self._series_cache[cache_key]
            self._trigger_bg_refresh(trade_date, codes, mode_key, is_today, cache_key)
            return old   # 立即返回旧数据，不阻塞

        # 3. 无缓存（冷启动）→ 同步拉取，singleflight 防并发击穿
        lk = self._get_series_lock(cache_key)
        with lk:
            # double-check：等锁期间可能已有其他请求填入
            if cache_key in self._series_cache:
                return self._series_cache[cache_key]
            return self._fetch_and_cache(trade_date, codes, mode_key, is_today, cache_key)

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
                amount = float(sliced[-1].get("turnover", 0) or 0)  # 累计成交额（元）
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
                amount = 0.0  # 集合竞价阶段不计成交额

            rows.append({
                "code": code,
                "change_ratio": float(change_ratio),
                "speed": float(speed),
                "body": float(body),
                "acceleration": float(acceleration),
                "amount": float(amount),
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
        custom_mode: bool = False,
    ) -> Dict:
        """
        计算完整看板数据（基于分时序列切片）。

        :param trade_date: 交易日 YYYYMMDD（默认今天）
        :param snapshot_time: 截止时刻 HH:MM（如 "09:50"），None=最新
        :param top_n: 返回前/后 N 个板块
        :param watchlist_mode: 是否聚焦 watchlist（默认 True）
        :param watchlist_date: watchlist 日期，默认最近一次
        :param custom_mode: 自选股分组模式（优先级高于 watchlist_mode），
                            用 custom_group 表的分组替代概念板块
        """
        self._ensure_maps()

        today_str = datetime.now().strftime("%Y%m%d")
        if trade_date is None:
            trade_date = today_str
        trade_date = trade_date.replace("-", "")
        is_today = (trade_date == today_str)

        # active_* 变量：当前看板实际使用的分组映射与名称
        # 默认沿用概念板块体系；custom 模式覆盖为自选股分组
        active_members_map = None    # None 表示第4步再用 self._members_map（或 watchlist 过滤）
        active_concept_names = self._concept_names
        holding_stocks = []          # 持仓股清单（仅 custom 模式填，供前端醒目标注）

        # 自选股分组模式（优先级最高）：用 custom_group 表的分组替代概念板块
        if custom_mode:
            active_members_map = self.db.get_custom_members_map()
            if not active_members_map:
                return {"error": "自选股分组为空，请先 import-groups 导入", "trade_date": trade_date}
            active_concept_names = self.db.get_custom_group_names()
            codes = self.db.get_custom_all_stock_codes()
            mode_key = "custom_group"
            # 识别持仓分组（按 config.HOLDING_GROUP_NAME 匹配 block_name），取其成分股作为持仓股
            holding_name = getattr(config, "HOLDING_GROUP_NAME", "")
            for gid, gname in active_concept_names.items():
                if gname == holding_name:
                    holding_stocks = active_members_map.get(gid, [])
                    break
            print(f"[REALTIME] 自选分组模式：{len(active_members_map)} 分组，{len(codes)} 只股票"
                  f"{f'，持仓分组 {holding_name}={len(holding_stocks)} 只' if holding_stocks else ''}")
        # watchlist 模式：限定拉取范围 + 板块范围
        elif watchlist_mode:
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
        if active_members_map is not None:
            # custom 模式已设置完整的自定义分组映射
            members_map = active_members_map
        elif watchlist_mode and watchlist_concepts:
            members_map = {cc: self._members_map.get(cc, []) for cc in watchlist_concepts
                           if cc in self._members_map}
        else:
            members_map = self._members_map
        strength_df = calc_all_sectors_strength(rt_df, members_map)
        if strength_df.empty:
            return {"error": "板块强度计算为空", "trade_date": trade_date}

        # 5. top / bottom 板块
        # custom 模式：把分组名以 "ZT" 开头的涨停板分组单独拆出（不参与 top/bottom 排序），
        # 其余非 ZT 分组才进 top/bottom。sector 看板无此概念，zt_df 始终为 None。
        zt_df = None
        if custom_mode:
            def _is_zt(cc):
                name = active_concept_names.get(cc, cc)
                return name.strip().upper().startswith("ZT")
            zt_mask = strength_df["concept_code"].apply(_is_zt)
            zt_df = strength_df[zt_mask].sort_values("s1_return", ascending=False) if zt_mask.any() else None
            strength_df = strength_df[~zt_mask].copy()

        strength_sorted = strength_df.sort_values("score", ascending=False)
        top_df = strength_sorted.head(top_n)
        bottom_df = strength_sorted.tail(top_n).iloc[::-1]

        # 6. 板块成分股排名
        stock_name_map = self._stock_names or {}

        def _build_sector_entry(row, asc):
            cc = row["concept_code"]
            members = members_map.get(cc, [])
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
            # 该分组完整成分股与持仓股的交集（基于全部成分股，非仅 top10）
            holding_in = sorted(set(members) & set(holding_stocks)) if holding_stocks else []
            return {
                "concept_code": cc,
                "concept_name": active_concept_names.get(cc, cc),
                "score": round(float(row["score"]), 4),
                "s1_return": round(float(row["s1_return"]), 2),
                "body": round(float(row["s_body"]), 2) if pd.notna(row.get("s_body")) else None,
                "s2_breadth": round(float(row["s2_breadth"]), 4),
                "member_count": int(row["member_count"]),
                "holding_in_group": holding_in,   # 本分组包含的持仓股（供前端醒目标注）
                "members_top10": members_top10,
            }

        top_sectors = [_build_sector_entry(row, asc=False) for _, row in top_df.iterrows()]
        bottom_sectors = [_build_sector_entry(row, asc=True) for _, row in bottom_df.iterrows()]

        # ZT 涨停分组：custom 模式下，分组名以 "ZT" 开头的单独成区，不参与 top/bottom 排序
        zt_sectors = []
        if custom_mode and zt_df is not None and not zt_df.empty:
            zt_sectors = [_build_sector_entry(row, asc=False) for _, row in zt_df.iterrows()]

        # 7. 市场统计（基于切片末点）
        market_stats = self._market_stats(rt_df)

        return {
            "trade_date": trade_date,
            "snapshot_time": snapshot_time or latest_time,
            "latest_time": latest_time,
            "available_times": available_times,
            "is_today": is_today,
            "watchlist_mode": watchlist_mode,
            "custom_mode": custom_mode,
            "market_stats": market_stats,
            "top_sectors": top_sectors,
            "bottom_sectors": bottom_sectors,
            "zt_sectors": zt_sectors,   # ZT 涨停分组（仅 custom 模式，独立统计不参与 top/bottom）
            "holding_stocks": holding_stocks,   # 持仓股清单（仅 custom 模式非空，供前端醒目标注）
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

    # ========== 强势股归类扫描 ==========
    def scan_custom_groups(self, query: str) -> Dict:
        """
        自选股强势归类：用 iFinD MCP search_stocks 自然语言选股，
        把选出的股票与自选分组股票取交集，再按自选分组归类统计。

        与 scan_market_groups 的差异：
          - 归类维度是自选分组（custom_group 表），非 884 概念板块
          - 命中股取 search_stocks 结果 ∩ 自选分组股票（只看自选范围内的强势股）

        :param query: 自然语言选股条件
        :return: {query, pool_size, hit_total, group_hit_count, groups:[...]}
        """
        from mcp_proxy import MCPClient

        self._ensure_maps()
        if not query or not query.strip():
            return {"error": "请输入选股条件（query）"}

        members_map = self.db.get_custom_members_map()
        if not members_map:
            return {"error": "自选股分组为空，请先 import-groups 导入"}
        group_names = self.db.get_custom_group_names()
        custom_codes = set(self.db.get_custom_all_stock_codes())  # 自选股票全集（取交集用）

        # 1. 调 MCP search_stocks 选股（~4.5s）
        try:
            mcp = MCPClient.instance()
            md_raw = mcp.call_tool("stock", "search_stocks", {"query": query})
        except Exception as e:
            return {"error": f"MCP 选股失败：{e}", "query": query}
        if isinstance(md_raw, dict) and md_raw.get("error"):
            return {"error": f"MCP 选股失败：{md_raw.get('error')}", "query": query}

        # 2. 解析 markdown → {code: {name, change_ratio}}
        all_hit_lookup = _parse_search_stocks_md(str(md_raw))
        if not all_hit_lookup:
            return {"error": "选股结果为空或解析失败（检查 query 表达）", "query": query,
                    "raw_preview": str(md_raw)[:300]}

        # 3. 取自选交集：只保留在自选分组里的命中股
        hit_lookup = {c: v for c, v in all_hit_lookup.items() if c in custom_codes}
        hit_codes = set(hit_lookup.keys())

        # 4. 按自选分组归类（一股属多组，各组各计）
        groups_out = []
        for gid, g_codes in members_map.items():
            g_hit_codes = [c for c in g_codes if c in hit_codes]
            if not g_hit_codes:
                continue
            hits = []
            for c in g_hit_codes:
                m = hit_lookup[c]
                hits.append({
                    "code": c,
                    "name": m.get("name", ""),
                    "change_ratio": round(float(m["change_ratio"]), 2),
                })
            hits.sort(key=lambda x: x["change_ratio"], reverse=True)
            avg_chg = sum(h["change_ratio"] for h in hits) / len(hits)
            groups_out.append({
                "group_id": gid,
                "group_name": group_names.get(gid, gid),
                "hit_count": len(g_hit_codes),
                "member_total": len(g_codes),
                "coverage": round(len(g_hit_codes) / len(g_codes), 4) if g_codes else 0,
                "hit_avg_change": round(avg_chg, 2),
                "hits": hits,
            })

        groups_out.sort(key=lambda x: (x["hit_count"], x["hit_avg_change"]), reverse=True)

        return {
            "query": query,
            "pool_size": int(len(all_hit_lookup)),   # 全市场选股总数（参考）
            "hit_total": int(len(hit_codes)),         # 自选范围内的命中数
            "group_hit_count": len(groups_out),
            "groups": groups_out,
        }


    # ========== 全市场强势归类（MCP 选股 + 884 概念归类，不碰分时） ==========
    def scan_market_groups(self, query: str) -> Dict:
        """
        全市场强势股概念板块归类：用 iFinD MCP search_stocks 自然语言选股，
        把选出的股票按 884 概念板块归类统计。

        与 scan_custom_groups 的差异：
          - 命中股来自 MCP search_stocks（收盘数据），非分时筛选
          - 归类维度是 884 概念板块（self._members_map），非自选分组
          - 不依赖分时序列缓存，纯收盘 → 更轻量
          - hits 指标只有 change_ratio（search_stocks 返回的涨跌幅）

        :param query: 自然语言选股条件（如 "涨幅大于7%并且小于12.1%；未涨停；非ST"）
        :return: {query, pool_size, hit_total, group_hit_count, groups:[...]}（与 scan 同 schema）
        """
        from mcp_proxy import MCPClient

        self._ensure_maps()
        if not query or not query.strip():
            return {"error": "请输入选股条件（query）"}

        # 1. 调 MCP search_stocks 选股（~4.5s，返回 markdown 表格）
        try:
            mcp = MCPClient.instance()
            md_raw = mcp.call_tool("stock", "search_stocks", {"query": query})
        except Exception as e:
            return {"error": f"MCP 选股失败：{e}", "query": query}
        if isinstance(md_raw, dict) and md_raw.get("error"):
            return {"error": f"MCP 选股失败：{md_raw.get('error')}", "query": query}

        # 2. 解析 markdown → {code: {name, change_ratio}}
        hit_lookup = _parse_search_stocks_md(str(md_raw))
        if not hit_lookup:
            return {"error": "选股结果为空或解析失败（检查 query 表达）", "query": query,
                    "raw_preview": str(md_raw)[:300]}
        hit_codes = set(hit_lookup.keys())

        # 3. 按 884 概念板块归类（一股属多板块，各板块各计）
        members_map = self._members_map       # {884xxx.TI: [stock_codes]}
        group_names = self._concept_names      # {884xxx.TI: concept_name}
        groups_out = []
        for gid, g_codes in members_map.items():
            g_hit_codes = [c for c in g_codes if c in hit_codes]
            if not g_hit_codes:
                continue
            hits = []
            for c in g_hit_codes:
                m = hit_lookup[c]
                hits.append({
                    "code": c,
                    "name": m.get("name", ""),
                    "change_ratio": round(float(m["change_ratio"]), 2),
                })
            hits.sort(key=lambda x: x["change_ratio"], reverse=True)
            avg_chg = sum(h["change_ratio"] for h in hits) / len(hits)
            groups_out.append({
                "group_id": gid,
                "group_name": group_names.get(gid, gid),
                "hit_count": len(g_hit_codes),
                "member_total": len(g_codes),
                "coverage": round(len(g_hit_codes) / len(g_codes), 4) if g_codes else 0,
                "hit_avg_change": round(avg_chg, 2),
                "hits": hits,
            })

        groups_out.sort(key=lambda x: (x["hit_count"], x["hit_avg_change"]), reverse=True)

        return {
            "query": query,
            "pool_size": int(len(hit_codes)),
            "hit_total": int(len(hit_codes)),
            "group_hit_count": len(groups_out),
            "groups": groups_out,
        }


def _parse_search_stocks_md(md: str) -> Dict[str, dict]:
    """
    解析 MCP search_stocks 返回的 markdown 表格，提取 {股票代码: {name, change_ratio}}。

    表格形如：
        |股票代码|股票简称|涨跌幅:前复权[YYYYMMDD]|收盘价...|...
        |---|---|---|...
        |000955.SZ|欣龙控股|7.34341253|4.97|...

    列顺序可能随 query 变化，故先解析表头定位"涨跌幅"列索引，再按索引取值。
    :return: {code: {"name": str, "change_ratio": float}}
    """
    import json
    # md 可能是 JSON 字符串（含 data.result），也可能是纯 markdown
    try:
        j = json.loads(md)
        if isinstance(j, dict):
            md = j.get("data", {}).get("result", "") or md
    except (ValueError, TypeError):
        pass

    lines = [ln for ln in md.split("\n") if ln.strip().startswith("|")]
    if len(lines) < 2:
        return {}

    # 表头：找"股票代码""股票简称""涨跌幅"的列索引
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    idx_code = idx_name = idx_chg = -1
    for i, h in enumerate(header):
        if "股票代码" in h and idx_code < 0:
            idx_code = i
        elif "股票简称" in h and idx_name < 0:
            idx_name = i
        elif "涨跌幅" in h and idx_chg < 0:
            idx_chg = i
    if idx_code < 0:
        return {}

    result = {}
    for ln in lines:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) <= max(idx_code, idx_name, idx_chg):
            continue
        code = cells[idx_code]
        if not config.is_a_share_code(code):
            continue  # 只认 A 股代码
        name = cells[idx_name] if idx_name >= 0 else ""
        chg = 0.0
        if idx_chg >= 0:
            try:
                chg = float(cells[idx_chg])
            except (ValueError, IndexError):
                chg = 0.0
        result[code] = {"name": name, "change_ratio": chg}
    return result


# ========== 全局入口（带缓存） ==========
_engine_instance = None


def _result_cache_key(trade_date, snapshot_time, watchlist_mode, watchlist_date, custom_mode):
    """构造看板结果缓存的 key（同一看板+同一时刻的多人请求命中同一 key）"""
    mode = "custom" if custom_mode else ("wl:" + (watchlist_date or "") if watchlist_mode else "market")
    st = snapshot_time if snapshot_time else "latest"
    return f"{mode}:{trade_date or 'today'}:{st}"


def _get_result_lock(rk: str) -> "threading.Lock":
    """获取 per-result-key 锁（singleflight，防并发重复计算）"""
    with RealtimeEngine._result_locks_guard:
        lk = RealtimeEngine._result_locks.get(rk)
        if lk is None:
            lk = threading.Lock()
            RealtimeEngine._result_locks[rk] = lk
        return lk


def _result_cache_get(rk: str) -> Optional[dict]:
    """查 result_cache（命中且未过期返回结果，否则 None）"""
    rec = RealtimeEngine._result_cache.get(rk)
    if rec and (time.time() - rec["fetched_at"]) < RealtimeEngine._RESULT_TTL:
        return rec["data"]
    return None


def _result_cache_set(rk: str, data: dict):
    """写 result_cache（LRU 淘汰）"""
    cache = RealtimeEngine._result_cache
    cache[rk] = {"data": data, "fetched_at": time.time()}
    # LRU：超过上限删最早的（dict 保持插入序，删 first key）
    while len(cache) > RealtimeEngine._RESULT_MAX_ENTRIES:
        try:
            del cache[next(iter(cache))]
        except (StopIteration, KeyError):
            break


def get_realtime_dashboard(
    trade_date: str = None,
    snapshot_time: str = None,
    watchlist_mode: bool = True,
    watchlist_date: str = None,
    top_n: int = 10,
    custom_mode: bool = False,
) -> Dict:
    """
    获取实时看板。三层缓存（透明，前端无感）：
      1. result_cache：同一看板+同一时刻的多人请求 → 直读结果（0ms）
      2. series_cache：分时序列 stale-while-revalidate（不阻塞 + 后台刷新）
      3. 中焯 API：singleflight 合并（多人只拉一次）
    """
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = RealtimeEngine()

    # —— 第1层：result_cache 快速路径 ——
    rk = _result_cache_key(trade_date, snapshot_time, watchlist_mode, watchlist_date, custom_mode)
    cached = _result_cache_get(rk)
    if cached is not None:
        return cached

    # —— miss：singleflight 抢锁，只算一次 ——
    lk = _get_result_lock(rk)
    with lk:
        # double-check：等锁期间可能已有其他请求填入
        cached = _result_cache_get(rk)
        if cached is not None:
            return cached
        # 实际计算（内部走 series_cache + 中焯 singleflight）
        result = _engine_instance.compute_dashboard(
            trade_date=trade_date,
            snapshot_time=snapshot_time,
            top_n=top_n,
            watchlist_mode=watchlist_mode,
            watchlist_date=watchlist_date,
            custom_mode=custom_mode,
        )
        # 只缓存成功结果（不缓存 error）
        if "error" not in result:
            _result_cache_set(rk, result)
        return result


def scan_custom_groups(query: str) -> Dict:
    """自选股强势归类扫描（全局入口，复用 engine 单例）。MCP 选股 + 自选交集 + 自选分组归类。"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = RealtimeEngine()
    return _engine_instance.scan_custom_groups(query=query)


def scan_market_groups(query: str) -> Dict:
    """全市场强势归类扫描（全局入口，复用 engine 单例）。MCP 选股 + 884 概念归类。"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = RealtimeEngine()
    return _engine_instance.scan_market_groups(query)


def clear_cache():
    """清空全部缓存（分时序列 + 看板结果 + singleflight 锁 + 后台刷新标志，调试/切日用）"""
    with RealtimeEngine._series_locks_guard:
        RealtimeEngine._series_cache.clear()
        RealtimeEngine._series_locks.clear()
    with RealtimeEngine._refreshing_guard:
        RealtimeEngine._refreshing.clear()
    with RealtimeEngine._result_locks_guard:
        RealtimeEngine._result_cache.clear()
        RealtimeEngine._result_locks.clear()
