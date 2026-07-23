# -*- coding: utf-8 -*-
"""
FastAPI 服务层
提供 REST API 接口供外部调用
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from datetime import datetime
import json
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
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ========== 请求模型 ==========
class StockAttributionRequest(BaseModel):
    stock_codes: List[str]
    date: Optional[str] = None


class PortfolioAttributionRequest(BaseModel):
    holdings: List[dict]
    date: Optional[str] = None


class WatchedSaveRequest(BaseModel):
    """保存监控板块勾选清单（全量覆盖）。"""
    concept_codes: List[str]


# ========== API 接口 ==========
@app.get("/", response_class=HTMLResponse)
def root():
    """
    可视化看板入口：返回 Vue 3 SPA（static/index.html，Hash 路由）。
    所有看板/Tab 在前端切换。SPA 未构建时返回构建提示。
    注意：index.html 必须 no-cache（否则浏览器缓存旧入口，引用的旧 hash js
    在新 build 后 404，导致 SPA 白屏）。assets 文件本身带内容 hash 可长缓存。
    """
    from fastapi.responses import HTMLResponse as _HTML
    spa_path = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(spa_path):
        with open(spa_path, "r", encoding="utf-8") as f:
            content = f.read()
        return _HTML(content, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return ("<h1>前端未构建</h1>"
            "<p>运行 <code>cd frontend &amp;&amp; npm run build</code> 后刷新。</p>"
            "<!-- static/index.html 缺失：Vue SPA 未构建 -->")


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
):
    """
    实时看板（分时数据版）：拉取分时序列，按 snapshot_time 切片算板块强度 + 成分股排名。

    股票范围取「监控板块管理」勾选板块的全部去重成分股。
    分时序列在引擎内按日期和模式缓存，TTL 内不重拉网络。
    snapshot_time 切片纯内存（毫秒级），用于时间条拖动回看历史时刻。

    :param trade_date: 交易日 YYYYMMDD，默认今天（当日实时）；传历史日期则拉该日全天分时
    :param snapshot_time: 截止时刻 HH:MM（如 "09:50"），None 或 "latest" = 最新时刻
    :param top_n: 返回前/后 N 个板块
    """
    from realtime_engine import get_realtime_dashboard as _fetch
    return _fetch(
        trade_date=trade_date,
        snapshot_time=snapshot_time,
        top_n=top_n,
    )


@app.post("/api/realtime/clear_cache")
def clear_realtime_cache():
    """清空分时序列缓存（切日/调试用）。"""
    from realtime_engine import clear_cache
    clear_cache()
    return {"ok": True}


@app.get("/api/auction/dashboard")
def get_auction_dashboard(trade_date: str = None, snapshot_time: str = None):
    """
    集合竞价选股选分组看板（9:20~9:25 不可撤单窗口）。

    观察池：自选股分组全部去重个股。4 因子综合分：高开/爆量/挂单失衡/价格趋势。
    :param trade_date: 交易日 YYYYMMDD，None=今天（当日实时）；传历史日期回看该日竞价
    :param snapshot_time: 截止时刻 HH:MM（如 "09:25"），None=取 pre_market 末点
    """
    from auction_engine import compute_auction_dashboard
    return compute_auction_dashboard(trade_date=trade_date, snapshot_time=snapshot_time)


@app.post("/api/auction/clear_cache")
def clear_auction_cache():
    """清空竞价看板缓存。"""
    from auction_engine import clear_cache
    clear_cache()
    return {"ok": True}


@app.get("/api/custom/dashboard")
def get_custom_dashboard(
    trade_date: str = None,
    snapshot_time: str = None,
    top_n: int = 10,
):
    """
    自选股分组看板：用 custom_group 表的自选分组替代概念板块，算分组强弱 + 成分股排名。
    复用 realtime_engine 的分时序列缓存与切片逻辑，仅 members_map 来源不同。

    :param trade_date: 交易日 YYYYMMDD，默认今天；传历史日期则拉该日全天分时
    :param snapshot_time: 截止时刻 HH:MM（如 "09:50"），None 或 "latest" = 最新
    :param top_n: 返回前/后 N 个分组
    """
    from realtime_engine import get_realtime_dashboard as _fetch
    return _fetch(
        trade_date=trade_date,
        snapshot_time=snapshot_time,
        top_n=top_n,
        custom_mode=True,
    )


@app.get("/api/custom/scan")
def get_custom_scan(query: str):
    """
    自选股强势归类：用 iFinD MCP search_stocks 自然语言选股，
    取与自选分组股票的交集，再按自选分组归类统计。

    与 /api/market/scan 的差异：命中股限定在自选分组范围内（取交集），
    归类维度是自选分组（custom_group 表），非 884 概念板块。

    :param query: 自然语言选股条件（如 "涨幅大于7%并且小于12.1%；未涨停；非ST"）
    """
    from realtime_engine import scan_custom_groups as _scan
    return _scan(query=query)


@app.get("/api/market/scan")
def get_market_scan(query: str):
    """
    全市场强势股概念板块归类：用 iFinD MCP search_stocks 自然语言选股，
    把选出的股票按「监控板块管理」当前勾选板块归类统计。

    与 /api/custom/scan 的差异：命中股来自 MCP 选股（收盘数据），归类维度是
    管理页勾选概念板块，不依赖分时序列。选股约 4.5s，归类纯内存。

    :param query: 自然语言选股条件（如 "涨幅大于7%并且小于12.1%；未涨停；非ST"）
    """
    from realtime_engine import scan_market_groups as _scan
    return _scan(query=query)


# 记录上次导入自选分组时的 JSON mtime（None=服务启动后尚未导入过）
_custom_groups_mtime = None


@app.post("/api/custom/check_reload")
def custom_check_reload():
    """
    检查自选股分组 JSON 是否变更，变了就全量重导（前端切到自选 Tab 时调用）。
    判定依据：JSON 文件的 mtime 与上次导入时不同 → 重导。
    首次（服务启动后未导入过）也会触发一次，确保表里有数据。

    :return: {reloaded: bool, reason: str, ...stats（重导时）}
    """
    global _custom_groups_mtime
    import os
    from main import import_groups_from_json, CUSTOM_GROUPS_JSON

    if not os.path.exists(CUSTOM_GROUPS_JSON):
        return {"reloaded": False, "reason": "JSON 文件不存在", "json_path": CUSTOM_GROUPS_JSON}

    cur_mtime = os.path.getmtime(CUSTOM_GROUPS_JSON)
    if _custom_groups_mtime is not None and cur_mtime == _custom_groups_mtime:
        return {"reloaded": False, "reason": "JSON 未变更", "json_path": CUSTOM_GROUPS_JSON}

    # mtime 变了（或首次）→ 全量重导
    result = import_groups_from_json(CUSTOM_GROUPS_JSON)
    if result is None:
        return {"reloaded": False, "reason": "导入失败（JSON 解析错误）", "json_path": CUSTOM_GROUPS_JSON}

    _custom_groups_mtime = cur_mtime
    print(f"[CUSTOM-RELOAD] 检测到 JSON 变更，已重导：{result['group_count']} 分组 / {result['stock_count']} 只股票")
    # 重导后清掉旧的分时序列缓存（股票范围可能变了）
    try:
        from realtime_engine import clear_cache
        clear_cache()
    except Exception:
        pass
    try:
        from auction_engine import clear_cache as clear_auction_cache
        clear_auction_cache()
    except Exception:
        pass
    return {"reloaded": True, "reason": "JSON 变更，已全量重导", **result}


# ========== 交易日历 / 交易时段（服务前端盘前判断与日期选择器）==========
@app.get("/api/trade_calendar")
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


@app.get("/api/session_status")
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


@app.get("/api/history/dashboard")
def get_history_dashboard(
    date: str,
    top_n: int = 10,
    force_calc: bool = False,
    scope: str = "sector",
):
    """
    历史看板：读取已入库的收盘数据（concept_strength + daily_kline）。
    成分股排名按当日涨幅（历史模式无1min，降级为纯涨幅）。

    :param date: 历史日期 YYYYMMDD
    :param top_n: 返回前 N 个板块
    :param force_calc: 无数据时是否自动拉取并计算（耗时约2分钟）
    :param scope: sector=当前勾选监控板块；custom=自选分组
    """
    import sqlite3

    if scope not in ("sector", "custom"):
        return {"error": f"不支持的历史范围：{scope}", "date": date}

    watched_codes = set(db.get_watched_concept_codes())
    rankings = []
    if scope == "sector":
        # 历史表可能含旧监控范围，读取时必须再次按当前勾选集收口。
        rankings = [
            r for r in db.get_sector_rankings(date, top_n=999)
            if r["concept_code"] in watched_codes
        ]

    daily_data = db.get_daily_kline_by_date(date)
    needs_history_data = (
        (scope == "sector" and not rankings)
        or (scope == "custom" and not daily_data)
    )
    if needs_history_data:
        # force_calc：板块缺评分时重新计算；自选缺个股日K时按需拉取。
        if force_calc:
            from sync_pipeline import SyncPipeline
            # 校验日期格式
            try:
                datetime.strptime(date, "%Y%m%d")
            except ValueError:
                return {"error": f"日期格式错误，需 YYYYMMDD：{date}", "date": date}
            try:
                pipeline = SyncPipeline()
                if not daily_data:
                    stock_codes = (
                        pipeline.db.get_custom_all_stock_codes()
                        if scope == "custom"
                        else pipeline.db.get_all_member_stock_codes()
                    )
                    pipeline.sync_daily_kline(stock_codes, date, date)
                if scope == "sector":
                    pipeline.calc_daily_strength(date)
                    pipeline.calc_daily_attribution(date)
                    rankings = [
                        r for r in db.get_sector_rankings(date, top_n=999)
                        if r["concept_code"] in watched_codes
                    ]
                daily_data = db.get_daily_kline_by_date(date)
            except Exception as e:
                return {"error": f"计算失败：{e}", "date": date}
            if (scope == "sector" and not rankings) or not daily_data:
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
    daily_df = pd.DataFrame(daily_data) if daily_data else pd.DataFrame()
    change_map = dict(zip(daily_df["code"], daily_df["change_ratio"])) if not daily_df.empty else {}

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

    if scope == "custom":
        members_map = db.get_custom_members_map()
        group_names = db.get_custom_group_names()
        rankings = []
        for gid, codes in members_map.items():
            changes = [
                float(change_map[c]) for c in codes
                if c in change_map and not pd.isna(change_map[c])
            ]
            if not changes:
                continue
            rankings.append({
                "concept_code": gid,
                "concept_name": group_names.get(gid, gid),
                "score_final": sum(changes) / len(changes),
                "s1_return": sum(changes) / len(changes),
                "s2_breadth": sum(1 for v in changes if v > 0) / len(changes),
            })
        rankings.sort(key=lambda r: r["score_final"], reverse=True)

    def _group_members(group_code: str):
        if scope == "custom":
            return [{"stock_code": c} for c in db.get_custom_members_map().get(group_code, [])]
        return db.get_concept_members(group_code)

    def _build_member_ranking(concept_code: str, limit: int = 10, reverse: bool = True):
        """
        历史模式：成分股按当日涨幅排序。
        :param reverse: True=降序(涨幅最大在前，给top板块)；False=升序(跌幅最深在前，给bottom板块)
        :return: (排序后的前 limit 只, 有当日涨幅数据的成分股总数)
        """
        members = _group_members(concept_code)
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
    main_rankings = rankings
    zt_rankings = []
    if scope == "custom":
        zt_rankings = [
            r for r in rankings if r["concept_name"].strip().upper().startswith("ZT")
        ]
        main_rankings = [
            r for r in rankings if not r["concept_name"].strip().upper().startswith("ZT")
        ]

    top_rankings = main_rankings[:top_n]
    top_sectors = []
    for r in top_rankings:
        cc = r["concept_code"]
        top10, total_cnt = _build_member_ranking(cc, 10, reverse=True)
        top_sectors.append({
            "concept_code": cc,
            "concept_name": r.get("concept_name") or concept_names.get(cc, cc),
            "score": r.get("score_final", r.get("score_1d", 0)),
            "s1_return": r.get("s1_return", 0),
            "s2_breadth": r.get("s2_breadth", 0),
            "member_count": total_cnt,
            "members_top10": top10,
        })

    # Bottom 板块（含成分股，跌幅最深前10）
    bottom_rankings = main_rankings[-top_n:][::-1]
    bottom_sectors = []
    for r in bottom_rankings:
        cc = r["concept_code"]
        top10, total_cnt = _build_member_ranking(cc, 10, reverse=False)
        bottom_sectors.append({
            "concept_code": cc,
            "concept_name": r.get("concept_name") or concept_names.get(cc, cc),
            "score": r.get("score_final", r.get("score_1d", 0)),
            "s1_return": r.get("s1_return", 0),
            "s2_breadth": r.get("s2_breadth", 0),
            "member_count": total_cnt,
            "members_top10": top10,
        })

    zt_sectors = []
    for r in zt_rankings:
        cc = r["concept_code"]
        top10, total_cnt = _build_member_ranking(cc, 10, reverse=True)
        zt_sectors.append({
            "concept_code": cc,
            "concept_name": r["concept_name"],
            "score": r["score_final"],
            "s1_return": r["s1_return"],
            "s2_breadth": r["s2_breadth"],
            "member_count": total_cnt,
            "members_top10": top10,
        })

    # 统计口径与当前页面范围一致，而不是整张 daily_kline 表。
    if not daily_df.empty:
        if scope == "custom":
            scope_codes = set(db.get_custom_all_stock_codes())
        else:
            scope_codes = set()
            for cc in watched_codes:
                scope_codes.update(m["stock_code"] for m in db.get_concept_members(cc))
        scoped_df = daily_df[daily_df["code"].isin(scope_codes)]
        chg_series = scoped_df["change_ratio"].dropna()

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
                1 for code, chg in zip(scoped_df["code"], scoped_df["change_ratio"])
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
        "zt_sectors": zt_sectors,
        "scope": scope,
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


# ========== 监控板块管理 ==========
@app.get("/api/sector_manage/list")
def sector_manage_list(date: str = None):
    """
    监控板块管理列表：全部候选板块（观察池全集）+ iFinD 实时行情 + 是否已勾选监控。
    行情数据直接从 iFinD 接口3（概念指数日K）获取，不依赖本地 daily_kline。
    :param date: 保留兼容，实际行情由 iFinD 实时返回
    :return: {date, total, watched_count, sectors: [{concept_code, concept_name, level,
              change_ratio, body, return_3d, return_5d, member_count, watched}]}
    """
    from sector_manage import compute_sector_quotes_from_ifind
    from datetime import datetime
    rows = compute_sector_quotes_from_ifind(db)
    return {
        "date": date or datetime.now().strftime("%Y%m%d"),
        "total": len(rows),
        "watched_count": sum(1 for r in rows if r.get("watched")),
        "sectors": rows,
    }


@app.get("/api/sector_manage/watched")
def sector_manage_watched():
    """读取当前勾选的监控板块代码列表（轻量，供其他页校验）。"""
    codes = db.get_watched_concept_codes()
    return {"count": len(codes), "concept_codes": codes}


@app.post("/api/sector_manage/save")
def sector_manage_save(req: WatchedSaveRequest):
    """
    全量覆盖监控板块勾选清单。保存后立即生效（各链路下次查询即读取新清单）。
    :return: {ok, saved_count}
    """
    db.save_watched_concepts(req.concept_codes)
    from realtime_engine import clear_cache
    clear_cache()
    return {"ok": True, "saved_count": len(req.concept_codes)}


# 刷新板块信息的后台任务状态（全局，进程内）
_refresh_state = {
    "running": False,
    "done": False,
    "error": None,
    "result": None,
    "started_at": None,
    "finished_at": None,
}


def _run_refresh_background():
    """后台线程：刷新板块字典+成分股，完成后清看板缓存。"""
    from sync_pipeline import SyncPipeline
    from realtime_engine import clear_cache
    from datetime import datetime
    global _refresh_state
    try:
        pipeline = SyncPipeline()
        result = pipeline.refresh_observe_members()
        # 刷新成功后清看板缓存（engine 单例会在 clear_cache 内重置）
        try:
            clear_cache()
        except Exception as e:
            print(f"[REFRESH] clear_cache 失败（不影响数据）: {e}")
        _refresh_state.update({
            "running": False, "done": True, "error": None, "result": result,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        })
        print(f"[REFRESH] 后台刷新完成：{result}")
    except Exception as e:
        _refresh_state.update({
            "running": False, "done": True, "error": str(e), "result": None,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        })
        print(f"[REFRESH] 后台刷新失败：{e}")


@app.post("/api/sector_manage/refresh")
def sector_manage_refresh():
    """
    触发刷新：后台线程重新从 iFinD 拉取最新板块字典 + 成分股（约 1-2 分钟）。
    立即返回，前端轮询 /api/sector_manage/refresh/status 查进度。
    不可重入：已在刷新中则返回 running 状态。
    """
    import threading
    from datetime import datetime
    global _refresh_state
    if _refresh_state["running"]:
        return {"ok": False, "reason": "已在刷新中", "status": _refresh_state}
    _refresh_state.update({
        "running": True, "done": False, "error": None, "result": None,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
    })
    t = threading.Thread(target=_run_refresh_background, daemon=True)
    t.start()
    return {"ok": True, "reason": "刷新已启动", "status": _refresh_state}


@app.get("/api/sector_manage/refresh/status")
def sector_manage_refresh_status():
    """查询刷新进度（前端轮询用）。"""
    return _refresh_state


# ========== 板块轮动分析智能体 ==========
@app.get("/api/rotation/analyze")
def rotation_analyze():
    """
    板块轮动分析智能体（SSE 流式）。
    三阶段 loop engine：数据采集 → 第一性原理分析 → 对抗审查 → 综合结论。
    全程流式推送进度文本给前端。
    """
    from rotation_agent import get_rotation_agent

    def event_stream():
        try:
            agent = get_rotation_agent()
            for chunk in agent.analyze_rotation():
                yield f"data: {json.dumps({'type': 'delta', 'text': chunk}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
