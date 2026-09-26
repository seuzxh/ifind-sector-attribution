#!/usr/bin/env python3
"""题材催化反向归因运行器。

用法：
  python scripts/theme_catalyst_run.py --date 20260911 --pool style_indices   # 历史日离线验证
  python scripts/theme_catalyst_run.py --pool style_indices                   # 当日
  python scripts/theme_catalyst_run.py --pool "smart:涨幅大于7%的A股"          # smart_pick 自然语言池
  python scripts/theme_catalyst_run.py --pool "codes:600519.SH,000021.SZ,..."  # 外部代码
  可选 --selfcheck 输出 Lift 人工复算样例

历史日的全市场横截面（题材全成分涨跌停结构用）由日K构建并缓存到 tc_market 表。
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import theme_catalyst as tc  # noqa: E402
from theme_catalyst import run_pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def selfcheck(result, log=print):
    """验收抽检：Lift 人工复算 + 宽泛概念抑制检查。"""
    log("\n[SELFCHECK] Lift 人工复算（Top 主线）")
    d = result["detail"]
    br = result["base_rate"]
    for p in result["primary"][:2]:
        cc = p["theme_code"]
        hit, size = p["hit"], p["size"]
        if not size:
            continue
        purity = hit / size
        lift_manual = purity / br
        log(f"  {p['name']}({cc}): hit={hit} size={size} |S|={result['pool_size']}"
            f" |U|={result['universe']} → purity={purity:.3f} "
            f"lift_manual={lift_manual:.2f} vs 引擎 {p['lift']:.2f}"
            f" {'✅' if abs(lift_manual - p['lift']) < 0.05 else '❌'}")
    # 宽泛概念抑制：命中数最多者不应必然排第一
    if d:
        top_hit = max(d.values(), key=lambda v: v["hit"])
        top_score = max(d.values(), key=lambda v: v["score"])
        log(f"[SELFCHECK] 宽泛概念抑制: 命中最多={top_hit['name']}"
            f"(hit={top_hit['hit']}, score={top_hit['score']}) vs "
            f"得分最高={top_score['name']}(hit={top_score['hit']}, "
            f"score={top_score['score']})"
            " → 宽概念未霸榜 ✅" if top_hit is not top_score else
            f"[SELFCHECK] 宽泛概念抑制: 命中最多者同时得分最高（{top_hit['name']}），"
            "需人工判断是否真为主线")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYYMMDD，默认当日")
    ap.add_argument("--pool", default="style_indices")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    day = (args.date or "").replace("-", "") or __import__("datetime").datetime \
        .now().strftime("%Y%m%d")

    result = run_pipeline(day, args.pool)
    print(f"[RUN] {day} 池={result.get('pool_rule', args.pool)}")

    if "error" in result:
        print("[ERR]", result)
        return
    print(f"\n===== {day} 题材催化归因（池 {result['pool_size']} 只 / "
          f"U {result['universe']}）=====")
    print(f"主线解释度: {result['explain_ratio']:.1%} "
          f"({'有单一主线' if result['has_single_theme'] else '⚠️ 题材分散/无单一主线'})")
    for i, p in enumerate(result["primary"], 1):
        print(f"\n#{i} [{p['role']}] {p['name']} ({p['theme_code']}) "
              f"score={p['score']}")
        print(f"    hit={p['hit']} size={p['size']} lift={p['lift']:.1f}x "
              f"contribution={p['contribution']:.1%} sync={p['sync']:.2f}")
        lp, lt = p["lim_pool"], p["lim_theme"]
        print(f"    涨跌停(池内): 涨停{lp['up']}/跌停{lp['down']} "
              f"封板率{fmt(lp['seal_rate'])} 炸板率{fmt(lp['break_rate'])}; "
              f"(全成分): 涨停{lt['up']}/{lt['n']}")
        print(f"    连板高度={p['max_height']} 梯队={p['ladder']}")
        top3 = ", ".join(sorted(p["hit_codes"])[:8])
        print(f"    命中(前8): {top3}")
    print("\n候选榜 Top10:")
    for c in result["candidates"]:
        print(f"  {c['score']:6.1f} {c['name']:<12} hit={c['hit']:<4} "
              f"lift={c['lift']:<7} {c['role']}")
    if args.selfcheck:
        selfcheck(result)


def fmt(x):
    return f"{x:.0%}" if x is not None else "-"


if __name__ == "__main__":
    main()
