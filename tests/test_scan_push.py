# -*- coding: utf-8 -*-
"""
scan_push 单元测试（离线，不打真实网络/MCP/webhook）

运行方式（两种等价）：
  python tests/test_scan_push.py
  python -m unittest discover -s tests

覆盖：
- build_feishu_message：卡片结构、两侧（自选/全市场）渲染、空结果、失败侧
- run_classification：monkeypatch scan_*_groups，验证编排与一侧失败容错
- run_push：非交易日跳过、dry_run 不推送、交易日正常推送路径
- get_slot_query：未知 slot 返回 None
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest import mock

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


class GetSlotQueryTests(unittest.TestCase):
    def test_slot_query(self):
        q = scan_push.get_slot_query("933")
        self.assertTrue(q and "实体涨幅" in q, "933 返回有效 query")
        self.assertIsNone(scan_push.get_slot_query("9999"), "未知 slot 返回 None")


class BuildFeishuMessageTests(unittest.TestCase):
    def test_normal_two_sides(self):
        cls = {"slot": "1430", "query": "...", "custom": _FAKE_CUSTOM, "market": _FAKE_MARKET}
        msg = scan_push.build_feishu_message("1430", cls)
        self.assertEqual(msg["msg_type"], "interactive")
        self.assertIn("股池归因推送", msg["card"]["header"]["title"]["content"])
        elems = msg["card"]["elements"]

        # 两侧各有 column_set（三列统计卡片）
        col_sets = [e for e in elems if e.get("tag") == "column_set"]
        self.assertEqual(len(col_sets), 2, "两侧各一个 column_set")

        text = _md_texts(elems)
        # 自选侧：命中数 + 分组名
        self.assertIn("命中 3", text)
        self.assertIn("示例分组A", text)
        # 全市场侧：可归类数
        self.assertIn("可归类", text)
        self.assertIn("42", text)
        # 涨幅上色：<font color> 标签存在，且命中明细含 9.1%
        self.assertIn("<font color=", text)
        self.assertIn("9.1%", text)

    def test_empty_renders_hint(self):
        empty = {"query": "...", "pool_size": 0, "hit_total": 0, "group_hit_count": 0, "groups": []}
        cls = {"slot": "933", "query": "...", "custom": empty, "market": empty}
        msg = scan_push.build_feishu_message("933", cls)
        elems = msg["card"]["elements"]
        has_empty_hint = any("无符合条件" in e.get("text", {}).get("content", "")
                             for e in elems if e.get("tag") == "div")
        self.assertTrue(has_empty_hint, "空结果时渲染'无符合条件股票'提示")

    def test_error_side_isolated(self):
        cls = {
            "slot": "945", "query": "...",
            "custom": {"error": "自选股分组为空"},
            "market": _FAKE_MARKET,
        }
        msg = scan_push.build_feishu_message("945", cls)
        elems = msg["card"]["elements"]
        custom_div = next(e for e in elems
                          if e.get("tag") == "div" and "自选分组归类" in e.get("text", {}).get("content", ""))
        self.assertIn("❌", custom_div["text"]["content"])
        self.assertIn("自选股分组为空", custom_div["text"]["content"])


class RunClassificationTests(unittest.TestCase):
    def test_orchestration_and_error_isolation(self):
        import realtime_engine
        calls = {"custom": 0, "market": 0}

        def fake_custom(query):
            calls["custom"] += 1
            self.assertEqual(query, scan_push.PUSH_SLOTS["1430"]["query"], "query 应原样传入")
            return dict(_FAKE_CUSTOM)

        def fake_market(query):
            calls["market"] += 1
            # 模拟全市场侧失败，验证不影响自选侧
            raise RuntimeError("MCP 炸了")

        with mock.patch.object(realtime_engine, "scan_custom_groups", fake_custom), \
             mock.patch.object(realtime_engine, "scan_market_groups", fake_market):
            cls = scan_push.run_classification("1430")

        self.assertEqual(calls, {"custom": 1, "market": 1}, "两侧各调用一次")
        self.assertEqual(cls["query"], scan_push.PUSH_SLOTS["1430"]["query"])
        self.assertEqual(cls["custom"]["pool_size"], 106, "自选侧成功返回数据")
        self.assertIn("error", cls["market"])
        self.assertIn("MCP 炸了", cls["market"]["error"], "全市场侧失败被隔离")


class RunPushTests(unittest.TestCase):
    def test_non_trading_day_skip(self):
        import trade_calendar
        pushed = {"n": 0}

        def fake_push(url, message, **kw):
            pushed["n"] += 1
            return True

        with mock.patch.object(trade_calendar, "is_trading_day", lambda d: False), \
             mock.patch.object(scan_push, "push_to_feishu", fake_push):
            res = scan_push.run_push("1430")

        self.assertTrue(res.get("skipped"))
        self.assertFalse(res.get("is_trading_day"))
        self.assertEqual(pushed["n"], 0, "非交易日不推送")

    def test_dry_run_no_push(self):
        import trade_calendar
        import realtime_engine
        pushed = {"n": 0}

        def fake_push(url, message, **kw):
            pushed["n"] += 1
            return True

        with mock.patch.object(trade_calendar, "is_trading_day", lambda d: True), \
             mock.patch.object(scan_push, "push_to_feishu", fake_push), \
             mock.patch.object(realtime_engine, "scan_custom_groups", lambda q: dict(_FAKE_CUSTOM)), \
             mock.patch.object(realtime_engine, "scan_market_groups", lambda q: dict(_FAKE_MARKET)):
            res = scan_push.run_push("1430", dry_run=True)

        self.assertIn("messages", res)
        self.assertIn("custom", res["messages"])
        self.assertIn("market", res["messages"])
        self.assertEqual(res["messages"]["custom"]["msg_type"], "interactive")
        self.assertEqual(res["messages"]["market"]["msg_type"], "interactive")
        self.assertEqual(pushed["n"], 0)
        self.assertEqual(res["pushed"], {"custom": False, "market": False}, "dry-run 不调用 push_to_feishu")

    def test_real_trading_path(self):
        import trade_calendar
        import realtime_engine
        sent = {"calls": []}  # 记录每次推送的 (url, message)

        def fake_push(url, message, **kw):
            sent["calls"].append((url, message))
            return True

        with mock.patch.object(trade_calendar, "is_trading_day", lambda d: True), \
             mock.patch.object(scan_push, "push_to_feishu", fake_push), \
             mock.patch.object(realtime_engine, "scan_custom_groups", lambda q: dict(_FAKE_CUSTOM)), \
             mock.patch.object(realtime_engine, "scan_market_groups", lambda q: dict(_FAKE_MARKET)):
            res = scan_push.run_push("1430", webhook_url="https://example.test/hook")

        self.assertEqual(res.get("pushed"), {"custom": True, "market": True})
        self.assertEqual(len(sent["calls"]), 2, "推送了 2 次")
        self.assertTrue(all(c[0] == "https://example.test/hook" for c in sent["calls"]), "webhook_url 透传正确")
        # 第一张是自选(蓝头)，第二张是全市场(紫头)
        headers = [c[1]["card"]["header"]["template"] for c in sent["calls"]]
        self.assertEqual(headers, ["blue", "purple"], "先自选蓝头后全市场紫头")
        self.assertIn("自选分组归类", sent["calls"][0][1]["card"]["header"]["title"]["content"])
        self.assertIn("全市场归类", sent["calls"][1][1]["card"]["header"]["title"]["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
