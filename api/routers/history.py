# -*- coding: utf-8 -*-
"""历史域路由：历史看板（薄壳，编排在 api.history_service）"""

from fastapi import APIRouter

from api.deps import db
from api.history_service import build_history_dashboard

router = APIRouter()


@router.get("/api/history/dashboard")
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
    return build_history_dashboard(db, date, top_n=top_n, force_calc=force_calc, scope=scope)
