# -*- coding: utf-8 -*-
"""总览域路由：板块排名 / 归因 / 概念字典 / 日期与交易日历 / 交易时段"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter

from core_calculator import calc_portfolio_attribution
from api.deps import db
from api.schemas import PortfolioAttributionRequest, StockAttributionRequest

router = APIRouter()


@router.get("/api/sector/rankings")
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


@router.post("/api/attribution/stock")
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


@router.post("/api/attribution/portfolio")
def get_portfolio_attribution(req: PortfolioAttributionRequest):
    """
    组合归因 + 强势板块定位
    :param req: holdings + date
    """
    date = req.date or datetime.now().strftime("%Y%m%d")
    result = calc_portfolio_attribution(db, req.holdings, date)
    return result


@router.get("/api/realtime/sector")
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


@router.get("/api/concept/list")
def get_concept_list():
    """获取全部概念板块列表"""
    codes = db.get_all_concept_codes()
    return {
        "count": len(codes),
        "concepts": codes
    }


@router.get("/api/concept/members")
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


@router.get("/api/dates")
def get_available_dates():
    """获取已入库的板块强度日期列表（供前端日期选择器）"""
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT calc_date FROM concept_strength ORDER BY calc_date DESC"
        ).fetchall()
    return {"dates": [r[0] for r in rows]}


# ========== 交易日历 / 交易时段（服务前端盘前判断与日期选择器）==========
@router.get("/api/trade_calendar")
def get_trade_calendar(year: Optional[int] = None):
    """
    返回交易日列表（YYYYMMDD）。
    :param year: 指定年份；不传则返回近 3 年全部（供前端日期选择器过滤非交易日）
    :return today: 服务端权威当前日期 YYYYMMDD（前端据此设默认日期，避免浏览器时区与服务器不一致）
    """
    from trade_calendar import TradeCalendar
    cal = TradeCalendar.instance()
    days = cal.get_trade_days(year=year)
    return {
        "count": len(days),
        "trade_days": days,
        # 服务端权威"今天"：前端用此（而非浏览器 new Date()）决定默认日期，
        # 避免浏览器与服务端时区不一致（如 UTC 浏览器 vs 北京服务器）导致默认日期错成 T-1。
        "today": datetime.now().strftime("%Y%m%d"),
    }


@router.get("/api/session_status")
def get_session_status():
    """
    返回当前交易时段状态（前端据此决定是否启动 3s 轮询）。
    :return is_trading_day: 今天是否交易日
    :return phase: pre_open/auction/pre_morning/morning/lunch/afternoon/closed
    :return next_open_time: 下一个有数据时刻 HH:MM（当前已有数据则 null）
    :return next_trade_day: 下一个交易日 YYYYMMDD（当前已收盘则用）
    :return now: 服务器当前时间 HH:MM:SS
    """
    from trade_calendar import TradeCalendar
    cal = TradeCalendar.instance()
    now = datetime.now()
    phase = cal.session_phase(now)
    next_open = cal.next_open_time(now)
    # next_trade_day：仅在"需要等下一交易日"时返回（盘前/非交易日/收盘后）。
    # 收盘后或非交易日，"下一交易日"应严格 > 今天（用明天作为查询起点），
    # 否则 next_trade_day(今天) 会返回今天自己。
    today = now.strftime("%Y%m%d")
    if next_open and (phase in ("pre_open", "closed") or not cal.is_trading_day(today)):
        from datetime import timedelta
        tomorrow = (now + timedelta(days=1)).strftime("%Y%m%d")
        next_trade_day = cal.next_trade_day(tomorrow)
    else:
        next_trade_day = None
    return {
        "is_trading_day": cal.is_trading_day(now.strftime("%Y%m%d")),
        "phase": phase,
        "next_open_time": next_open,
        "next_trade_day": next_trade_day,
        "now": now.strftime("%H:%M:%S"),
    }
