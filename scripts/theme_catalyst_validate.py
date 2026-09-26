#!/usr/bin/env python3
"""题材催化反向归因：过去 N 个交易日逐日验证。

逐日执行归因（三指数成分池，point-in-time），汇总：主线分布、解释度、
无主线天数、池规模、涨跌停结构覆盖率、耗时。结果存
data/theme_catalyst/validate_{N}d.json。

用法：python scripts/theme_catalyst_validate.py [--days 60]
"""
import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import theme_catalyst as tc  # noqa: E402
from theme_catalyst import ThemeCatalystEngine  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "theme_catalyst"


def load_market(day):
    con = sqlite3.connect(f"file:{tc.TC_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT code, name, chg FROM tc_market WHERE trade_date=?", (day,)
    ).fetchall()
    con.close()
    return {c: {"name": n, "chg": g} for c, n, g in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    bt = sqlite3.connect(f"file:{tc.BT_DB}?mode=ro", uri=True)
    meta = bt.execute(
        "SELECT value FROM bt_meta WHERE key LIKE 'trade_days:%' "
        "ORDER BY key DESC").fetchone()
    all_days = meta[0].split(",")
    win = all_days[-args.days:]
    bt.close()
    print(f"[VAL] 窗口 {win[0]}~{win[-1]} 共 {len(win)} 天")

    eng = ThemeCatalystEngine()
    records = []
    t_all = time.time()
    for i, day in enumerate(win):
        pool = tc.get_style_members(day)
        market = load_market(day)
        t0 = time.time()
        try:
            r = eng.run(day, pool, market=market, log=lambda *a: None)
        except Exception as e:
            records.append({"date": day, "error": str(e)[:200]})
            print(f"[VAL] {day} 异常: {e}")
            continue
        dt = time.time() - t0
        rec = {
            "date": day, "pool": r["pool_size"], "universe": r["universe"],
            "n_cands": len(r["candidates"]), "runtime_s": round(dt, 2),
            "explain": r["explain_ratio"], "single": r["has_single_theme"],
            "primary": [
                {"name": p["name"], "code": p["theme_code"],
                 "score": p["score"], "hit": p["hit"],
                 "lift": round(p["lift"], 2)} for p in r["primary"]],
        }
        records.append(rec)
        if (i + 1) % 10 == 0:
            ok = [x for x in records if "error" not in x]
            print(f"[VAL] {i+1}/{len(win)}（成功 {len(ok)}）", flush=True)

    ok = [x for x in records if "error" not in x]
    err = [x for x in records if "error" in x]
    primary_cnt = Counter(
        p["name"] for x in ok for p in x["primary"])
    explains = [x["explain"] for x in ok]
    singles = sum(1 for x in ok if x["single"])
    runtimes = [x["runtime_s"] for x in ok]
    summary = {
        "window": [win[0], win[-1]], "n_days": len(win),
        "n_ok": len(ok), "n_err": len(err),
        "errors": err,
        "single_theme_days": singles,
        "no_single_theme_days": len(ok) - singles,
        "explain_mean": round(sum(explains) / len(explains), 4) if explains else None,
        "explain_min": min(explains) if explains else None,
        "pool_mean": round(sum(x['pool'] for x in ok) / len(ok), 1) if ok else None,
        "runtime_mean_s": round(sum(runtimes) / len(runtimes), 2) if runtimes else None,
        "runtime_max_s": max(runtimes) if runtimes else None,
        "primary_theme_frequency": dict(primary_cnt.most_common(15)),
        "records": records,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"validate_{args.days}d.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n[VAL] 完成：{len(ok)} 成功 / {len(err)} 异常，总耗时 "
          f"{(time.time()-t_all)/60:.1f} 分钟")
    print(f"[VAL] 单一主线日 {singles} / 无单一主线日 {len(ok)-singles}")
    print(f"[VAL] 解释度 mean={summary['explain_mean']} min={summary['explain_min']}")
    print(f"[VAL] 耗时 mean={summary['runtime_mean_s']}s max={summary['runtime_max_s']}s")
    print(f"[VAL] 主线频次 Top10: {dict(primary_cnt.most_common(10))}")
    print(f"[OUT] {out}")


if __name__ == "__main__":
    main()
