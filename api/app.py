# -*- coding: utf-8 -*-
"""FastAPI 组装：app 实例、静态托管、SPA 入口、路由注册"""

import os

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .routers import history, kg, opening_strength, overview, realtime, sector_manage

app = FastAPI(
    title="行业归因与板块强度检测系统",
    description="基于 iFinD API 的量化行业归因与板块强度检测",
    version="1.0.0"
)


@app.exception_handler(RequestValidationError)
async def _request_validation_error(request: Request, error: RequestValidationError):
    if request.url.path == "/api/opening-strength/dashboard":
        return opening_strength.invalid_request_response()
    return await request_validation_exception_handler(request, error)


# 挂载静态文件目录（前端页面资源）
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# 静态资源缓存策略：带内容 hash 的 assets 可永久缓存（immutable），
# 其余 static 文件（favicon 等）与 SPA 入口交给根路由的 no-cache。
@app.middleware("http")
async def _static_cache_control(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/assets/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.get("/", response_class=HTMLResponse)
def root():
    """
    可视化看板入口：返回 Vue 3 SPA（static/index.html，Hash 路由）。
    所有看板/Tab 在前端切换。SPA 未构建时返回构建提示。
    注意：index.html 必须 no-cache（否则浏览器缓存旧入口，引用的旧 hash js
    在新 build 后 404，导致 SPA 白屏）。assets 文件本身带内容 hash 可长缓存。
    """
    spa_path = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(spa_path):
        with open(spa_path, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return ("<h1>前端未构建</h1>"
            "<p>运行 <code>cd frontend &amp;&amp; npm run build</code> 后刷新。</p>"
            "<!-- static/index.html 缺失：Vue SPA 未构建 -->")


app.include_router(overview.router)
app.include_router(realtime.router)
app.include_router(history.router)
app.include_router(sector_manage.router)
app.include_router(kg.router)
app.include_router(opening_strength.router)
