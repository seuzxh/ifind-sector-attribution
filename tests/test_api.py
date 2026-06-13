# -*- coding: utf-8 -*-
"""
iFinD API 接口测试脚本
运行方式: python tests/test_api.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from ifind_client import IFindClient


def print_response(title: str, resp: dict, max_len: int = 2000):
    """格式化打印响应"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    text = json.dumps(resp, ensure_ascii=False, indent=2)
    print(text[:max_len])
    if len(text) > max_len:
        print(f"\n... (truncated, total {len(text)} chars)")


def test_interface_1_stock_concepts():
    """测试接口1: 个股所属同花顺概念"""
    client = IFindClient()
    resp = client.get_stock_concepts(
        stock_codes=["688001.SH", "600004.SH", "000001.SZ", "300001.SZ"],
        date="2026-06-13"
    )
    print_response("接口1: 个股所属同花顺概念", resp)
    return resp


def test_interface_2_concept_members():
    """测试接口2: 概念板块成分股 (p03473)"""
    client = IFindClient()
    resp = client.get_concept_members(
        concept_code="886102.TI",
        date="20260613"
    )
    print_response("接口2: 概念板块成分股 (886102.TI)", resp)
    return resp


def test_interface_3_history_quotation():
    """测试接口3: 历史行情日K"""
    client = IFindClient()
    resp = client.get_history_quotation(
        codes=["300033.SZ", "600030.SH"],
        start_date="2026-06-01",
        end_date="2026-06-13",
        indicators="preClose,open,high,low,close,changeRatio"
    )
    print_response("接口3: 历史行情日K", resp)
    return resp


def test_interface_4_high_frequency():
    """测试接口4: 高频序列1min K"""
    client = IFindClient()
    resp = client.get_high_frequency(
        codes=["300033.SZ"],
        start_time="2026-06-13 09:30:00",
        end_time="2026-06-13 10:00:00",
        indicators="open,high,low,close,changeRatio"
    )
    print_response("接口4: 高频序列1min K", resp)
    return resp


def test_interface_5_concept_basic_info():
    """测试接口5: 概念基本信息（字典初始化）"""
    client = IFindClient()
    # 取少量概念代码测试
    test_codes = ["700301.TI", "700302.TI", "700303.TI", "700304.TI", "700305.TI"]
    resp = client.get_concept_basic_info(test_codes)
    print_response("接口5: 概念基本信息", resp)
    return resp


def test_batch_concept_basic_info():
    """测试批量获取概念基本信息"""
    client = IFindClient()
    test_codes = ["700301.TI", "700302.TI", "700303.TI", "700304.TI", "700305.TI"]
    result = client.batch_get_concept_basic_info(test_codes, batch_size=3)
    print(f"\n{'='*60}")
    print("  批量接口5: 概念基本信息")
    print(f"{'='*60}")
    print(f"Total concepts: {len(result)}")
    for c in result[:3]:
        print(f"  {c['concept_code']}: {c['short_name']} ({c['full_name']})")
    return result


def run_all_tests():
    """运行全部接口测试"""
    print("\n" + "="*60)
    print("  iFinD API 接口测试开始")
    print("="*60)

    tests = [
        ("接口1", test_interface_1_stock_concepts),
        ("接口2", test_interface_2_concept_members),
        ("接口3", test_interface_3_history_quotation),
        ("接口4", test_interface_4_high_frequency),
        ("接口5", test_interface_5_concept_basic_info),
        ("批量接口5", test_batch_concept_basic_info),
    ]

    results = {}
    for name, test_func in tests:
        try:
            results[name] = test_func()
            print(f"\n  [OK] {name} 测试通过")
        except Exception as e:
            print(f"\n  [FAIL] {name} 测试失败: {e}")
            results[name] = None

    print("\n" + "="*60)
    print("  测试完成")
    print("="*60)
    return results


if __name__ == "__main__":
    run_all_tests()
