# -*- coding: utf-8 -*-
"""
FastAPI 接口层（按域拆分为包）

结构：
- api/app.py            FastAPI 组装（app 实例 / 静态托管 / SPA 入口 / include routers）
- api/deps.py           共享依赖（db 单例）
- api/schemas.py        请求模型
- api/routers/          按域路由（overview / realtime / history / sector_manage / kg）
- api/history_service.py 历史看板编排（从原 220 行 handler 下沉）
- api/kg_views.py       知识图谱查询组装与 cytoscape 图供数（从原 handler 下沉）

对外入口不变：`from api_server import app`（uvicorn "api_server:app" 兼容）。
"""

from .app import app

__all__ = ["app"]
