# -*- coding: utf-8 -*-
"""
sector-hub 切换等价性验证。
用法（在项目根目录、vibe-trading python）：
  PYTHONPATH=. python scripts/verify_sector_hub_equivalence.py capture  # 切换前捕获
  PYTHONPATH=. python scripts/verify_sector_hub_equivalence.py verify   # 切换后对比
基线存 data/equivalence_baseline.json（gitignore）。
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "data", "equivalence_baseline.json")
DB = os.path.join(ROOT, "data", "sector_attribution.db")

import api_server  # noqa: E402  （import 即触发 Database() 初始化，只读快照安全）


def _latest_strength_date():
    with sqlite3.connect(DB) as conn:
        row = conn.execute("SELECT MAX(calc_date) FROM concept_strength").fetchone()
        return row[0] or ""


def _db_fingerprint():
    fp = {}
    with sqlite3.connect(DB) as conn:
        for table, date_col in [("ths_concept_dict", "update_date"),
                                ("concept_members", "member_date"),
                                ("stock_concept_map", "map_date"),
                                ("watched_concepts", None)]:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            mx = None
            if date_col:
                mx = conn.execute(f"SELECT MAX({date_col}) FROM {table}").fetchone()[0]
            fp[table] = {"count": cnt, "max_date": mx}
    return fp


def _snapshot():
    date = _latest_strength_date()
    out = {"endpoints": {}, "db": _db_fingerprint()}
    calls = {
        "concept_list": lambda: api_server.get_concept_list(),
        "watched": lambda: api_server.sector_manage_watched(),
        "dates": lambda: api_server.get_available_dates(),
        "history_sector": lambda: api_server.get_history_dashboard(
            date=date, scope="sector", force_calc=False),
    }
    for name, fn in calls.items():
        try:
            out["endpoints"][name] = fn()
        except Exception as e:  # 记录错误本身也是基线的一部分
            out["endpoints"][name] = {"__error__": str(e)}
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    snap = _snapshot()
    if mode == "capture":
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2, sort_keys=True)
        print(f"[EQUIV] 基线已捕获 → {BASELINE}")
        return 0
    if mode != "verify" or not os.path.exists(BASELINE):
        print("用法: capture | verify（verify 前须先 capture）")
        return 2
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    diffs = []
    for key in ("endpoints", "db"):
        for k in sorted(set(base[key]) | set(snap[key])):
            a, b = base[key].get(k), snap[key].get(k)
            if json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str):
                diffs.append(f"[DIFF] {key}.{k}:\n  before={json.dumps(a, default=str)[:500]}\n  after ={json.dumps(b, default=str)[:500]}")
    if diffs:
        print("\n".join(diffs))
        print(f"[EQUIV] ❌ {len(diffs)} 处不一致")
        return 1
    print("[EQUIV] ✅ 端点响应与 DB 指纹与基线完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
