# -*- coding: utf-8 -*-
"""
FastAPI 服务层
提供 REST API 接口供外部调用
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import pandas as pd

from database import Database
from core_calculator import calc_portfolio_attribution

app = FastAPI(
    title="行业归因与板块强度检测系统",
    description="基于 iFinD API 的量化行业归因与板块强度检测",
    version="1.0.0"
)

db = Database()

# 挂载静态文件目录（前端页面资源）
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ========== 请求模型 ==========
class StockAttributionRequest(BaseModel):
    stock_codes: List[str]
    date: Optional[str] = None


class PortfolioAttributionRequest(BaseModel):
    holdings: List[dict]
    date: Optional[str] = None


class PrescreenRequest(BaseModel):
    date: Optional[str] = None
    top_sector: Optional[int] = None
    top_stock: Optional[int] = None


# ========== API 接口 ==========
@app.get("/", response_class=HTMLResponse)
def root():
    """返回可视化看板页面"""
    index_path = os.path.join(_TEMPLATE_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>templates/index.html 未找到</h1>"


@app.get("/api/sector/rankings")
def get_sector_rankings(date: str = None, top_n: int = 10):
    """
    获取概念板块强度排名
    :param date: 日期，如 "20260613"，默认最新日期
    :param top_n: 返回前 N 个
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    rankings = db.get_sector_rankings(date, top_n=top_n)
    return {
        "date": date,
        "count": len(rankings),
        "rankings": rankings
    }


@app.post("/api/attribution/stock")
def get_stock_attribution(req: StockAttributionRequest):
    """
    获取个股多概念归因
    :param req: stock_codes + date
    """
    date = req.date or datetime.now().strftime("%Y%m%d")
    results = []

    for stock_code in req.stock_codes:
        attr = db.get_stock_attribution(stock_code, date)
        if attr:
            results.append(attr)

    return {
        "date": date,
        "count": len(results),
        "results": results
    }


@app.post("/api/attribution/portfolio")
def get_portfolio_attribution(req: PortfolioAttributionRequest):
    """
    组合归因 + 强势板块定位
    :param req: holdings + date
    """
    date = req.date or datetime.now().strftime("%Y%m%d")
    result = calc_portfolio_attribution(db, req.holdings, date)
    return result


@app.get("/api/realtime/sector")
def get_realtime_sector(top_n: int = 5):
    """
    获取最新板块强度排名（从数据库读取最新计算结果）
    :param top_n: 返回前 N 个
    """
    date = datetime.now().strftime("%Y%m%d")
    rankings = db.get_sector_rankings(date, top_n=top_n)
    return {
        "date": date,
        "count": len(rankings),
        "rankings": rankings
    }


@app.get("/api/concept/list")
def get_concept_list():
    """获取全部概念板块列表"""
    codes = db.get_all_concept_codes()
    return {
        "count": len(codes),
        "concepts": codes
    }


@app.get("/api/concept/members")
def get_concept_members(concept_code: str, date: str = None):
    """
    获取概念板块成分股
    :param concept_code: 概念代码，如 "886102.TI"
    :param date: 成分股快照日期；不传则取最新一份缓存
    """
    members = db.get_concept_members(concept_code, date)  # date=None 时取最新缓存
    return {
        "concept_code": concept_code,
        "date": date,
        "count": len(members),
        "members": members
    }


# ========== 可视化看板路由 ==========

@app.get("/api/realtime/dashboard")
def get_realtime_dashboard(
    trade_date: str = None,
    snapshot_time: str = None,
    top_n: int = 10,
    watchlist_mode: bool = True,
    watchlist_date: str = None,
):
    """
    实时看板（分时数据版）：拉取分时序列，按 snapshot_time 切片算板块强度 + 成分股排名。

    分时序列在引擎内按 (日期, 模式) 缓存，TTL 内不重拉网络（watchlist ~1.5s 拉取，TTL 5s）。
    snapshot_time 切片纯内存（毫秒级），用于时间条拖动回看历史时刻。

    :param trade_date: 交易日 YYYYMMDD，默认今天（当日实时）；传历史日期则拉该日全天分时
    :param snapshot_time: 截止时刻 HH:MM（如 "09:50"），None 或 "latest" = 最新时刻
    :param top_n: 返回前/后 N 个板块
    :param watchlist_mode: 聚焦 watchlist（默认 True，约 279 只股票 1.5s 拉取）
    :param watchlist_date: watchlist 日期，默认最近一次
    """
    from realtime_engine import get_realtime_dashboard as _fetch
    return _fetch(
        trade_date=trade_date,
        snapshot_time=snapshot_time,
        top_n=top_n,
        watchlist_mode=watchlist_mode,
        watchlist_date=watchlist_date,
    )


@app.post("/api/realtime/clear_cache")
def clear_realtime_cache():
    """清空分时序列缓存（切日/调试用）。"""
    from realtime_engine import clear_cache
    clear_cache()
    return {"ok": True}


@app.post("/api/prescreen")
def trigger_prescreen(req: PrescreenRequest):
    """
    触发盘前筛选（页面按钮用）：5日涨幅选板块+成分股，存入 watchlist。
    """
    from prescreen import run_prescreen
    date = req.date or datetime.now().strftime("%Y%m%d")
    result = run_prescreen(
        db, date,
        top_sector=req.top_sector,
        top_stock=req.top_stock,
    )
    return result


@app.get("/api/watchlist")
def get_watchlist(date: str = None):
    """
    读取 watchlist（盘前筛选结果）。
    :param date: 日期，默认最近一次
    """
    date = date or db.get_latest_watchlist_date()
    if not date:
        return {"error": "无 watchlist 数据，请先盘前筛选", "date": None}
    rows = db.get_watchlist(date)
    # 按板块分组组装
    sectors = {}
    for r in rows:
        cc = r["concept_code"]
        if cc not in sectors:
            sectors[cc] = {
                "concept_code": cc,
                "concept_name": r["concept_name"],
                "sector_5d_return": r["sector_5d_return"],
                "rank_sector": r["rank_sector"],
                "stocks": [],
            }
        sectors[cc]["stocks"].append({
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "stock_5d_return": r["stock_5d_return"],
            "rank_stock": r["rank_stock"],
        })
    sector_list = sorted(sectors.values(), key=lambda x: x["rank_sector"])
    return {"date": date, "sector_count": len(sector_list), "sectors": sector_list}


@app.get("/api/history/dashboard")
def get_history_dashboard(date: str, top_n: int = 10, force_calc: bool = False):
    """
    历史看板：读取已入库的收盘数据（concept_strength + daily_kline）。
    成分股排名按当日涨幅（历史模式无1min，降级为纯涨幅）。

    :param date: 历史日期 YYYYMMDD
    :param top_n: 返回前 N 个板块
    :param force_calc: 无数据时是否自动拉取并计算（耗时约2分钟）
    """
    import sqlite3

    # 板块强度（读 concept_strength）
    rankings = db.get_sector_rankings(date, top_n=999)  # 取全部再切片
    if not rankings:
        # force_calc：按需拉取 K 线 + 计算
        if force_calc:
            from sync_pipeline import SyncPipeline
            # 校验日期格式
            try:
                datetime.strptime(date, "%Y%m%d")
            except ValueError:
                return {"error": f"日期格式错误，需 YYYYMMDD：{date}", "date": date}
            try:
                pipeline = SyncPipeline()
                # 检查该日是否有 K 线（接口3 单日窗口在交易日能拿到数据）
                # run_daily 会同步单日 K 线 + 算板块强度 + 归因
                pipeline.sync_daily_kline(pipeline.db.get_all_member_stock_codes(), date, date)
                pipeline.calc_daily_strength(date)
                pipeline.calc_daily_attribution(date)
                # 重新读取
                rankings = db.get_sector_rankings(date, top_n=999)
            except Exception as e:
                return {"error": f"计算失败：{e}", "date": date}
            if not rankings:
                return {"error": f"日期 {date} 可能非交易日或无行情数据", "date": date}
        else:
            return {"error": f"日期 {date} 无数据，可点击\"拉取并计算\"获取", "date": date, "can_calc": True}

    # 概念名映射
    concept_names = {}
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT concept_code, concept_name FROM ths_concept_dict"):
            concept_names[row["concept_code"]] = row["concept_name"]

    # 当日个股涨幅（成分股排名用）
    daily_data = db.get_daily_kline_by_date(date)
    daily_df = pd.DataFrame(daily_data) if daily_data else pd.DataFrame()
    change_map = dict(zip(daily_df["code"], daily_df["change_ratio"])) if not daily_df.empty else {}

    # 成分股映射
    concept_codes = [r["concept_code"] for r in rankings]

    # 成分股名称映射
    stock_names = {}
    import sqlite3 as _sqlite3
    with _sqlite3.connect(db.db_path) as _conn:
        _conn.row_factory = _sqlite3.Row
        for _row in _conn.execute(
            "SELECT stock_code, stock_name FROM concept_members "
            "WHERE member_date = (SELECT MAX(member_date) FROM concept_members)"
        ):
            if _row["stock_code"] not in stock_names:
                stock_names[_row["stock_code"]] = _row["stock_name"]

    def _build_member_ranking(concept_code: str, limit: int = 10, reverse: bool = True):
        """
        历史模式：成分股按当日涨幅排序。
        :param reverse: True=降序(涨幅最大在前，给top板块)；False=升序(跌幅最深在前，给bottom板块)
        :return: (排序后的前 limit 只, 有当日涨幅数据的成分股总数)
        """
        members = db.get_concept_members(concept_code)
        member_changes = []
        for m in members:
            chg = change_map.get(m["stock_code"])
            if chg is not None and not pd.isna(chg):
                member_changes.append({
                    "code": m["stock_code"],
                    "name": stock_names.get(m["stock_code"], ""),
                    "change_ratio": round(float(chg), 2),
                    "speed": 0.0,
                    "body": 0.0,
                    "limit": 0,
                    "score": round(float(chg), 4),
                })
        member_changes.sort(key=lambda x: x["change_ratio"], reverse=reverse)
        return member_changes[:limit], len(member_changes)

    # Top 板块（含成分股，涨幅最大前10）
    top_rankings = rankings[:top_n]
    top_sectors = []
    for r in top_rankings:
        cc = r["concept_code"]
        top10, total_cnt = _build_member_ranking(cc, 10, reverse=True)
        top_sectors.append({
            "concept_code": cc,
            "concept_name": concept_names.get(cc, cc),
            "score": r.get("score_final", r.get("score_1d", 0)),
            "s1_return": r.get("s1_return", 0),
            "s2_breadth": r.get("s2_breadth", 0),
            "member_count": total_cnt,
            "members_top10": top10,
        })

    # Bottom 板块（含成分股，跌幅最深前10）
    bottom_rankings = rankings[-top_n:][::-1]
    bottom_sectors = []
    for r in bottom_rankings:
        cc = r["concept_code"]
        top10, total_cnt = _build_member_ranking(cc, 10, reverse=False)
        bottom_sectors.append({
            "concept_code": cc,
            "concept_name": concept_names.get(cc, cc),
            "score": r.get("score_final", r.get("score_1d", 0)),
            "s1_return": r.get("s1_return", 0),
            "s2_breadth": r.get("s2_breadth", 0),
            "member_count": total_cnt,
            "members_top10": top10,
        })

    # 市场统计
    if not daily_df.empty:
        chg_series = daily_df["change_ratio"].dropna()

        def _is_limit(code, chg):
            pure = code.split(".")[0]
            if code.endswith(".BJ"):
                return chg >= 29.0
            if pure.startswith(("300", "688")):
                return chg >= 19.5
            return chg >= 9.8

        market_stats = {
            "stock_count": int(len(chg_series)),
            "market_avg_change": round(float(chg_series.mean()), 2),
            "up_count": int((chg_series > 0).sum()),
            "down_count": int((chg_series < 0).sum()),
            "flat_count": int((chg_series == 0).sum()),
            "limit_up_count": int(sum(
                1 for code, chg in zip(daily_df["code"], daily_df["change_ratio"])
                if pd.notna(chg) and _is_limit(code, chg)
            )),
        }
    else:
        market_stats = {}

    return {
        "date": date,
        "market_stats": market_stats,
        "top_sectors": top_sectors,
        "bottom_sectors": bottom_sectors,
    }


@app.get("/api/dates")
def get_available_dates():
    """获取已入库的板块强度日期列表（供前端日期选择器）"""
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT calc_date FROM concept_strength ORDER BY calc_date DESC"
        ).fetchall()
    return {"dates": [r[0] for r in rows]}
