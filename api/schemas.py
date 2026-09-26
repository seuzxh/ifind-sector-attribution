# -*- coding: utf-8 -*-
"""请求模型"""

from typing import List, Optional

from pydantic import BaseModel


class StockAttributionRequest(BaseModel):
    stock_codes: List[str]
    date: Optional[str] = None


class PortfolioAttributionRequest(BaseModel):
    holdings: List[dict]
    date: Optional[str] = None


class WatchedSaveRequest(BaseModel):
    """保存监控板块勾选清单（全量覆盖）。"""
    concept_codes: List[str]
