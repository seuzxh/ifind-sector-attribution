# -*- coding: utf-8 -*-
"""
iFinD MCP 代理模块

封装对两个同花顺 MCP server 的 JSON-RPC 调用，作为网页「AI 问答」功能的底座。
- hexin-ifind-ds-stock-mcp：A股股票数据（摘要/选股/行情/基本面/股东/财务/风险/事件/ESG/高频）
- hexin-ifind-ds-index-mcp：指数与板块数据（指数指标/板块指标/高频行情）

调用约定（已实测）：
- POST JSON-RPC 2.0，header 带 Authorization: <jwt>
- Accept: application/json, text/event-stream
- 返回普通 JSON（非 SSE）；stateless 调用无需先 initialize / 无需 session id

线程安全：单例 MCPClient.instance()，list_tools 结果带内存缓存（TOOL_CACHE_TTL）。
Token 从 config.IFIND_MCP_TOKEN 读取（config_local.py 注入，不入库）。
"""

import threading
import time
from typing import Any, Dict, List, Optional

import requests

import config

# 两个 MCP server 的端点（URL 非敏感，写死）
MCP_SERVERS: Dict[str, str] = {
    "stock": "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-stock-mcp",
    "index": "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-index-mcp",
}

# 工具元数据缓存有效期（秒）：启动后拉一次，避免每次请求都打 MCP
TOOL_CACHE_TTL = 300
# 单次 JSON-RPC 请求超时（秒）：tools/list 快，tools/call 可能跑数据查询，给宽点
_REQUEST_TIMEOUT = 60

# MCP 协议版本（initialize 用，stateless 调用不强依赖，但带上更规范）
_PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    """MCP 调用异常（含 JSON-RPC error 或网络/解析错误）。"""


class MCPClient:
    """
    MCP server 客户端单例。

    用法：
        client = MCPClient.instance()
        tools = client.list_tools()                 # 所有 server 的工具合集
        result = client.call_tool("stock",          # 直接调一个工具
                                  "get_stock_summary",
                                  {"query": "同花顺最新估值水平"})
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        # 工具缓存：{server_key: {"tools": [...], "fetched_at": ts}}
        self._tool_cache: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def instance(cls) -> "MCPClient":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ---------- 内部：发一次 JSON-RPC ----------
    def _rpc(self, server: str, method: str, params: Optional[dict] = None) -> dict:
        """
        向指定 server 发 JSON-RPC 请求，返回 result 字段（解析后的 dict）。
        :raises MCPError: server 未知 / token 缺失 / 网络/HTTP 错误 / JSON-RPC error
        """
        if server not in MCP_SERVERS:
            raise MCPError(f"未知 MCP server: {server}（可选: {list(MCP_SERVERS)}）")
        token = getattr(config, "IFIND_MCP_TOKEN", "") or ""
        if not token:
            raise MCPError("未配置 IFIND_MCP_TOKEN（请在 config_local.py 设置）")

        url = MCP_SERVERS[server]
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise MCPError(f"MCP 网络请求失败（{server}）: {e}") from e

        if r.status_code != 200:
            raise MCPError(f"MCP HTTP {r.status_code}（{server}）: {r.text[:300]}")

        try:
            data = r.json()
        except ValueError as e:
            raise MCPError(f"MCP 响应非 JSON（{server}）: {r.text[:300]}") from e

        if "error" in data and data["error"]:
            err = data["error"]
            raise MCPError(f"MCP JSON-RPC 错误（{server}）: {err}")
        if "result" not in data:
            raise MCPError(f"MCP 响应无 result 字段（{server}）: {data}")
        return data["result"]

    # ---------- 公共 API ----------
    def list_tools(self, server: Optional[str] = None, use_cache: bool = True) -> List[Dict[str, Any]]:
        """
        列出工具元数据（name/description/inputSchema）。
        :param server: 指定 server；None=返回所有 server 工具的合集（每项带额外 server 字段）
        :param use_cache: 是否用缓存（TTL 内不重拉）
        :return: [{name, description, inputSchema, server}, ...]
        """
        targets = [server] if server else list(MCP_SERVERS)
        out: List[Dict[str, Any]] = []
        with self._lock:
            for srv in targets:
                cached = self._tool_cache.get(srv)
                now = time.time()
                if use_cache and cached and (now - cached["fetched_at"]) < TOOL_CACHE_TTL:
                    tools = cached["tools"]
                else:
                    result = self._rpc(srv, "tools/list")
                    tools = result.get("tools", [])
                    self._tool_cache[srv] = {"tools": tools, "fetched_at": now}
                # 给每个工具打上来源 server 标记，便于前端分组展示与 LLM 选工具
                for t in tools:
                    item = dict(t)
                    item["server"] = srv
                    out.append(item)
        return out

    def clear_cache(self) -> None:
        """清掉工具元数据缓存（token 换发或调试时用）。"""
        with self._lock:
            self._tool_cache.clear()

    def call_tool(self, server: str, tool_name: str, arguments: Optional[dict] = None) -> str:
        """
        调用指定 server 的一个工具，返回拼接后的文本结果。
        MCP 工具结果格式：result.content = [{type:"text", text:"..."}, ...]
        本方法把所有 text 段拼成一个字符串返回（业务层大多是单段 Markdown）。

        :param server: "stock" 或 "index"
        :param tool_name: 如 "get_stock_summary"
        :param arguments: 工具入参，如 {"query": "同花顺最新估值水平"}
        :return: 工具返回的文本（通常是 Markdown 表格/JSON 文本）
        :raises MCPError: 调用失败
        """
        result = self._rpc(server, "tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })
        content = result.get("content", [])
        if not isinstance(content, list):
            return str(result)
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(t for t in texts if t)


# 模块级便捷函数
def list_tools(server: Optional[str] = None, use_cache: bool = True) -> List[Dict[str, Any]]:
    return MCPClient.instance().list_tools(server=server, use_cache=use_cache)


def call_tool(server: str, tool_name: str, arguments: Optional[dict] = None) -> str:
    return MCPClient.instance().call_tool(server, tool_name, arguments)


def is_configured() -> bool:
    """MCP token 是否已配置（供 API 层判断可用性）。"""
    return bool(getattr(config, "IFIND_MCP_TOKEN", ""))
