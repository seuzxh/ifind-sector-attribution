# -*- coding: utf-8 -*-
"""监控板块管理域路由：候选列表 / 勾选读写 / 字典后台刷新"""

from datetime import datetime

from fastapi import APIRouter

from api.deps import db
from api.schemas import WatchedSaveRequest

router = APIRouter()


@router.get("/api/sector_manage/list")
def sector_manage_list(date: str = None):
    """
    监控板块管理列表：观察池内成分股数 10~500 的候选板块
    + iFinD 实时行情 + 是否已勾选监控。
    行情数据直接从 iFinD 接口3（概念指数日K）获取，不依赖本地 daily_kline。
    :param date: 保留兼容，实际行情由 iFinD 实时返回
    :return: {date, total, watched_count, sectors: [{concept_code, concept_name, level,
              change_ratio, body, return_3d, return_5d, member_count, watched}]}
    """
    from sector_manage import compute_sector_quotes_from_ifind
    rows = compute_sector_quotes_from_ifind(db)
    return {
        "date": date or datetime.now().strftime("%Y%m%d"),
        "total": len(rows),
        "watched_count": sum(1 for r in rows if r.get("watched")),
        "sectors": rows,
    }


@router.get("/api/sector_manage/watched")
def sector_manage_watched():
    """读取当前勾选的监控板块代码列表（轻量，供其他页校验）。"""
    codes = db.get_watched_concept_codes()
    return {"count": len(codes), "concept_codes": codes}


@router.post("/api/sector_manage/save")
def sector_manage_save(req: WatchedSaveRequest):
    """
    全量覆盖监控板块勾选清单。保存后立即生效（各链路下次查询即读取新清单）。
    成分股数量不在配置范围内的代码会被自动剔除。
    :return: {ok, saved_count, excluded_count}
    """
    saved_codes = db.save_watched_concepts(req.concept_codes)
    from realtime_engine import clear_cache
    clear_cache()
    return {
        "ok": True,
        "saved_count": len(saved_codes),
        "excluded_count": len(set(req.concept_codes) - set(saved_codes)),
    }


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


@router.post("/api/sector_manage/refresh")
def sector_manage_refresh():
    """
    触发刷新：后台线程重新从 iFinD 拉取最新板块字典 + 成分股（约 1-2 分钟）。
    立即返回，前端轮询 /api/sector_manage/refresh/status 查进度。
    不可重入：已在刷新中则返回 running 状态。
    """
    import threading
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


@router.get("/api/sector_manage/refresh/status")
def sector_manage_refresh_status():
    """查询刷新进度（前端轮询用）。"""
    return _refresh_state
