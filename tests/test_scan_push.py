# -*- coding: utf-8 -*-
"""
scan_push 单元测试（离线，不打真实网络/MCP/webhook）

运行方式: python tests/test_scan_push.py
（沿用本项目的 test_api.py 风格：普通函数 + run_all_tests，无 pytest 依赖）

测试覆盖：
- build_feishu_message：卡片结构、两侧（自选/全市场）渲染、空结果、失败侧
- run_classification：monkeypatch scan_*_groups，验证编排与一侧失败容错
- run_push：非交易日跳过、dry_run 不推送、交易日正常推送路径
- get_slot_query：未知 slot 返回 None
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scan_push


# ---------- 测试夹具：假归类结果 ----------
_FAKE_CUSTOM = {
    "query": "涨幅大于7%并且小于12.1%；未涨停；非ST",
    "pool_size": 106,
    "hit_total": 3,
    "group_hit_count": 2,
    "groups": [
        {
            "group_id": "G1", "group_name": "示例分组A",
            "hit_count": 2, "member_total": 25, "coverage": 0.08,
            "hit_avg_change": 8.6,
            "hits": [
                {"code": "000001.SZ", "name": "平安银行", "change_ratio": 9.1},
                {"code": "600000.SH", "name": "浦发银行", "change_ratio": 8.1},
            ],
        },
        {
            "group_id": "G2", "group_name": "示例分组B",
            "hit_count": 1, "member_total": 10, "coverage": 0.1,
            "hit_avg_change": 7.5,
            "hits": [{"code": "300001.SZ", "name": "特锐德", "change_ratio": 7.5}],
        },
    ],
}

_FAKE_MARKET = {
    "query": "涨幅大于7%并且小于12.1%；未涨停；非ST",
    "pool_size": 106,
    "hit_total": 42,
    "group_hit_count": 1,
    "groups": [
        {
            "group_id": "884001.TI", "group_name": "示例板块",
            "hit_count": 3, "member_total": 25, "coverage": 0.12,
            "hit_avg_change": 8.6,
            "hits": [{"code": "000955.SZ", "name": "欣龙控股", "change_ratio": 8.8}],
        },
    ],
}


def _ok(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return cond


def test_get_slot_query():
    print("\n=== test_get_slot_query ===")
    q = scan_push.get_slot_query("933")
    ok1 = _ok(q and "实体涨幅" in q, "933 返回有效 query")
    ok2 = _ok(scan_push.get_slot_query("9999") is None, "未知 slot 返回 None")
    return ok1 and ok2


def _md_texts(elems):
    """把 elements 里所有 lark_md 的 content 拼成一个字符串（剥 markdown 星号），便于子串断言。"""
    parts = []
    for e in elems:
        t = e.get("text", {})
        if isinstance(t, dict) and t.get("tag") == "lark_md" and t.get("content"):
            parts.append(str(t["content"]).replace("*", ""))
        # column_set 内的列文本也收进来
        for col in e.get("columns", []) or []:
            for ce in col.get("elements", []) or []:
                ct = ce.get("text", {})
                if isinstance(ct, dict) and ct.get("tag") == "lark_md" and ct.get("content"):
                    parts.append(str(ct["content"]).replace("*", ""))
    return "\n".join(parts)


def test_build_feishu_message_normal():
    print("\n=== test_build_feishu_message_normal ===")
    cls = {"slot": "1430", "query": "...", "custom": _FAKE_CUSTOM, "market": _FAKE_MARKET}
    msg = scan_push.build_feishu_message("1430", cls)
    ok_type = _ok(msg["msg_type"] == "interactive", "msg_type=interactive")
    ok_header = _ok("股池归因推送" in msg["card"]["header"]["title"]["content"], "卡片标题含名称")
    elems = msg["card"]["elements"]

    # 两侧各有 column_set（三列统计卡片）
    col_sets = [e for e in elems if e.get("tag") == "column_set"]
    ok_columns = _ok(len(col_sets) == 2, f"两侧各一个 column_set（实得 {len(col_sets)}）")

    text = _md_texts(elems)
    # 自选侧：命中数 + 分组名
    ok_custom = _ok("命中 3" in text and "示例分组A" in text, "自选侧渲染命中数与分组名")
    # 全市场侧：可归类（列标签与数值42，列内为 换行 分隔）
    ok_market = _ok("可归类" in text and "42" in text, "全市场侧渲染可归类数")
    # 涨幅上色：<font color> 标签存在，且命中明细含 9.1%
    ok_color = _ok("<font color=" in text and "9.1%" in text, "涨幅按 <font color> 上色")
    return all([ok_type, ok_header, ok_columns, ok_custom, ok_market, ok_color])


def test_build_feishu_message_empty():
    print("\n=== test_build_feishu_message_empty ===")
    empty = {"query": "...", "pool_size": 0, "hit_total": 0, "group_hit_count": 0, "groups": []}
    cls = {"slot": "933", "query": "...", "custom": empty, "market": empty}
    msg = scan_push.build_feishu_message("933", cls)
    elems = msg["card"]["elements"]
    has_empty_hint = any("无符合条件" in e.get("text", {}).get("content", "") for e in elems if e.get("tag") == "div")
    ok = _ok(has_empty_hint, "空结果时渲染'无符合条件股票'提示")
    return ok


def test_build_feishu_message_error_side():
    print("\n=== test_build_feishu_message_error_side ===")
    cls = {
        "slot": "945", "query": "...",
        "custom": {"error": "自选股分组为空"},
        "market": _FAKE_MARKET,
    }
    msg = scan_push.build_feishu_message("945", cls)
    elems = msg["card"]["elements"]
    custom_div = next(e for e in elems if e.get("tag") == "div" and "自选分组归类" in e.get("text", {}).get("content", ""))
    ok = _ok("❌" in custom_div["text"]["content"] and "自选股分组为空" in custom_div["text"]["content"],
             "失败侧渲染错误标记与原因")
    return ok


def test_run_classification_with_fake_scan():
    print("\n=== test_run_classification_with_fake_scan ===")
    calls = {"custom": 0, "market": 0}

    def fake_custom(query):
        calls["custom"] += 1
        assert query == scan_push.PUSH_SLOTS["1430"]["query"], "query 应原样传入"
        return dict(_FAKE_CUSTOM)

    def fake_market(query):
        calls["market"] += 1
        # 模拟全市场侧失败，验证不影响自选侧
        raise RuntimeError("MCP 炸了")

    import realtime_engine
    orig_c, orig_m = realtime_engine.scan_custom_groups, realtime_engine.scan_market_groups
    realtime_engine.scan_custom_groups = fake_custom
    realtime_engine.scan_market_groups = fake_market
    try:
        cls = scan_push.run_classification("1430")
    finally:
        realtime_engine.scan_custom_groups = orig_c
        realtime_engine.scan_market_groups = orig_m

    ok_calls = _ok(calls == {"custom": 1, "market": 1}, "两侧各调用一次")
    ok_query = _ok(cls["query"] == scan_push.PUSH_SLOTS["1430"]["query"], "结果含 query")
    ok_custom_ok = _ok(cls["custom"]["pool_size"] == 106, "自选侧成功返回数据")
    ok_market_err = _ok("error" in cls["market"] and "MCP 炸了" in cls["market"]["error"],
                        "全市场侧失败被隔离，置 error 字段")
    return all([ok_calls, ok_query, ok_custom_ok, ok_market_err])


def test_run_push_non_trading_day_skip():
    print("\n=== test_run_push_non_trading_day_skip ===")
    import trade_calendar
    orig = trade_calendar.is_trading_day
    trade_calendar.is_trading_day = lambda d: False
    pushed = {"n": 0}

    def fake_push(url, message, **kw):
        pushed["n"] += 1
        return True

    orig_push = scan_push.push_to_feishu
    scan_push.push_to_feishu = fake_push
    try:
        res = scan_push.run_push("1430")
    finally:
        trade_calendar.is_trading_day = orig
        scan_push.push_to_feishu = orig_push

    ok_skip = _ok(res.get("skipped") is True and res.get("is_trading_day") is False, "非交易日标记 skipped")
    ok_nopush = _ok(pushed["n"] == 0, "非交易日不推送")
    return ok_skip and ok_nopush


def test_run_push_dry_run_no_push():
    print("\n=== test_run_push_dry_run_no_push ===")
    import trade_calendar
    orig = trade_calendar.is_trading_day
    trade_calendar.is_trading_day = lambda d: True
    pushed = {"n": 0}

    def fake_push(url, message, **kw):
        pushed["n"] += 1
        return True

    orig_push = scan_push.push_to_feishu
    scan_push.push_to_feishu = fake_push

    import realtime_engine
    orig_c, orig_m = realtime_engine.scan_custom_groups, realtime_engine.scan_market_groups
    realtime_engine.scan_custom_groups = lambda query: dict(_FAKE_CUSTOM)
    realtime_engine.scan_market_groups = lambda query: dict(_FAKE_MARKET)
    try:
        res = scan_push.run_push("1430", dry_run=True)
    finally:
        trade_calendar.is_trading_day = orig
        scan_push.push_to_feishu = orig_push
        realtime_engine.scan_custom_groups = orig_c
        realtime_engine.scan_market_groups = orig_m

    ok_msg = _ok("messages" in res and "custom" in res["messages"] and "market" in res["messages"],
                 "dry-run 仍组装自选/全市场两条消息")
    ok_card = _ok(res["messages"]["custom"]["msg_type"] == "interactive"
                  and res["messages"]["market"]["msg_type"] == "interactive", "两条均为 interactive 卡片")
    ok_nopush = _ok(pushed["n"] == 0
                    and res["pushed"] == {"custom": False, "market": False}, "dry-run 不调用 push_to_feishu")
    return all([ok_msg, ok_card, ok_nopush])


def test_run_push_real_trading_path():
    print("\n=== test_run_push_real_trading_path ===")
    import trade_calendar
    orig = trade_calendar.is_trading_day
    trade_calendar.is_trading_day = lambda d: True
    sent = {"calls": []}  # 记录每次推送的 (url, message)

    def fake_push(url, message, **kw):
        sent["calls"].append((url, message))
        return True

    orig_push = scan_push.push_to_feishu
    scan_push.push_to_feishu = fake_push

    import realtime_engine
    orig_c, orig_m = realtime_engine.scan_custom_groups, realtime_engine.scan_market_groups
    realtime_engine.scan_custom_groups = lambda query: dict(_FAKE_CUSTOM)
    realtime_engine.scan_market_groups = lambda query: dict(_FAKE_MARKET)
    try:
        res = scan_push.run_push("1430", webhook_url="https://example.test/hook")
    finally:
        trade_calendar.is_trading_day = orig
        scan_push.push_to_feishu = orig_push
        realtime_engine.scan_custom_groups = orig_c
        realtime_engine.scan_market_groups = orig_m

    ok_pushed = _ok(res.get("pushed") == {"custom": True, "market": True}, "自选/全市场都推送成功")
    ok_two = _ok(len(sent["calls"]) == 2, f"推送了 2 次（实得 {len(sent['calls'])}）")
    ok_url = _ok(all(c[0] == "https://example.test/hook" for c in sent["calls"]), "两次 webhook_url 都透传正确")
    # 第一张是自选(蓝头)，第二张是全市场(紫头)
    headers = [c[1]["card"]["header"]["template"] for c in sent["calls"]]
    ok_order = _ok(headers == ["blue", "purple"], f"先自选蓝头后全市场紫头（实得 {headers}）")
    ok_title = _ok("自选分组归类" in sent["calls"][0][1]["card"]["header"]["title"]["content"]
                   and "全市场归类" in sent["calls"][1][1]["card"]["header"]["title"]["content"],
                   "两张卡片标题分别为自选/全市场")
    return all([ok_pushed, ok_two, ok_url, ok_order, ok_title])


def run_all_tests():
    print("\n" + "=" * 60)
    print("  scan_push 单元测试开始")
    print("=" * 60)
    tests = [
        ("get_slot_query", test_get_slot_query),
        ("build_feishu_message_normal", test_build_feishu_message_normal),
        ("build_feishu_message_empty", test_build_feishu_message_empty),
        ("build_feishu_message_error_side", test_build_feishu_message_error_side),
        ("run_classification_with_fake_scan", test_run_classification_with_fake_scan),
        ("run_push_non_trading_day_skip", test_run_push_non_trading_day_skip),
        ("run_push_dry_run_no_push", test_run_push_dry_run_no_push),
        ("run_push_real_trading_path", test_run_push_real_trading_path),
    ]
    all_ok = True
    for name, fn in tests:
        try:
            ok = fn()
            if not ok:
                all_ok = False
        except Exception as e:
            import traceback
            print(f"  [FAIL] {name} 抛异常: {e}")
            traceback.print_exc()
            all_ok = False
    print("\n" + "=" * 60)
    print(f"  测试完成：{'全部 PASS ✅' if all_ok else '存在 FAIL ❌'}")
    print("=" * 60)
    return all_ok


if __name__ == "__main__":
    ok = run_all_tests()
    sys.exit(0 if ok else 1)
