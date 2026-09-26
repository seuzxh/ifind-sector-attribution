# -*- coding: utf-8 -*-
"""
FastAPI 服务层（兼容入口）

实际实现已拆分至 api/ 包（app 组装 / deps 依赖 / schemas 模型 /
routers 按域路由 / history_service 与 kg_views 编排下沉）。
保留本模块：uvicorn "api_server:app" 与 `from api_server import app` 的既有引用不变。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api import app  # noqa: F401
