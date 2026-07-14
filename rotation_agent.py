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
ROTATION_MAX_TOOL_ROUNDS = 40


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

    def _batch_collect(self) -> str:
        """
        后端批量采集数据（多线程，约10-20秒），返回汇总文本供 LLM 分析。
        采集内容：
          1. 列出自选分组（剔除ZT/CC），过滤成分股>6的分组
          2. 多线程拉全部成分股的最近5日日K线
          3. 按分组汇总：涨跌家数/平均涨幅/量能/与指数对比
          4. 拉上证/创业板指数最近5日日K线
        """
        from database import Database
        from kline_fetcher import KLineFetcher
        from concurrent.futures import ThreadPoolExecutor, as_completed

        db = Database()
        members_map = db.get_custom_members_map()
        group_names = db.get_custom_group_names()
        holding_name = getattr(config, "HOLDING_GROUP_NAME", "CC")

        # 筛选有效分组（剔除ZT/CC，成分股>6）
        valid_groups = {}
        all_codes = set()
        for gid, codes in members_map.items():
            gname = group_names.get(gid, gid)
            if gname.strip().upper().startswith("ZT"):
                continue
            if gname == holding_name:
                continue
            if len(codes) < 6:
                continue
            valid_groups[gid] = {"name": gname, "codes": codes}
            all_codes.update(codes)

        print(f"[ROTATION] 有效分组 {len(valid_groups)} 个，总股票 {len(all_codes)} 只")

        # 多线程拉日K线（每线程独立 KLineFetcher 实例，避免共享 session/throttle 竞争）
        kline_data = {}  # {code: [day_kline_dicts]}

        def _fetch_one(code):
            pure = code.split(".")[0] if "." in code else code
            try:
                f = KLineFetcher()   # 每线程独立实例（独立 session + throttle）
                return code, f.fetch_day_kline(pure, count=-5) or []
            except Exception:
                return code, []

        with ThreadPoolExecutor(max_workers=32) as ex:
            futs = {ex.submit(_fetch_one, c): c for c in all_codes}
            for fu in as_completed(futs):
                code, data = fu.result()
                kline_data[code] = data

        # 拉指数（单独实例）
        idx_fetcher = KLineFetcher()
        idx_sh = idx_fetcher.fetch_day_kline("000001", count=-5) or []
        idx_cyb = idx_fetcher.fetch_day_kline("399006", count=-5) or []

        # 汇总
        lines = ["# 板块数据汇总\n"]
        # 指数
        lines.append("## 大盘指数")
        if idx_sh:
            sh_chg5 = (idx_sh[-1]["close"] / idx_sh[0]["close"] - 1) * 100
            sh_last = (idx_sh[-1]["close"] / idx_sh[-2]["close"] - 1) * 100 if len(idx_sh) >= 2 else 0
            lines.append(f"- 上证指数: 5日{sh_chg5:+.2f}% 最新{sh_last:+.2f}%")
        if idx_cyb:
            cyb_chg5 = (idx_cyb[-1]["close"] / idx_cyb[0]["close"] - 1) * 100
            cyb_last = (idx_cyb[-1]["close"] / idx_cyb[-2]["close"] - 1) * 100 if len(idx_cyb) >= 2 else 0
            lines.append(f"- 创业板指: 5日{cyb_chg5:+.2f}% 最新{cyb_last:+.2f}%")
        idx_avg5 = (sh_chg5 + cyb_chg5) / 2 if idx_sh and idx_cyb else 0
        idx_avg_last = (sh_last + cyb_last) / 2 if idx_sh and idx_cyb else 0

        lines.append(f"\n## 自选分组数据（共{len(valid_groups)}个分组）\n")

        # 按最近一日涨幅排序分组
        group_stats = []
        for gid, info in valid_groups.items():
            codes = info["codes"]
            changes_5d = []
            changes_last = []
            vol_ratios = []
            up_count = 0

            for code in codes:
                kl = kline_data.get(code, [])
                if len(kl) < 2:
                    continue
                closes = [d["close"] for d in kl]
                vols = [d.get("volume", 0) for d in kl]
                # 5日累计涨幅
                chg5 = (closes[-1] / closes[0] - 1) * 100 if closes[0] else 0
                # 最后一日涨幅
                chg_last = (closes[-1] / closes[-2] - 1) * 100 if len(closes) >= 2 else 0
                # 量比
                avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1) if len(vols) > 1 else vols[0] if vols else 1
                vr = vols[-1] / avg_vol if avg_vol else 1

                changes_5d.append(chg5)
                changes_last.append(chg_last)
                vol_ratios.append(vr)
                if chg_last > 0:
                    up_count += 1

            valid_cnt = len(changes_5d)
            if valid_cnt == 0:
                continue

            avg_5d = sum(changes_5d) / valid_cnt
            avg_last = sum(changes_last) / valid_cnt
            avg_vol_ratio = sum(vol_ratios) / valid_cnt
            up_pct = up_count / valid_cnt * 100

            # vs 指数
            vs_idx = "强于大盘" if avg_5d > idx_avg5 + 1 else ("弱于大盘" if avg_5d < idx_avg5 - 1 else "同步")

            group_stats.append({
                "name": info["name"],
                "members": len(codes),
                "valid": valid_cnt,
                "up_count": up_count,
                "up_pct": round(up_pct, 1),
                "avg_5d": round(avg_5d, 2),
                "avg_last": round(avg_last, 2),
                "vol_ratio": round(avg_vol_ratio, 2),
                "vs_index": vs_idx,
                # 找涨幅最大的3只
                "top_stocks": sorted(
                    [(codes[i], round(changes_last[i], 2)) for i in range(len(codes)) if i < len(changes_last)],
                    key=lambda x: x[1], reverse=True
                )[:3],
            })

        # 按最近日涨幅排序
        group_stats.sort(key=lambda x: x["avg_last"], reverse=True)

        for i, g in enumerate(group_stats):
            lines.append(f"### {i+1}. {g['name']}（{g['valid']}/{g['members']}只有效）")
            lines.append(f"   - 5日均涨: **{g['avg_5d']:+.2f}%** | 最新日: **{g['avg_last']:+.2f}%**")
            lines.append(f"   - 上涨: {g['up_count']}/{g['valid']}({g['up_pct']:.0f}%) | 量比: {g['vol_ratio']:.2f} | vs指数: {g['vs_index']}")
            top_str = ", ".join(f"{c.split('.')[0]}({v:+.1f}%)" for c, v in g["top_stocks"])
            lines.append(f"   - 领涨: {top_str}")
            lines.append("")

        return "\n".join(lines)

    def analyze_rotation(self) -> Generator[str, None, None]:
        """
        板块轮动分析（生成器，yield SSE 文本片段）。
        阶段1 后端批量采集 → 阶段2 LLM 情绪周期分析 → 阶段3 对抗审查 → 阶段4 结论
        """
        yield "\n📋 **板块轮动分析启动**\n\n"

        # ========== 阶段1：后端批量数据采集（多线程，不依赖 LLM 调工具）==========
        yield "---\n📊 **阶段1：数据采集**（后端批量拉取日K线）\n\n"
        raw_data = ""
        try:
            import time as _t
            _t0 = _t.time()
            raw_data = self._batch_collect()
            _dt = _t.time() - _t0

            # 从 raw_data 提取统计摘要展示给用户
            lines = raw_data.split('\n')
            group_count = sum(1 for l in lines if l.startswith('### '))
            yield f"✅ 采集完成：**{group_count}** 个概念题材分组，耗时 **{_dt:.1f}s**\n\n"
            yield "**当日涨幅 Top 10 分组：**\n\n"
            yield "| # | 分组 | 5日均涨 | 最新日 | 上涨占比 | 量比 | vs指数 |\n"
            yield "|---|---|---|---|---|---|---|\n"
            shown = 0
            for l in lines:
                if l.startswith('### ') and shown < 10:
                    # 解析分组名
                    name_part = l.replace('### ', '').split('（')[0]
                    # 找紧跟的两行数据
                    idx = lines.index(l)
                    detail1 = lines[idx+1] if idx+1 < len(lines) else ''
                    detail2 = lines[idx+2] if idx+2 < len(lines) else ''
                    yield f"| {shown+1} | {name_part} | {detail1.replace('   - ', '')} | {detail2.replace('   - ', '')} |\n"
                    shown += 1
            yield f"\n"
        except Exception as e:
            yield f"\n⚠ 数据采集失败：{e}\n\n"
            return

        # ========== 阶段2：第一性原理 + 情绪周期分析 ==========
        yield "---\n📈 **阶段2：第一性原理分析**\n\n"
        stage2_system = (
            "你是一位资深A股板块轮动分析师。请基于第一性原理，结合情绪周期理论分析以下数据。\n\n"
            "## 情绪周期四阶段（核心分析框架）\n"
            "每个概念题材分组都处于以下某个阶段：\n"
            "1. **启动期**：少数龙头股率先异动（涨幅3-7%），量能温和放大，板块多数个股尚未跟涨。"
            "→ 明日大概率继续走强（资金刚开始关注，上行空间大）\n"
            "2. **发酵期**：板块内>60%个股上涨，涨幅扩散，量能明显放大，与指数同步偏强。"
            "→ 明日可能继续强势（趋势确立，资金涌入）\n"
            "3. **高潮期**：板块普涨且涨幅巨大（多只>10%），量能急剧放大（天量），龙头股涨停潮。"
            "→ 明日可能分歧/回调（短期过热，获利盘抛压）\n"
            "4. **退潮期**：涨幅收窄、冲高回落、涨跌分化，量能萎缩。"
            "→ 明日大概率走弱（资金流出，寻找下一个题材）\n\n"
            "## 分析要求\n"
            "1. **情绪周期定位**：对每个分组，判断其处于哪个阶段，给出依据\n"
            "2. **轮动预判**：高潮/退潮的板块资金可能流向哪些启动/发酵期的板块？\n"
            "3. **指数共振**：哪些分组与指数走势强共振？（指数跌它抗跌→真强势；指数涨它不涨→弱势）\n"
            "4. **冰点反转信号**：如果指数连续下跌，哪些板块率先企稳走强？（可能是新主线）\n\n"
            "## 输出格式\n"
            "给出**明日强势板块排名（Top 5）**，每个包含：\n"
            "- 当前情绪周期阶段 + 判断依据（引用具体数据）\n"
            "- 明日预判（继续走强/分歧/回调）+ 理由\n"
            "- 风险提示（什么信号出现就应放弃）\n"
            "- 重点关注个股（该分组内的领涨股）\n\n"
            "用 Markdown 格式输出，分析要有数据支撑，不要空泛。"
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
