# -*- coding: utf-8 -*-
"""
FastAPI 服务层
提供 REST API 接口供外部调用
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from database import Database
from core_calculator import calc_portfolio_attribution

app = FastAPI(
    title="行业归因与板块强度检测系统",
    description="基于 iFinD API 的量化行业归因与板块强度检测",
    version="1.0.0"
)

db = Database()


# ========== 请求模型 ==========
class StockAttributionRequest(BaseModel):
    stock_codes: List[str]
    date: Optional[str] = None


class PortfolioAttributionRequest(BaseModel):
    holdings: List[dict]
    date: Optional[str] = None


# ========== API 接口 ==========
@app.get("/")
def root():
    return {"message": "行业归因与板块强度检测系统", "version": "1.0.0"}


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
    :param date: 日期，默认最新
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    members = db.get_concept_members(concept_code, date)
    return {
        "concept_code": concept_code,
        "date": date,
        "count": len(members),
        "members": members
    }
