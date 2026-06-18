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
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import requests

import config
from mcp_proxy import MCPClient, MCPError


# Agent 循环最多调用工具的次数（防死循环）
MAX_TOOL_ROUNDS = 6

# 模型列表缓存有效期（秒）：ARK /models 接口较慢，缓存避免每次切换都重拉
_MODEL_LIST_CACHE_TTL = 600
# 不适合「文本对话问答」的模型类型关键字（embedding/视觉/视频/图片生成/3D/翻译/角色扮演等）
_NON_CHAT_KEYWORDS = (
    "embedding", "vision", "seedance", "seedream", "seed3d",
    "hitem3d", "hyper3d", "translation", "character",
)
# coding plan 探测：ARK /coding/v3/models 返回账户全部模型，但只有部分支持 coding plan。
# 列表接口不标注此能力（features 为空），唯一可靠判断方式是实际发一次极简 chat 请求。
# 不支持的会返回 HTTP 404 + "does not support the coding plan feature"。
_PROBE_CONCURRENCY = 8   # 探测并发数，避免逐个串行太慢
_PROBE_TIMEOUT = 25      # 单次探测超时（秒）

# 运行时模型状态（进程内，可被前端切换；重启回 config 默认）
_runtime_lock = threading.Lock()
_runtime_model: Optional[str] = None   # 运行时当前模型 id；None=用 config 默认
_model_list_cache: Optional[Dict[str, Any]] = None  # {"models":[...], "fetched_at": ts}


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
    模型选择优先级：运行时切换的模型 > config.LLM_MODEL 默认。
    """
    api_key = getattr(config, "LLM_API_KEY", "")
    if not api_key:
        return None
    base_url = getattr(config, "LLM_BASE_URL", "https://api.deepseek.com/v1")
    model = get_current_model()
    return OpenAICompatibleAgent(api_key, base_url, model)


def get_current_model() -> str:
    """当前生效的模型 id（运行时切换的 > config 默认）。"""
    with _runtime_lock:
        if _runtime_model:
            return _runtime_model
    return getattr(config, "LLM_MODEL", "deepseek-chat")


def set_current_model(model_id: str) -> None:
    """运行时切换模型（进程内，重启回 config 默认）。"""
    global _runtime_model
    with _runtime_lock:
        _runtime_model = model_id


def reset_current_model() -> str:
    """重置为 config 默认模型，返回默认模型 id。"""
    global _runtime_model
    with _runtime_lock:
        _runtime_model = None
    return getattr(config, "LLM_MODEL", "deepseek-chat")


def _probe_coding_models(base_url: str, api_key: str, candidate_ids: List[str]) -> set:
    """
    并发探测哪些模型真正支持 coding plan。
    ARK /coding/v3 列表接口返回账户全部模型，但其中部分不支持 coding plan（调用即 404），
    列表又不标注此能力，故只能逐个发极简 chat 请求实测。
    :return: 支持的模型 id 集合
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    chat_url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload_tmpl = {"messages": [{"role": "user", "content": "ok"}], "max_tokens": 5, "stream": False}
    supported = set()

    def _probe(mid: str) -> bool:
        try:
            r = requests.post(chat_url, headers=headers,
                              json={**payload_tmpl, "model": mid}, timeout=_PROBE_TIMEOUT)
            return r.status_code == 200
        except requests.RequestException:
            return False

    with ThreadPoolExecutor(max_workers=_PROBE_CONCURRENCY) as ex:
        futs = {ex.submit(_probe, mid): mid for mid in candidate_ids}
        for fut in as_completed(futs):
            if fut.result():
                supported.add(futs[fut])
    return supported


def list_chat_models(use_cache: bool = True) -> Dict[str, Any]:
    """
    从 ARK /models 拉取并过滤出适合「文本对话问答」的可用模型列表。
    过滤规则：排除 Shutdown 状态，排除 embedding/vision/视频/图片/3D/翻译等非对话模型。

    :return: {"models": [{"id","name","status"}], "current": "当前模型id", "default": "config默认", "error": None|str}
    """
    global _model_list_cache
    now = time.time()
    with _runtime_lock:
        if (use_cache and _model_list_cache
                and (now - _model_list_cache["fetched_at"]) < _MODEL_LIST_CACHE_TTL):
            models = _model_list_cache["models"]
        else:
            models = None

    if models is None:
        api_key = getattr(config, "LLM_API_KEY", "")
        base_url = getattr(config, "LLM_BASE_URL", "")
        if not api_key or not base_url:
            return {"models": [], "current": get_current_model(),
                    "default": getattr(config, "LLM_MODEL", ""),
                    "error": "未配置 LLM_API_KEY / LLM_BASE_URL"}
        try:
            r = requests.get(
                base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=20,
            )
            if r.status_code != 200:
                return {"models": [], "current": get_current_model(),
                        "default": getattr(config, "LLM_MODEL", ""),
                        "error": f"ARK /models 返回 {r.status_code}: {r.text[:200]}"}
            raw = r.json().get("data", [])
        except (requests.RequestException, ValueError) as e:
            return {"models": [], "current": get_current_model(),
                    "default": getattr(config, "LLM_MODEL", ""),
                    "error": f"拉取模型列表失败: {e}"}

        candidates = []
        for m in raw:
            mid = (m.get("id") or "").strip()
            mid_low = mid.lower()
            if not mid:
                continue
            if m.get("status") == "Shutdown":
                continue  # 已下线
            if any(kw in mid_low for kw in _NON_CHAT_KEYWORDS):
                continue  # 非文本对话类
            candidates.append({"id": mid, "name": m.get("name") or mid, "status": m.get("status")})

        # 关键：ARK /coding/v3/models 返回账户全部模型，但只有部分支持 coding plan。
        # 实测探测，剔除调用会 404 的（如 doubao 老代、qwen 全系等）。
        # 探测失败（网络等）时不剔除，保守保留全部候选，避免误删可用模型。
        try:
            supported = _probe_coding_models(base_url, api_key, [c["id"] for c in candidates])
            models = [c for c in candidates if c["id"] in supported]
            # 探测被全部剔除（异常情况）：保留候选全集 + 标注，避免下拉变空
            if not models:
                models = candidates
                probe_warn = "（coding plan 探测结果为空，已保留全部候选，部分可能不可用）"
            else:
                probe_warn = None
        except Exception as e:
            models = candidates
            probe_warn = f"（coding plan 探测失败：{e}，已保留全部候选）"

        models.sort(key=lambda x: x["id"])
        with _runtime_lock:
            _model_list_cache = {"models": models, "fetched_at": now, "warn": probe_warn}

    return {
        "models": models,
        "current": get_current_model(),
        "default": getattr(config, "LLM_MODEL", ""),
        "error": None,
        "warn": _model_list_cache.get("warn") if _model_list_cache else None,
    }

    return {
        "models": models,
        "current": get_current_model(),
        "default": getattr(config, "LLM_MODEL", ""),
        "error": None,
    }

