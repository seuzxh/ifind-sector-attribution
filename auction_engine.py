# -*- coding: utf-8 -*-
"""
集合竞价选股选分组引擎（9:20~9:25 不可撤单窗口）

理论依据：9:20-9:25 是集合竞价的"不可撤单窗口"，此阶段挂单是真实意图
（vs 9:15-9:20 可撤单易诱多）。主流量化选股因子：
  - 竞价高开幅度（资金做多意愿）
  - 竞价量比（爆量 = 资金介入强度，主流阈值 2~5 倍昨日量）
  - 挂单失衡度（买盘抢筹 vs 卖盘出逃）
  - 价格趋势（9:20→9:25 抬升度，弱转强 / 真假突破）

观察池：自选股分组（custom_group）全部去重个股，按分组分别评分（分组联动 = 主题效应）。
数据源：kline-fetcher 分时数据 pre_market（含 ref_price/matched_vol/non_matched 量能字段）。

核心流程：
  1. 取观察池（get_custom_all_stock_codes）→ 拉分时序列（复用 IntradayFetcher series_cache）
  2. 每股算 4 因子：高开/爆量/失衡/趋势
  3. Z-score 标准化 + 加权 → 个股综合分
  4. 按 get_custom_members_map 聚合到分组 → 分组强度 + top 成分股
  5. 持仓分组（CC）成分股标注 holding，前端醒目
"""

import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from database import Database
from intraday_fetcher import IntradayFetcher
from stock_scorer import _zscore


class AuctionEngine:
    """集合竞价选股选分组引擎"""

    def __init__(self):
        self.db = Database()
        self.fetcher = IntradayFetcher()
        self._stock_names: Optional[Dict[str, str]] = None
        self._holding_stocks: Optional[set] = None

    def _ensure_stock_names(self):
        """懒加载股票名称（从 concept_members 最新快照，custom_group 无 name 列）"""
        if self._stock_names is not None:
            return
        import sqlite3
        names = {}
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(
                "SELECT stock_code, stock_name FROM concept_members "
                "WHERE member_date = (SELECT MAX(member_date) FROM concept_members)"
            ):
                if row["stock_code"] not in names:
                    names[row["stock_code"]] = row["stock_name"]
        self._stock_names = names

    def _get_holding_stocks(self) -> set:
        """获取持仓分组（CC）的成分股代码集合"""
        if self._holding_stocks is not None:
            return self._holding_stocks
        holding_name = getattr(config, "HOLDING_GROUP_NAME", "")
        members_map = self.db.get_custom_members_map()
        group_names = self.db.get_custom_group_names()
        holding = set()
        for gid, gname in group_names.items():
            if gname == holding_name:
                holding.update(members_map.get(gid, []))
                break
        self._holding_stocks = holding
        return holding

    # ========== 4 因子计算 ==========
    @staticmethod
    def _calc_factors(rec: dict, yest_volume: float, snapshot_time: Optional[str]) -> Optional[dict]:
        """
        从单股 pre_market 序列算 4 个竞价因子。
        :param rec: IntradayFetcher._fetch_one 返回的 {pre_close, open, trading, pre_market}
        :param yest_volume: 昨日成交量（股），用于算量比；0/None 表示无昨日数据
        :param snapshot_time: 截止时刻 HH:MM（如 "09:25"），None=取末点
        :return: {gap_pct, vol_ratio, order_imbalance, trend_score} 或 None
        """
        pm = rec.get("pre_market") or []
        if len(pm) < 2:
            return None
        pre_close = rec.get("pre_close")
        if not pre_close or pre_close == 0:
            return None

        # 切片到 snapshot_time（<= 该时刻的点）
        if snapshot_time:
            sliced = [p for p in pm if p["time"] <= snapshot_time]
            if not sliced:
                return None
        else:
            sliced = pm

        last = sliced[-1]
        last_ref = last["ref_price"]

        # 因子1：高开幅度（相对昨收）
        gap_pct = (last_ref / pre_close - 1) * 100

        # 因子2：竞价量比 = 竞价成交量 / 昨日成交量
        matched_vol = last.get("matched_vol", 0) or 0
        if yest_volume and yest_volume > 0:
            vol_ratio = matched_vol / yest_volume
        else:
            vol_ratio = 0.0  # 无昨日量（新股等）无法算量比

        # 因子3：挂单失衡度 = (未撮合买 - 未撮合卖) / (买+卖)，范围 [-1, 1]
        nmb = last.get("non_matched_vol_buy", 0) or 0
        nms = last.get("non_matched_vol_sell", 0) or 0
        total_nm = nmb + nms
        order_imbalance = (nmb - nms) / total_nm if total_nm > 0 else 0.0

        # 因子4：价格趋势 = 末点 ref / 9:20 点 ref - 1（9:20-9:25 抬升度）
        # 找 <= "09:20" 的最后一个点作为基准
        ref_0920 = None
        for p in sliced:
            if p["time"] <= "09:20":
                ref_0920 = p["ref_price"]
            else:
                break
        if ref_0920 and ref_0920 > 0:
            trend_score = (last_ref / ref_0920 - 1) * 100
        else:
            trend_score = 0.0  # 早于 9:20 无基准点

        return {
            "gap_pct": float(gap_pct),
            "vol_ratio": float(vol_ratio),
            "order_imbalance": float(order_imbalance),
            "trend_score": float(trend_score),
        }

    def _build_factor_df(self, series: Dict[str, dict], yest_volumes: Dict[str, float],
                         snapshot_time: Optional[str]) -> pd.DataFrame:
        """对观察池所有股票算 4 因子，返回 DataFrame"""
        rows = []
        for code, rec in series.items():
            factors = self._calc_factors(rec, yest_volumes.get(code, 0), snapshot_time)
            if factors is None:
                continue
            factors["code"] = code
            rows.append(factors)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        # Z-score 标准化（4 因子分别标准化，跨全市场）
        for col in ["gap_pct", "vol_ratio", "order_imbalance", "trend_score"]:
            df["z_" + col] = _zscore(df[col])
        # 综合分 = 加权 Z-score
        w = config.AUCTION_SCORE_WEIGHTS
        df["score"] = (
            w["gap_pct"] * df["z_gap_pct"]
            + w["vol_ratio"] * df["z_vol_ratio"]
            + w["order_imbalance"] * df["z_order_imbalance"]
            + w["trend_score"] * df["z_trend_score"]
        )
        return df

    # ========== 分组聚合 ==========
    def _aggregate_groups(self, factor_df: pd.DataFrame, members_map: Dict[str, List[str]],
                          group_names: Dict[str, str]) -> List[dict]:
        """按分组聚合竞价强度，返回分组列表（已排序）"""
        if factor_df.empty:
            return []
        code_to_row = {r["code"]: r for _, r in factor_df.iterrows()}
        groups = []
        for gid, codes in members_map.items():
            member_rows = [code_to_row[c] for c in codes if c in code_to_row]
            if not member_rows:
                continue
            n = len(member_rows)
            gname = group_names.get(gid, gid)
            # 分组维度
            sector_gap = float(np.mean([r["gap_pct"] for r in member_rows]))
            # 量比用中位数（抗极端值）
            sector_vol_ratio = float(np.median([r["vol_ratio"] for r in member_rows]))
            sector_imbalance = float(np.mean([r["order_imbalance"] for r in member_rows]))
            # 联动度 = 高开 > 阈值 的成分占比（主题效应强度）
            coherency = float(np.mean([1 if r["gap_pct"] >= config.AUCTION_GAP_MIN else 0
                                       for r in member_rows]))
            # 分组综合分 = 成分股 score 均值（复用个股已标准化的分）
            sector_score = float(np.mean([r["score"] for r in member_rows]))
            # 持仓分组标记
            is_zt = gname.startswith("ZT")
            groups.append({
                "group_id": gid,
                "group_name": gname,
                "member_count": n,
                "sector_gap": round(sector_gap, 2),
                "sector_vol_ratio": round(sector_vol_ratio, 3),
                "sector_imbalance": round(sector_imbalance, 3),
                "coherency": round(coherency, 3),
                "score": round(sector_score, 4),
                "is_zt": is_zt,
            })
        # 按综合分降序
        groups.sort(key=lambda x: x["score"], reverse=True)
        return groups

    def _top_stocks_of_group(self, factor_df: pd.DataFrame, codes: List[str], top_n: int) -> List[dict]:
        """取某分组 top 成分股"""
        sub = factor_df[factor_df["code"].isin(codes)].sort_values("score", ascending=False).head(top_n)
        holding = self._get_holding_stocks()
        result = []
        for _, r in sub.iterrows():
            result.append({
                "code": r["code"],
                "name": self._stock_names.get(r["code"], ""),
                "gap_pct": round(float(r["gap_pct"]), 2),
                "vol_ratio": round(float(r["vol_ratio"]), 2),
                "order_imbalance": round(float(r["order_imbalance"]), 3),
                "trend_score": round(float(r["trend_score"]), 2),
                "score": round(float(r["score"]), 4),
                "holding": r["code"] in holding,
            })
        return result

    # ========== 主入口 ==========
    def compute(self, trade_date: Optional[str] = None, snapshot_time: Optional[str] = None,
                top_stock: int = None, top_group: int = None) -> Dict:
        """
        计算竞价选股选分组看板。

        :param trade_date: 交易日 YYYYMMDD，None=今天（当日实时）；传历史日期则拉该日全天分时
        :param snapshot_time: 截止时刻 HH:MM（如 "09:25"），None=取 pre_market 末点
        :param top_stock: 返回 top 个股数（默认 config.AUCTION_TOP_STOCK）
        :param top_group: 返回 top 分组数（默认 config.AUCTION_TOP_GROUP）
        """
        self._ensure_stock_names()
        top_stock = top_stock or config.AUCTION_TOP_STOCK
        top_group = top_group or config.AUCTION_TOP_GROUP
        holding = self._get_holding_stocks()

        # 0. 日期处理：None=今天（当日实时），否则历史日期
        today_str = datetime.now().strftime("%Y%m%d")
        if trade_date:
            trade_date = trade_date.replace("-", "")
            is_today = (trade_date == today_str)
        else:
            trade_date = today_str
            is_today = True

        # 1. 观察池 + 分组映射
        codes = self.db.get_custom_all_stock_codes()
        members_map = self.db.get_custom_members_map()
        group_names = self.db.get_custom_group_names()
        if not codes:
            return {"error": "自选股分组为空，请先 import-groups 导入"}

        # 2. 拉前一交易日成交量（算量比）：取严格 < trade_date 的最新交易日
        # （get_latest_trade_date 用 <= 可能返回当日，竞价量比要用昨日的量）
        import sqlite3 as _sqlite3
        yest_date = None
        with _sqlite3.connect(self.db.db_path) as _conn:
            row = _conn.execute(
                "SELECT MAX(trade_date) FROM daily_kline WHERE trade_date < ?", (trade_date,)
            ).fetchone()
            yest_date = row[0] if row else None
        yest_volumes = {}
        if yest_date:
            for r in self.db.get_daily_kline_by_date(yest_date):
                v = r.get("volume")
                if v and v > 0:
                    yest_volumes[r["code"]] = float(v)

        # 3. 拉分时序列（当日实时 date=None；历史传 YYYYMMDD）
        date_arg = None if is_today else trade_date
        series = self.fetcher.fetch_batch(codes, date=date_arg)
        if not series:
            return {"error": "无分时数据（可能非集合竞价时段或历史日期无数据）"}

        # 历史日期无 pre_market（中焯 API 只对当日实时返回集合竞价），给出明确提示
        if not is_today:
            has_pm = any(rec.get("pre_market") for rec in series.values())
            if not has_pm:
                return {
                    "error": f"{trade_date} 无集合竞价数据（历史日期中焯 API 不返回 pre_market，"
                             f"集合竞价选股仅支持当日实时）",
                    "trade_date": trade_date, "is_today": is_today,
                }

        # 4. 算 4 因子 + 综合分
        factor_df = self._build_factor_df(series, yest_volumes, snapshot_time)
        if factor_df.empty:
            return {"error": "竞价因子计算为空（可能非集合竞价时段）"}

        # 5. 分组聚合
        groups = self._aggregate_groups(factor_df, members_map, group_names)

        # 6. top 个股（全市场）
        top_stocks_df = factor_df.sort_values("score", ascending=False).head(top_stock)
        top_stocks = []
        for _, r in top_stocks_df.iterrows():
            top_stocks.append({
                "code": r["code"],
                "name": self._stock_names.get(r["code"], ""),
                "gap_pct": round(float(r["gap_pct"]), 2),
                "vol_ratio": round(float(r["vol_ratio"]), 2),
                "order_imbalance": round(float(r["order_imbalance"]), 3),
                "trend_score": round(float(r["trend_score"]), 2),
                "score": round(float(r["score"]), 4),
                "holding": r["code"] in holding,
            })

        # 7. top 分组（拆 ZT 分组单独区，避免成分少被主流排名挤掉）
        main_groups = [g for g in groups if not g["is_zt"]][:top_group]
        zt_groups = [g for g in groups if g["is_zt"]]
        for g in main_groups + zt_groups:
            g["top_stocks"] = self._top_stocks_of_group(
                factor_df, members_map.get(g["group_id"], []), 10
            )

        # 8. 市场统计
        gaps = factor_df["gap_pct"]
        market_stats = {
            "stock_count": int(len(factor_df)),
            "avg_gap": round(float(gaps.mean()), 2),
            "up_count": int((gaps > 0).sum()),
            "down_count": int((gaps < 0).sum()),
            "strong_gap_count": int((gaps >= config.AUCTION_GAP_MIN).sum()),  # 高开>2%
            "limit_up_count": int((gaps >= 9.8).sum()),  # 简化：>=9.8 视为涨停级
            "explode_count": int((factor_df["vol_ratio"] >= config.AUCTION_VOL_RATIO_MIN).sum()),  # 爆量
        }

        return {
            "trade_date": trade_date,
            "is_today": is_today,
            "snapshot_time": snapshot_time or "09:25",
            "yest_date": yest_date,
            "market_stats": market_stats,
            "top_stocks": top_stocks,
            "top_groups": main_groups,
            "zt_groups": zt_groups,
        }


# ========== 全局入口（带缓存）==========
_engine_instance = None
# 缓存按 trade_date 分别存（历史日期全天不变，可长缓存；当日实时短缓存）
_last_result = {}  # {trade_date: result}
_last_result_time = {}  # {trade_date: timestamp}


def compute_auction_dashboard(trade_date: str = None, snapshot_time: str = None,
                              use_cache: bool = True) -> Dict:
    """
    获取竞价看板（带内存缓存）。
    当日实时：TTL = config.AUCTION_CACHE_TTL（短缓存，9:25 后数据不变也很快过期无妨）。
    历史日期：全天数据不变，长缓存。
    """
    global _engine_instance
    td_key = (trade_date or "today").replace("-", "")
    now = time.time()
    if use_cache and td_key in _last_result and (now - _last_result_time.get(td_key, 0)) < config.AUCTION_CACHE_TTL:
        return _last_result[td_key]
    if _engine_instance is None:
        _engine_instance = AuctionEngine()
    result = _engine_instance.compute(trade_date=trade_date, snapshot_time=snapshot_time)
    if "error" not in result:
        _last_result[td_key] = result
        _last_result_time[td_key] = now
    return result


def clear_cache():
    """清空竞价看板缓存"""
    _last_result.clear()
    _last_result_time.clear()
