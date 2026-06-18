# -*- coding: utf-8 -*-
"""
LLM Agent 模块（网页「AI 问答」的大脑）

职责：把用户的自然语言问题，结合 iFinD MCP 工具集，自动决策调用哪个工具、
执行后把结果整理成自然语言回答，SSE 流式输出。

接入方式（OpenAI 兼容接口，支持 function/tool calling）：
- DeepSeek（默认）、通义千问、智谱 GLM 等都兼容此协议，改 config.LLM_BASE_URL/LLM_MODEL 即可。

启用：在 config_local.py 配置 LLM_API_KEY 即启用全自动模式；
      留空则 get_agent() 返回 None，调用方走「手动选工具」降级模式。

设计为可插拔：未来加新的 provider，继承 LLMAgent 实现 chat() 即可。
"""

import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import requests

import config
from mcp_proxy import MCPClient, MCPError


# Agent 循环最多调用工具的次数（防死循环）
MAX_TOOL_ROUNDS = 6


class LLMAgent:
    """LLM Agent 基类。子类实现 chat() 流式输出。"""

    def chat(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        available_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[str]:
        """
        流式输出回答文本（yield 文本片段，供 SSE 逐段推给前端）。

        :param message: 用户本轮问题
        :param history: 历史对话 [{role:"user"|"assistant", content:"..."}]
        :param available_tools: 可用 MCP 工具元数据（name/description/inputSchema/server）
        """
        raise NotImplementedError
        yield ""  # pragma: no cover  # 让类型检查识别为生成器


class OpenAICompatibleAgent(LLMAgent):
    """
    OpenAI 兼容接口的 Agent（DeepSeek / 通义 / GLM 等）。

    工具调用循环：
      1. 把 MCP 工具 schema 转成 OpenAI tools 定义喂给 LLM
      2. LLM 若返回 tool_calls → 执行对应 MCP 工具 → 结果作为 tool 角色消息回喂
      3. 重复直到 LLM 输出纯文本回答，流式 yield 给前端
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.mcp = MCPClient.instance()

    def _build_tools_param(self, available_tools: List[Dict[str, Any]]) -> List[dict]:
        """把 MCP 工具元数据转成 OpenAI tools 定义。"""
        tools = []
        for t in available_tools:
            # 把 server 编码进工具名，避免不同 server 重名；调用时再拆开
            full_name = f"{t['server']}__{t['name']}"
            tools.append({
                "type": "function",
                "function": {
                    "name": full_name,
                    "description": (t.get("description") or "")[:500],  # 截断防超长
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
        return tools

    def _run_tool(self, full_name: str, arguments: dict) -> str:
        """执行 LLM 选定的工具（拆出 server/tool），返回结果文本。"""
        if "__" not in full_name:
            return f"[工具名格式错误: {full_name}]"
        server, tool_name = full_name.split("__", 1)
        try:
            return self.mcp.call_tool(server, tool_name, arguments)
        except MCPError as e:
            return f"[工具调用失败: {e}]"

    def chat(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        available_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[str]:
        """
        同步实现 agent 循环（用生成器模拟流式，避免引入 async 依赖）。
        FastAPI StreamingResponse 会把 yield 的片段逐段推给浏览器。
        """
        messages = list(history or [])
        messages.append({"role": "user", "content": message})
        tools_param = self._build_tools_param(available_tools or [])

        chat_url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        for _ in range(MAX_TOOL_ROUNDS + 1):
            body = {
                "model": self.model,
                "messages": messages,
                "stream": False,   # 工具调用阶段用非流式，拿到完整 tool_calls 再执行
                "temperature": 0.3,
            }
            if tools_param:
                body["tools"] = tools_param

            try:
                r = requests.post(chat_url, headers=headers, json=body, timeout=90)
                if r.status_code != 200:
                    yield f"\n\n⚠ LLM 接口返回 {r.status_code}：{r.text[:300]}"
                    return
                data = r.json()
            except requests.RequestException as e:
                yield f"\n\n⚠ LLM 网络异常：{e}"
                return
            except ValueError as e:
                yield f"\n\n⚠ LLM 响应解析失败：{e}"
                return

            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {})

            # 1) LLM 要调工具 → 执行后回喂，进入下一轮
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                messages.append(msg)  # 记录 assistant 的 tool_calls 消息
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    full_name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    yield f"\n\n🔧 调用工具 `{full_name}`...\n"
                    result_text = self._run_tool(full_name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result_text,
                    })
                continue  # 再让 LLM 基于工具结果生成回答

            # 2) 无 tool_calls → 最终文本回答，流式吐出（模拟逐段，前端有打字机效果即可）
            final_text = msg.get("content") or ""
            if not final_text:
                yield "(模型未返回内容)"
            # 分段 yield（按句号/换行切，制造流式观感）
            chunk = ""
            for ch in final_text:
                chunk += ch
                if ch in "。；;\n":
                    yield chunk
                    chunk = ""
                    time.sleep(0.02)
            if chunk:
                yield chunk
            return

        yield "\n\n⚠ 超过工具调用最大轮数，已停止。"


def is_enabled() -> bool:
    """LLM 是否已配置（启用全自动模式）。"""
    return bool(getattr(config, "LLM_API_KEY", ""))


def get_agent() -> Optional[LLMAgent]:
    """
    获取已配置的 LLM Agent 实例；未配置返回 None（调用方走降级模式）。
    后续若要支持多 provider，在此按 config.LLM_PROVIDER 分发即可。
    """
    api_key = getattr(config, "LLM_API_KEY", "")
    if not api_key:
        return None
    base_url = getattr(config, "LLM_BASE_URL", "https://api.deepseek.com/v1")
    model = getattr(config, "LLM_MODEL", "deepseek-chat")
    return OpenAICompatibleAgent(api_key, base_url, model)
