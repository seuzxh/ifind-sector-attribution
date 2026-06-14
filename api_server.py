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
    start_time: str = "09:30",
    end_time: str = None,
    top_n: int = 10,
):
    """
    实时看板：盘中实时拉取 1min K 线，算板块强度 + 成分股排名。
    带内存缓存（TTL 10秒），避免高频请求打爆 iFinD。

    :param trade_date: 交易日，默认今天
    :param start_time: 起始时间 HH:MM，默认 09:30
    :param end_time: 结束时间 HH:MM，默认当前时刻
    :param top_n: 返回前 N 个板块
    """
    from realtime_engine import get_realtime_dashboard as _fetch
    return _fetch(trade_date, start_time, end_time, use_cache=True)


@app.get("/api/history/dashboard")
def get_history_dashboard(date: str, top_n: int = 10):
    """
    历史看板：读取已入库的收盘数据（concept_strength + daily_kline）。
    成分股排名按当日涨幅（历史模式无1min，降级为纯涨幅）。

    :param date: 历史日期 YYYYMMDD
    :param top_n: 返回前 N 个板块
    """
    import sqlite3

    # 板块强度（读 concept_strength）
    rankings = db.get_sector_rankings(date, top_n=999)  # 取全部再切片
    if not rankings:
        return {"error": f"日期 {date} 无板块强度数据（请先运行 daily）", "date": date}

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

    def _build_member_ranking(concept_code: str, limit: int = 10):
        """历史模式：成分股按当日涨幅降序（无1min，纯涨幅）"""
        members = db.get_concept_members(concept_code)
        member_changes = []
        for m in members:
            chg = change_map.get(m["stock_code"])
            if chg is not None and not pd.isna(chg):
                member_changes.append({
                    "code": m["stock_code"],
                    "change_ratio": round(float(chg), 2),
                    "speed": 0.0,   # 历史模式无涨速
                    "body": 0.0,    # 历史模式无实体
                    "limit": 0,
                    "score": round(float(chg), 4),  # 历史模式 score = 涨幅
                })
        member_changes.sort(key=lambda x: x["change_ratio"], reverse=True)
        return member_changes[:limit]

    # Top 板块（含成分股）
    top_rankings = rankings[:top_n]
    top_sectors = []
    for r in top_rankings:
        cc = r["concept_code"]
        top_sectors.append({
            "concept_code": cc,
            "concept_name": concept_names.get(cc, cc),
            "score": r.get("score_final", r.get("score_1d", 0)),
            "s1_return": r.get("s1_return", 0),
            "s2_breadth": r.get("s2_breadth", 0),
            "member_count": r.get("member_count", 0),
            "members_top10": _build_member_ranking(cc, 10),
        })

    # Bottom 板块（不含成分股）
    bottom_rankings = rankings[-top_n:][::-1]
    bottom_sectors = [
        {
            "concept_code": r["concept_code"],
            "concept_name": concept_names.get(r["concept_code"], r["concept_code"]),
            "score": r.get("score_final", r.get("score_1d", 0)),
            "s1_return": r.get("s1_return", 0),
            "s2_breadth": r.get("s2_breadth", 0),
        }
        for r in bottom_rankings
    ]

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
