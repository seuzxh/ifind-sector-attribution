# -*- coding: utf-8 -*-
"""
板块轮动分析智能体（Loop Engine + 对抗审查）

基于 OpenAICompatibleAgent 扩展，增加 kline-fetcher 工具集 + 三阶段编排：
  阶段1【数据采集】LLM 自主调工具拉日K线+分时+指数
  阶段2【第一性原理分析】基于数据分析板块轮动/强势度/指数共振
  阶段3【对抗审查】质疑阶段2结论的漏洞
  阶段4【综合结论】融合分析+审查，输出最终排名

工具集（kline-fetcher + database 包装为 LLM 可调工具，不走 MCP server）：
  kline__day_kline       拉个股日K线
  kline__history_trend   拉历史分时
  custom__list_groups    列出自选分组（剔除ZT/CC）
  custom__group_members  查分组成分股
"""

import os
import sys
import json
import time
from typing import Any, Dict, Generator, List, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from llm_agent import OpenAICompatibleAgent, get_current_model, MAX_TOOL_ROUNDS


# 确保 kline-fetcher 能拿到 API 地址
if not os.environ.get("KLINE_API_BASE_URL") and getattr(config, "KLINE_API_BASE_URL", ""):
    os.environ["KLINE_API_BASE_URL"] = config.KLINE_API_BASE_URL

# 轮动分析的工具调用最大轮数（比通用问答多，需多次拉数据）
ROTATION_MAX_TOOL_ROUNDS = 20


# ========== kline-fetcher 工具定义 ==========
KLINE_TOOLS = [
    {
        "name": "day_kline",
        "description": "获取A股个股的日K线数据（后复权）。用于分析近期走势、涨幅、量能。返回最近N天的 OHLCV + 涨幅摘要。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "股票代码，如 600519 或 600519.SH"},
                "days": {"type": "integer", "description": "获取最近N个交易日的日K线，默认5", "default": 5},
            },
            "required": ["code"],
        },
    },
    {
        "name": "history_trend",
        "description": "获取个股历史分时数据（某一交易日的逐分钟分时）。用于分析当日走势强度、与指数联动性。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "股票代码"},
                "date": {"type": "string", "description": "交易日期 YYYYMMDD，如 20260630"},
            },
            "required": ["code", "date"],
        },
    },
]

CUSTOM_TOOLS = [
    {
        "name": "list_groups",
        "description": "列出所有自选股分组（已自动剔除ZT开头的涨停分组和CC持仓分组）。返回分组名和成分股数量。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "group_members",
        "description": "查某个自选分组的全部成分股代码列表。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "分组ID（从 list_groups 获取）"},
            },
            "required": ["group_id"],
        },
    },
]


# ========== 工具执行（kline-fetcher + database）==========
def _exec_kline_tool(tool_name: str, args: dict) -> str:
    """执行 kline-fetcher 工具，返回摘要化文本。"""
    from kline_fetcher import KLineFetcher, TrendFetcher

    fetcher = KLineFetcher()

    if tool_name == "day_kline":
        code = args.get("code", "")
        days = abs(args.get("days", 5))
        data = fetcher.fetch_day_kline(code, count=-days)
        if not data:
            return f"[{code}] 无日K线数据"
        return _summarize_day_kline(code, data)

    elif tool_name == "history_trend":
        code = args.get("code", "")
        date = args.get("date", "")
        tf = TrendFetcher()
        data = tf.fetch_history_trend(code, date)
        if not data or not data.get("trading"):
            return f"[{code} {date}] 无分时数据"
        return _summarize_trend(code, date, data)

    return f"[未知 kline 工具: {tool_name}]"


def _exec_custom_tool(tool_name: str, args: dict) -> str:
    """执行自选分组工具（database 查询）。"""
    from database import Database
    db = Database()

    if tool_name == "list_groups":
        names = db.get_custom_group_names()
        members_map = db.get_custom_members_map()
        holding_name = getattr(config, "HOLDING_GROUP_NAME", "CC")
        groups = []
        for gid, gname in names.items():
            # 剔除 ZT 开头和 CC 持仓分组
            if gname.strip().upper().startswith("ZT"):
                continue
            if gname == holding_name:
                continue
            cnt = len(members_map.get(gid, []))
            groups.append({"group_id": gid, "group_name": gname, "member_count": cnt})
        return json.dumps({"groups": groups}, ensure_ascii=False)

    elif tool_name == "group_members":
        gid = args.get("group_id", "")
        members_map = db.get_custom_members_map()
        codes = members_map.get(gid, [])
        names = db.get_custom_group_names()
        gname = names.get(gid, gid)
        return json.dumps({
            "group_id": gid, "group_name": gname,
            "member_count": len(codes), "stocks": codes,
        }, ensure_ascii=False)

    return f"[未知 custom 工具: {tool_name}]"


# ========== 摘要函数（避免 context 爆炸）==========
def _summarize_day_kline(code: str, data: list) -> str:
    """日K线 → 摘要文本（最近N日 + 关键统计）。"""
    if not data:
        return f"[{code}] 无数据"
    closes = [d["close"] for d in data]
    volumes = [d.get("volume", 0) for d in data]
    amounts = [d.get("amount", 0) for d in data]

    # 涨幅统计
    first_close = closes[0]
    last_close = closes[-1]
    total_chg = (last_close / first_close - 1) * 100 if first_close else 0
    # 最后一日涨幅
    if len(closes) >= 2:
        last_day_chg = (closes[-1] / closes[-2] - 1) * 100
    else:
        last_day_chg = 0
    # 量比（最后一日 vs 前几日均值）
    avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1) if len(volumes) > 1 else volumes[0]
    vol_ratio = volumes[-1] / avg_vol if avg_vol else 1
    # 振幅（最高-最低）/均值
    max_close = max(closes)
    min_close = min(closes)
    amplitude = (max_close / min_close - 1) * 100 if min_close else 0

    lines = [f"[{code}] 最近{len(data)}日日K线摘要："]
    lines.append(f"  累计涨幅: {total_chg:+.2f}% | 最后一日: {last_day_chg:+.2f}%")
    lines.append(f"  量比(vs前几日均量): {vol_ratio:.2f} | 振幅: {amplitude:.2f}%")
    lines.append(f"  最新收盘: {last_close:.2f} | 成交额: {amounts[-1]/1e8:.2f}亿")
    # 逐日简表
    for d in data[-5:]:
        chg = ""
        di = data.index(d)
        if di > 0:
            chg = f" ({(d['close']/data[di-1]['close']-1)*100:+.2f}%)"
        lines.append(f"  {d['date']} close={d['close']:.2f} vol={d.get('volume',0):.0f}{chg}")
    return "\n".join(lines)


def _summarize_trend(code: str, date: str, data: dict) -> str:
    """分时数据 → 摘要（关键时点涨幅 + 走势特征）。"""
    trading = data.get("trading", [])
    if not trading:
        return f"[{code} {date}] 无盘中分时"
    pre_close = None
    pm = data.get("pre_market", [])
    if pm:
        refs = [p.get("ref_price") for p in pm if p.get("ref_price")]
        pre_close = refs[0] if refs else None

    first = trading[0]
    last = trading[-1]
    open_price = first.get("last_price", 0)
    close_price = last.get("last_price", 0)

    # 关键时点涨幅（vs 昨收）
    def chg_vs_preclose(price):
        if pre_close and pre_close > 0:
            return (price / pre_close - 1) * 100
        return 0

    lines = [f"[{code} {date}] 分时摘要："]
    lines.append(f"  昨收: {pre_close or '-'} | 开盘: {open_price:.2f}({chg_vs_preclose(open_price):+.2f}%) | 收盘: {close_price:.2f}({chg_vs_preclose(close_price):+.2f}%)")

    # 走势特征：最高/最低涨幅
    max_price = max(p.get("last_price", 0) for p in trading)
    min_price = min(p.get("last_price", 999999) for p in trading)
    lines.append(f"  最高: {max_price:.2f}({chg_vs_preclose(max_price):+.2f}%) | 最低: {min_price:.2f}({chg_vs_preclose(min_price):+.2f}%)")

    # 尾盘走势（最后30分钟）
    tail = trading[-30:] if len(trading) >= 30 else trading
    tail_chg = 0
    if len(tail) >= 2 and tail[0].get("last_price"):
        tail_chg = (tail[-1]["last_price"] / tail[0]["last_price"] - 1) * 100
    lines.append(f"  尾盘30min: {tail_chg:+.2f}% | 总成交额: {last.get('turnover',0)/1e8:.2f}亿")

    # 采样5个时点
    sample_idx = [0, len(trading)//4, len(trading)//2, len(trading)*3//4, len(trading)-1]
    samples = []
    for i in sample_idx:
        if i < len(trading):
            p = trading[i]
            samples.append(f"{p['time']}={p['last_price']:.2f}")
    lines.append(f"  采样: {' | '.join(samples)}")
    return "\n".join(lines)


# ========== RotationAgent（核心 Loop Engine）==========
class RotationAgent(OpenAICompatibleAgent):
    """
    板块轮动分析智能体。继承 OpenAICompatibleAgent 的工具调用循环，
    重写工具构建/执行以注入 kline-fetcher + custom 工具。
    """

    def __init__(self):
        api_key = getattr(config, "LLM_API_KEY", "")
        base_url = getattr(config, "LLM_BASE_URL", "")
        model = get_current_model()
        super().__init__(api_key, base_url, model)

    def _build_tools_param(self, available_tools: List[Dict[str, Any]] = None) -> List[dict]:
        """注入 kline + custom 工具（不依赖 MCP 工具）。"""
        tools = []
        for t in KLINE_TOOLS:
            tools.append({
                "type": "function",
                "function": {
                    "name": f"kline__{t['name']}",
                    "description": t["description"][:500],
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
        for t in CUSTOM_TOOLS:
            tools.append({
                "type": "function",
                "function": {
                    "name": f"custom__{t['name']}",
                    "description": t["description"][:500],
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
        return tools

    def _run_tool(self, full_name: str, arguments: dict) -> str:
        """按工具名前缀路由：kline__→kline-fetcher，custom__→database。"""
        if "__" not in full_name:
            return f"[工具名格式错误: {full_name}]"
        source, tool_name = full_name.split("__", 1)
        try:
            if source == "kline":
                return _exec_kline_tool(tool_name, arguments)
            elif source == "custom":
                return _exec_custom_tool(tool_name, arguments)
            else:
                return f"[未知工具来源: {source}]"
        except Exception as e:
            return f"[工具调用失败 {full_name}: {e}]"

    def _llm_call(self, messages: list, use_tools: bool = True) -> dict:
        """单次 LLM 调用（非流式），返回 choice.message。"""
        chat_url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": 0.3,
        }
        if use_tools:
            body["tools"] = self._build_tools_param()
        r = requests.post(chat_url, headers=headers, json=body, timeout=120)
        if r.status_code != 200:
            return {"content": f"[LLM 返回 {r.status_code}: {r.text[:200]}]"}
        data = r.json()
        return (data.get("choices") or [{}])[0].get("message", {})

    def analyze_rotation(self) -> Generator[str, None, None]:
        """
        三阶段板块轮动分析（生成器，yield SSE 文本片段）。
        """
        yield "\n📋 **板块轮动分析启动**\n\n"

        # ========== 阶段1：数据采集（LLM 自主调工具）==========
        yield "---\n📊 **阶段1：数据采集**\n\n"
        stage1_system = (
            "你是一个A股板块轮动数据分析助手。请自主调用工具采集以下数据：\n"
            "1. 先用 custom__list_groups 列出所有自选分组\n"
            "2. 对每个分组，用 custom__group_members 获取成分股，"
            "然后对成分股中的代表性个股（选前3-5只）用 kline__day_kline 拉最近5日日K线\n"
            "3. 对上证指数(000001)和创业板指(399006)用 kline__day_kline 拉最近5日日K线\n"
            "4. 对重点分组用 kline__history_trend 拉最近交易日分时数据\n\n"
            "数据采集完成后，请用以下JSON格式总结每个分组的特征：\n"
            "```json\n{\n  \"groups\": [\n"
            "    {\"name\": \"分组名\", \"members\": N, \"avg_5d_change\": X%, "
            "\"last_day_change\": X%, \"vol_trend\": \"放量/缩量\", "
            "\"index_correlation\": \"强共振/弱/逆势\", \"stage\": \"启动发酵/高潮/回落\"}\n  ]\n}\n```\n"
            "只输出JSON，不要其他解释。"
        )
        messages = [
            {"role": "system", "content": stage1_system},
            {"role": "user", "content": "请开始采集数据并分析。"},
        ]
        tools_param = self._build_tools_param()

        # 工具调用循环
        raw_data = ""
        for round_i in range(ROTATION_MAX_TOOL_ROUNDS + 1):
            msg = self._llm_call(messages, use_tools=True)

            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                messages.append(msg)
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    full_name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    yield f"🔧 调用 `{full_name}`"
                    if args:
                        yield f"({json.dumps(args, ensure_ascii=False)[:80]})"
                    yield "...\n"
                    result_text = self._run_tool(full_name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result_text,
                    })
                continue

            # 无 tool_calls → 阶段1完成
            raw_data = msg.get("content") or ""
            yield f"\n✅ 数据采集完成\n\n"
            break
        else:
            yield "\n⚠ 数据采集超过最大轮数，使用已有数据继续\n\n"

        # ========== 阶段2：第一性原理分析 ==========
        yield "---\n📈 **阶段2：第一性原理分析**\n\n"
        stage2_system = (
            "你是一位资深A股板块轮动分析师，请基于第一性原理分析以下数据：\n\n"
            "分析维度：\n"
            "1. **启动发酵**：哪些分组处于温和放量上涨初期（5日涨幅适中、量能温和放大、未到高潮）？\n"
            "2. **轮动节奏**：哪些分组短期涨幅过大（可能资金流出轮动到其他板块）？\n"
            "3. **指数共振**：哪些分组与指数走势共振（指数跌时抗跌、指数涨时领涨 = 强势）？\n"
            "4. **冰点反转**：指数连续下跌至冰点后，率先企稳走强的板块可能是新主线。\n\n"
            "请给出**明日强势板块排名（Top 5）**，每个写明：\n"
            "- 排名理由（引用具体数据）\n"
            "- 风险提示（可能的失败信号）\n"
            "- 关注的领涨个股\n\n"
            "用 Markdown 格式输出。"
        )
        messages2 = [
            {"role": "system", "content": stage2_system},
            {"role": "user", "content": f"以下是采集到的板块数据：\n\n{raw_data}"},
        ]
        msg2 = self._llm_call(messages2, use_tools=False)
        analysis = msg2.get("content") or "(分析为空)"
        # 流式 yield（分段）
        yield from _stream_text(analysis)
        yield "\n\n"

        # ========== 阶段3：对抗审查 ==========
        yield "---\n⚔ **阶段3：对抗审查**\n\n"
        stage3_system = (
            "你是一位严格的投资审查者。请对以下板块轮动分析进行对抗性审查：\n\n"
            "审查维度：\n"
            "1. **过拟合风险**：结论是否仅基于最近几日数据？小样本是否可靠？\n"
            "2. **系统性风险**：如果大盘次日大跌，所有板块可能失效。分析是否考虑了止损？\n"
            "3. **逻辑漏洞**：排名是否把已涨过高的板块排在前面（追高风险）？\n"
            "4. **数据局限**：仅看日K线是否忽略了关键信息（如政策面、资金面）？\n"
            "5. **确认/修正**：哪些排名你认为合理？哪些需要调整？\n\n"
            "请直接给出审查意见。如果分析合理，确认即可；如果有漏洞，指出并给出修正建议。"
        )
        messages3 = [
            {"role": "system", "content": stage3_system},
            {"role": "user", "content": f"板块轮动分析：\n\n{analysis}"},
        ]
        msg3 = self._llm_call(messages3, use_tools=False)
        critique = msg3.get("content") or "(审查为空)"
        yield from _stream_text(critique)
        yield "\n\n"

        # ========== 阶段4：综合结论 ==========
        yield "---\n✅ **阶段4：综合结论**\n\n"
        stage4_system = (
            "请综合分析师的分析和审查者的审查意见，给出最终的明日板块操作建议。\n"
            "格式：\n"
            "## 最终排名\n"
            "1. **板块名** - 推荐度（★★★~★）- 一句话理由\n"
            "...\n\n"
            "## 操作策略\n"
            "- 重点关注：...\n"
            "- 观察信号：...（什么情况下确认/放弃）\n"
            "- 风险控制：...\n\n"
            "简洁有力，不超过300字。"
        )
        messages4 = [
            {"role": "system", "content": stage4_system},
            {"role": "user", "content": f"分析师结论：\n{analysis}\n\n审查意见：\n{critique}"},
        ]
        msg4 = self._llm_call(messages4, use_tools=False)
        conclusion = msg4.get("content") or "(结论为空)"
        yield from _stream_text(conclusion)
        yield "\n\n---\n🏁 **分析完成**"


def _stream_text(text: str) -> Generator[str, None, None]:
    """分段 yield 文本（按句号/换行切，制造流式观感）。"""
    chunk = ""
    for ch in text:
        chunk += ch
        if ch in "。；;\n":
            yield chunk
            chunk = ""
            time.sleep(0.02)
    if chunk:
        yield chunk


_agent_instance: Optional[RotationAgent] = None


def get_rotation_agent() -> RotationAgent:
    """获取 RotationAgent 单例。"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = RotationAgent()
    return _agent_instance
