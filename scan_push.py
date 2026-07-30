# -*- coding: utf-8 -*-
"""
股池归因定时推送

在交易日 9:33 / 9:45 / 10:00 / 14:30 四个时刻，按各自条件调 iFinD MCP 选股，
对「自选分组」和「全市场」分别做强势归类（完全复用 realtime_engine 的
scan_custom_groups / scan_market_groups，归类逻辑与【自选股强势归类】页面一致），
再把结果格式化成飞书卡片推送到 webhook。

触发方式：crontab 在 4 个时刻调用 `main.py push --slot <slot>`（见 scripts/run_push.sh）。
交易日校验放程序内（cron 只用 1-5 工作日粗筛，节假日由 is_trading_day 跳过）。

关键约定：
- 选股 query 原样传给 MCP（含「实体涨幅」「最大涨幅」「或」OR），不做翻译——
  盘中 MCP 反映实时数据；盘后调用因收盘会返回当日收盘结果。
- scan_custom_groups 与 scan_market_groups 互相隔离，一个失败不影响另一个。
"""

import time
from datetime import datetime
from typing import Dict, Optional

import requests

import config

# ========== 时间槽配置（slot → 选股条件）==========
# query 与用户给的触发条件逐字对应，集中在此便于日后调整。
PUSH_SLOTS: Dict[str, Dict] = {
    "933": {
        "time": "09:33",
        "label": "09:33 · 实体/最大涨幅>3% · 成交额>6亿 · 上市>5天",
        "query": "实体涨幅大于3%或最大涨幅大于3%;成交额大于6亿;上市时间大于5天",
    },
    "945": {
        "time": "09:45",
        "label": "09:45 · 实体/最大涨幅>3% · 成交金额>10亿",
        "query": "实体涨幅大于3%或最大涨幅大于3%;成交金额大于10亿",
    },
    "1000": {
        "time": "10:00",
        "label": "10:00 · 成交金额>20亿 · 实体/最大涨幅>4%",
        "query": "成交金额大于20亿;且实体涨幅大于4%或最大涨幅大于4%",
    },
    "1430": {
        "time": "14:30",
        "label": "14:30 · 涨幅7%~12.1% · 未涨停 · 非ST",
        "query": "涨幅大于7%并且小于12.1%;未涨停；非ST",
    },
}

# 飞书 webhook 单次推送最多展示的分组数 / 每组最多展示的命中股数（防卡片过长）
_MAX_GROUPS = 12
_MAX_HITS_PER_GROUP = 8

# ========== 涨幅配色（<font color> 内联标签，已实测本 webhook 支持）==========
# 阈值按 A 股涨幅习惯：>=9.8 视为涨停级（深红），>=5 强势（橙红），>0 红，<=0 绿，0 灰。
# 自定义涨幅区间通常不含负值，但分组均涨/个股都可能为 0（盘后 MCP），故覆盖全区间。
def _change_color(chg: float) -> str:
    """按涨幅返回飞书 <font color> 色名。"""
    if chg >= 9.8:
        return "darkred"   # 涨停级
    if chg >= 5.0:
        return "red"       # 强势
    if chg > 0:
        return "orange"    # 普通上涨
    if chg < 0:
        return "green"     # 下跌（绿）
    return "grey"          # 0 / 平


def _colored_chg(chg, suffix: str = "%") -> str:
    """涨幅数值上色：<font color='...'>9.1%</font>。chg 容错（None/非法→0）。"""
    try:
        chg = round(float(chg), 2)
    except (TypeError, ValueError):
        chg = 0.0
    return f"<font color='{_change_color(chg)}'>{chg}{suffix}</font>"


def _to_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def get_slot_query(slot: str) -> Optional[str]:
    """按 slot 取选股 query；未知 slot 返回 None。"""
    s = PUSH_SLOTS.get(slot)
    return s["query"] if s else None


def run_classification(slot: str) -> Dict:
    """
    按 slot 选股并归类（自选分组 + 全市场各跑一次），复用 realtime_engine 逻辑。

    两个扫描互相隔离：一个失败不影响另一个，失败侧置 error 字段。

    :param slot: "933"/"945"/"1000"/"1430"
    :return: {
        slot, query, custom: scan_custom_groups 结果(含 error 则失败),
        market: scan_market_groups 结果(含 error 则失败)
    }
    """
    from realtime_engine import scan_custom_groups, scan_market_groups

    slot_cfg = PUSH_SLOTS.get(slot)
    if not slot_cfg:
        return {"error": f"未知 slot: {slot}（可选: {list(PUSH_SLOTS)}）", "slot": slot}
    query = slot_cfg["query"]

    # 自选分组归类
    try:
        custom = scan_custom_groups(query=query)
    except Exception as e:
        custom = {"error": f"自选归类异常: {e}", "query": query}

    # 全市场归类（重置引擎单例避免与自选复用同一 MCP 结果/状态；两者 query 相同，安全）
    try:
        market = scan_market_groups(query=query)
    except Exception as e:
        market = {"error": f"全市场归类异常: {e}", "query": query}

    return {"slot": slot, "query": query, "custom": custom, "market": market}


def _format_hits(hits, limit: int = _MAX_HITS_PER_GROUP) -> str:
    """把一组命中股渲染成对齐的 markdown 行（代码  名称  涨幅%），涨幅按幅度上色。"""
    lines = []
    for h in hits[:limit]:
        name = h.get("name", "") or ""
        code = h.get("code", "")
        lines.append(f"{code}　{name}　{_colored_chg(h.get('change_ratio', 0))}")
    if len(hits) > limit:
        lines.append(f"... 等共 {len(hits)} 只")
    return "\n".join(lines) if lines else "（无命中明细）"


def _stat_column(label: str, value, color: str) -> Dict:
    """构造 column_set 里的一个统计列：上标签下数值（数值上色）。"""
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "vertical_align": "top",
        "elements": [
            {"tag": "div",
             "text": {"tag": "lark_md", "content": f"{label}\n<font color='{color}'>{value}</font>"}},
        ],
    }


def _scope_elements(name: str, result: Dict, scope_key: str) -> list:
    """
    把一侧（custom / market）归类结果转成飞书卡片元素列表。

    返回顺序：区域标题 div → 三列统计 column_set → 各分组明细 div。
    失败侧或空结果只返回一个 div。

    :param name: 区域名，如 "自选分组归类"
    :param result: scan_*_groups 的返回（含 error 视为失败）
    :param scope_key: "custom" / "market"，用于取字段语义（命中/可归类措辞）
    """
    title_color = "blue" if scope_key == "custom" else "purple"
    hit_word = "命中" if scope_key == "custom" else "可归类"

    if not result or result.get("error"):
        err = result.get("error", "无结果") if result else "无结果"
        return [{"tag": "div",
                 "text": {"tag": "lark_md",
                          "content": f"<font color='{title_color}'>**{name}**</font>\n❌ {err}"}}]

    pool_size = result.get("pool_size", 0)
    hit_total = result.get("hit_total", 0)
    group_hit_count = result.get("group_hit_count", 0)
    groups = result.get("groups", []) or []

    elements = [
        {"tag": "div",
         "text": {"tag": "lark_md",
                  "content": f"<font color='{title_color}'>**{name}**</font>"}},
        # 三列统计卡片：选股池 / 命中(可归类) / 涉及分组
        {"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": [
            _stat_column("选股池", pool_size, "blue"),
            _stat_column(hit_word, hit_total, "red"),
            _stat_column("涉及分组", group_hit_count, "green"),
        ]},
    ]

    if not groups:
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md", "content": "（本期无符合条件股票归入该范围）"}})
        return elements

    for g in groups[:_MAX_GROUPS]:
        gname = g.get("group_name", g.get("group_id", ""))
        hit_n = g.get("hit_count", 0)
        member_n = g.get("member_total", 0)
        avg = _to_float(g.get("hit_avg_change", 0))
        # 分组标题行：板块名加粗 + 命中数 + 均涨上色
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md",
                     "content": f"▸ **{gname}**　命中 {hit_n}/{member_n}　均涨 {_colored_chg(avg)}"},
        })
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": _format_hits(g.get("hits", []))},
        })
    if len(groups) > _MAX_GROUPS:
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md", "content": f"... 等共 {len(groups)} 个分组"}})

    return elements


def build_feishu_message(slot: str, classification: Dict) -> Dict:
    """
    组装飞书互动卡片（interactive）消息体。

    版面（已实测 font 颜色 + column_set 在本 webhook 可用）：
      蓝色标题头 → 条件行 → [自选: 蓝标题 + 三列统计 + 分组明细] → 分割线
                 → [全市场: 紫标题 + 三列统计 + 分组明细] → 备注页脚

    :param slot: 时间槽
    :param classification: run_classification 的返回
    :return: 飞书 webhook 期望的 {"msg_type": "interactive", "card": {...}}
    """
    slot_cfg = PUSH_SLOTS.get(slot, {})
    label = slot_cfg.get("label", slot)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    elements = [
        {"tag": "div",
         "text": {"tag": "lark_md",
                  "content": f"**条件**：{classification.get('query', '')}"}},
        {"tag": "hr"},
    ]
    elements += _scope_elements("自选分组归类", classification.get("custom", {}), "custom")
    elements.append({"tag": "hr"})
    elements += _scope_elements("全市场归类", classification.get("market", {}), "market")
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text",
                      "content": f"股池归因推送 · {now} · 归类逻辑同自选股强势归类页面"}],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text",
                          "content": f"股池归因推送 · {label}"},
                "template": "blue",
            },
            "elements": elements,
        },
    }


# ========== 单侧独立推送（自选 / 全市场 各发一条） ==========
# scope_key → 卡片头部颜色 + 标题措辞。两条推送各自独立、颜色区分。
_SCOPE_META = {
    "custom": {"title": "自选分组归类", "color": "blue"},
    "market": {"title": "全市场归类", "color": "purple"},
}


def build_scope_message(slot: str, scope_key: str, result: Dict) -> Dict:
    """
    组装单侧（自选 或 全市场）的独立飞书卡片，用于两条分别推送。

    :param slot: 时间槽
    :param scope_key: "custom" / "market"
    :param result: 对应侧的 scan_*_groups 结果（含 error 视为失败）
    :return: 飞书 webhook 期望的 {"msg_type": "interactive", "card": {...}}
    """
    meta = _SCOPE_META.get(scope_key, {"title": scope_key, "color": "blue"})
    slot_cfg = PUSH_SLOTS.get(slot, {})
    label = slot_cfg.get("label", slot)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    elements = [
        {"tag": "div",
         "text": {"tag": "lark_md",
                  "content": f"**条件**：{result.get('query', '') if isinstance(result, dict) else ''}"}},
        {"tag": "hr"},
    ]
    elements += _scope_elements(meta["title"], result, scope_key)
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text",
                      "content": f"{meta['title']} · {label} · {now} · 归类逻辑同自选股强势归类页面"}],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text",
                          "content": f"{meta['title']} · {label}"},
                "template": meta["color"],
            },
            "elements": elements,
        },
    }


def push_to_feishu(webhook_url: str, message: Dict, max_retries: int = 1) -> bool:
    """
    POST 飞书 webhook。失败重试 max_retries 次。

    :return: True 推送成功；False 失败（已打日志）
    """
    if not webhook_url:
        print("[PUSH] 未配置 PUSH_WEBHOOK_URL，跳过推送")
        return False
    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(webhook_url, json=message, timeout=10)
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    body = {}
                # 飞书成功返回 {"code":0,...} 或 {"StatusCode":0}
                code = body.get("code", body.get("StatusCode"))
                if code in (0, None) or "success" in str(body).lower():
                    print(f"[PUSH] 推送成功（attempt {attempt+1}）")
                    return True
                last_err = f"飞书返回 code={code} body={str(body)[:200]}"
                print(f"[PUSH] {last_err}")
            else:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                print(f"[PUSH] {last_err}")
        except requests.RequestException as e:
            last_err = f"网络异常: {e}"
            print(f"[PUSH] {last_err}")
        if attempt < max_retries:
            time.sleep(2)
    print(f"[PUSH] 推送失败（已重试 {max_retries+1} 次）：{last_err}")
    return False


def run_push(slot: str, webhook_url: Optional[str] = None, dry_run: bool = False) -> Dict:
    """
    编排：交易日校验 → 选股归类 → 自选/全市场**各组装一条卡片分别推送**。

    自选与全市场拆成两条独立推送（先自选蓝头，后全市场紫头），互不影响：
    一条推送失败不影响另一条。

    :param slot: "933"/"945"/"1000"/"1430"
    :param webhook_url: 推送地址，None 则用 config.PUSH_WEBHOOK_URL
    :param dry_run: True 则只归类+组装消息、打印 JSON，不推送
    :return: {slot, is_trading_day, classification, messages:{custom,market}, pushed:{custom,market}}
    """
    from trade_calendar import is_trading_day

    today = datetime.now().strftime("%Y%m%d")
    trading = is_trading_day(today)
    result: Dict = {"slot": slot, "is_trading_day": trading}

    if not trading:
        print(f"[PUSH] {today} 非交易日，跳过 slot {slot}")
        result["skipped"] = True
        return result

    print(f"[PUSH] {today} 交易日，开始 slot {slot} 选股归类...")
    classification = run_classification(slot)
    result["classification"] = classification

    # 自选 / 全市场 各组装一条独立卡片
    custom_result = classification.get("custom", {})
    market_result = classification.get("market", {})
    messages = {
        "custom": build_scope_message(slot, "custom", custom_result),
        "market": build_scope_message(slot, "market", market_result),
    }
    result["messages"] = messages

    if dry_run:
        import json
        print("[PUSH] dry-run，跳过推送。自选分组卡片：")
        print(json.dumps(messages["custom"], ensure_ascii=False, indent=2))
        print("[PUSH] dry-run，全市场归类卡片：")
        print(json.dumps(messages["market"], ensure_ascii=False, indent=2))
        result["pushed"] = {"custom": False, "market": False}
        return result

    url = webhook_url if webhook_url is not None else config.PUSH_WEBHOOK_URL
    # 分别推送：先自选，后全市场，各自独立重试
    print("[PUSH] 推送自选分组归类卡片...")
    pushed_custom = push_to_feishu(url, messages["custom"])
    print("[PUSH] 推送全市场归类卡片...")
    pushed_market = push_to_feishu(url, messages["market"])
    result["pushed"] = {"custom": pushed_custom, "market": pushed_market}
    return result
