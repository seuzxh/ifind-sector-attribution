# -*- coding: utf-8 -*-
"""
iFinD API 接口连通性冒烟测试（真实接口，消耗少量配额）

运行方式：
  python tests/test_api.py            （直接运行：始终执行全部接口）
  IFIND_SMOKE=1 python -m unittest discover -s tests   （套件发现时需显式启用，默认跳过保持离线可跑）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import unittest

from ifind_hub import get_hub


def print_response(title: str, resp: dict, max_len: int = 2000):
    """格式化打印响应"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    text = json.dumps(resp, ensure_ascii=False, indent=2)
    print(text[:max_len])
    if len(text) > max_len:
        print(f"\n... (truncated, total {len(text)} chars)")


def _latest_trade_day() -> str:
    """最近一个已确认交易日（YYYYMMDD）。接口4 分时窗口须落在交易日上，否则 -4211。"""
    from trade_calendar import get_latest_trade_day
    return get_latest_trade_day() or "20260612"


@unittest.skipUnless(
    os.environ.get("IFIND_SMOKE"),
    "真实接口连通性冒烟：直接运行本文件，或设 IFIND_SMOKE=1 启用",
)
class IfindApiSmokeTests(unittest.TestCase):
    """五个接口 + 批量接口连通性；断言 errorcode 正常即可（数据形态人工看打印）。"""

    def _assert_ok(self, resp):
        self.assertIn(resp.get("errorcode"), (0, None),
                      f"接口返回错误: {json.dumps(resp, ensure_ascii=False)[:300]}")

    def test_interface_1_stock_concepts(self):
        """测试接口1: 个股所属同花顺概念"""
        client = get_hub().client
        resp = client.get_stock_concepts(
            stock_codes=["688001.SH", "600004.SH", "000001.SZ", "300001.SZ"],
            date="2026-06-13"
        )
        print_response("接口1: 个股所属同花顺概念", resp)
        self._assert_ok(resp)

    def test_interface_2_concept_members(self):
        """测试接口2: 概念板块成分股 (p03473)"""
        client = get_hub().client
        resp = client.get_concept_members(
            concept_code="886102.TI",
            date="20260613"
        )
        print_response("接口2: 概念板块成分股 (886102.TI)", resp)
        self._assert_ok(resp)

    def test_interface_3_history_quotation(self):
        """测试接口3: 历史行情日K"""
        client = get_hub().client
        resp = client.get_history_quotation(
            codes=["300033.SZ", "600030.SH"],
            start_date="2026-06-01",
            end_date="2026-06-13",
            indicators="preClose,open,high,low,close,changeRatio"
        )
        print_response("接口3: 历史行情日K", resp)
        self._assert_ok(resp)

    def test_interface_4_high_frequency(self):
        """测试接口4: 高频序列1min K（窗口取最近交易日，硬编码周末日期会 -4211 无交易日）"""
        client = get_hub().client
        day = _latest_trade_day()
        resp = client.get_high_frequency(
            codes=["300033.SZ"],
            start_time=f"{day} 09:30:00",
            end_time=f"{day} 10:00:00",
            indicators="open,high,low,close,changeRatio"
        )
        print_response(f"接口4: 高频序列1min K（{day}）", resp)
        self._assert_ok(resp)

    def test_interface_5_concept_basic_info(self):
        """测试接口5: 概念基本信息（字典初始化）"""
        client = get_hub().client
        test_codes = ["700301.TI", "700302.TI", "700303.TI", "700304.TI", "700305.TI"]
        resp = client.get_concept_basic_info(test_codes)
        print_response("接口5: 概念基本信息", resp)
        self._assert_ok(resp)

    def test_batch_concept_basic_info(self):
        """测试批量获取概念基本信息"""
        client = get_hub().client
        test_codes = ["700301.TI", "700302.TI", "700303.TI", "700304.TI", "700305.TI"]
        result = client.batch_get_concept_basic_info(test_codes, batch_size=3)
        print(f"\n{'='*60}")
        print("  批量接口5: 概念基本信息")
        print(f"{'='*60}")
        print(f"Total concepts: {len(result)}")
        for c in result[:3]:
            print(f"  {c['concept_code']}: {c['short_name']} ({c['full_name']})")
        self.assertTrue(result, "批量接口5应返回非空结果")


if __name__ == "__main__":
    # 直接运行 = 显式冒烟意图，自动开启门控
    os.environ.setdefault("IFIND_SMOKE", "1")
    unittest.main(verbosity=2)
