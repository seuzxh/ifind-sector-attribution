# -*- coding: utf-8 -*-
"""实时域路由：实时/竞价/自选看板、成分股排序、选股归类、题材催化、自选重导检查"""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter

from api.deps import db

router = APIRouter()


# ========== 可视化看板路由 ==========

@router.get("/api/realtime/dashboard")
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


@router.post("/api/realtime/clear_cache")
def clear_realtime_cache():
    """清空分时序列缓存（切日/调试用）。"""
    from realtime_engine import clear_cache
    clear_cache()
    return {"ok": True}


@router.get("/api/auction/dashboard")
def get_auction_dashboard(trade_date: str = None, snapshot_time: str = None):
    """
    集合竞价选股选分组看板（9:20~9:25 不可撤单窗口）。

    观察池：自选股分组全部去重个股。4 因子综合分：高开/爆量/挂单失衡/价格趋势。
    :param trade_date: 交易日 YYYYMMDD，None=今天（当日实时）；传历史日期回看该日竞价
    :param snapshot_time: 截止时刻 HH:MM（如 "09:25"），None=取 pre_market 末点
    """
    from auction_engine import compute_auction_dashboard
    return compute_auction_dashboard(trade_date=trade_date, snapshot_time=snapshot_time)


@router.post("/api/auction/clear_cache")
def clear_auction_cache():
    """清空竞价看板缓存。"""
    from auction_engine import clear_cache
    clear_cache()
    return {"ok": True}


@router.get("/api/custom/dashboard")
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


@router.get("/api/dashboard/members")
def get_dashboard_members(
    concept_code: str,
    trade_date: str = None,
    snapshot_time: str = None,
    custom_mode: bool = False,
    sort_key: str = "score",
    descending: bool = True,
):
    """
    对指定板块/自选分组的全部有效成分股按字段排序，只返回前 10 支。

    页面点击成分股表头时调用，避免把所有板块的全量成员塞进 3 秒轮询响应。
    """
    from realtime_engine import get_realtime_member_ranking as _rank
    return _rank(
        concept_code=concept_code,
        trade_date=trade_date,
        snapshot_time=snapshot_time,
        custom_mode=custom_mode,
        sort_key=sort_key,
        descending=descending,
        limit=10,
    )


@router.get("/api/custom/scan")
def get_custom_scan(query: str):
    """
    自选股强势归类：用 REST smart_stock_picking 自然语言选股，
    取与自选分组股票的交集，再按自选分组归类统计。

    与 /api/market/scan 的差异：命中股限定在自选分组范围内（取交集），
    归类维度是自选分组（custom_group 表），非 884 概念板块。

    :param query: 自然语言选股条件（如 "涨幅大于7%并且小于12.1%；未涨停；非ST"）
    """
    from realtime_engine import scan_custom_groups as _scan
    return _scan(query=query)


@router.get("/api/market/scan")
def get_market_scan(query: str, order: str = "lift", min_hits: int = 2, top_n: int = 30):
    """
    全市场强势股板块归类：用 REST smart_stock_picking 自然语言选股，
    按知识图谱富集归类（全量 650 板块，富集倍数/命中数双指标，每股带 ρ）。
    「监控板块管理」勾选的板块带 is_watched=true。

    与 /api/custom/scan 的差异：命中股来自 REST smart_stock_picking 选股（收盘数据），归类维度是
    图谱板块，不依赖分时序列。选股约 4.5s，归类纯内存。

    :param query: 自然语言选股条件（如 "涨幅大于7%并且小于12.1%；未涨停；非ST"）
    :param order: lift=富集倍数降序（默认）| hits=命中数降序
    :param min_hits: 板块最少命中数（过滤散点噪音）
    :param top_n: 返回板块数上限
    """
    from realtime_engine import scan_market_groups as _scan
    return _scan(query=query, order=order, min_hits=min_hits, top_n=top_n)


# 题材催化反向归因（引擎懒加载 + 结果按 (日期,池) 缓存；首跑 15~30s）
_theme_engine = None
_theme_cache: Dict[tuple, Any] = {}


@router.get("/api/theme/attribution")
def theme_attribution(date: Optional[str] = None, pool: str = "style_indices"):
    """
    题材催化反向归因（方案书 v1.1 首版：无资讯、无快照接口，个股因子全部
    由 1min/分时推导；涨跌停按主板(ST 5%)/科创创业 20%/北交所 30% 规则；
    股池剔除 ST/停牌/收盘价<5元）。

    :param date: YYYYMMDD，缺省=当日（当日池内成分与横截面走 smart_pick）
    :param pool: style_indices（三指数成分）| smart:<自然语言> | codes:<逗号代码>
    """
    global _theme_engine
    day = (date or "").replace("-", "") or datetime.now().strftime("%Y%m%d")
    key = (day, pool)
    if key in _theme_cache:
        return _theme_cache[key]
    from theme_catalyst import ThemeCatalystEngine, run_pipeline
    if _theme_engine is None:
        _theme_engine = ThemeCatalystEngine()
    result = run_pipeline(day, pool, engine=_theme_engine)
    if "error" not in result:
        if len(_theme_cache) > 64:
            _theme_cache.clear()
        _theme_cache[key] = result
    return result


# 记录上次导入自选分组时的 JSON mtime（None=服务启动后尚未导入过）
_custom_groups_mtime = None


@router.post("/api/custom/check_reload")
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
