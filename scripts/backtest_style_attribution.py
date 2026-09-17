#!/usr/bin/env python3
"""三指数归因回测引擎（命题 2：归因口径能否识别有延续性的真主线）。

数据来自 backfill_style_history.py 产出的 data/style_backtest.db。

检验设计（见 docs/architecture/DESIGN-style-index-attribution.md 第八节）：
  1. 延续性：T 日 Top-K 概念 → T+k 概念指数超额（相对当日全概念截面中位数），k=1/2/3/5
  2. 消融矩阵：过滤变体 {F0 无 / F1 伪概念黑名单 / F2 黑名单+150} × 排序键
     {contribution / hit / concept_chg} × K {3/5/10}
  3. 安慰剂：每日随机 K 概念（多种子）+ 错位配对（T 的 Top5 配 T+6 的超额，期望≈0）
  4. 等权校验：逐日 |等权成分涨跌幅 − 官方指数涨跌幅|（指数编制一致性）

输出：data/style_backtest/report.md + metrics.json

用法：python scripts/backtest_style_attribution.py [--index 883910.TI] [--seed 42]
"""
import argparse
import json
import random
import sqlite3
import statistics
from array import array
from collections import defaultdict
from datetime import datetime
from math import nan
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "style_backtest.db"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "style_backtest"

INDICES = {"883926.TI": "高贝塔值", "883409.TI": "近期强势", "883910.TI": "同花顺热股"}

# 伪概念黑名单 + 命名模式（与设计文档第四节一致）
CONCEPT_BLACKLIST = {
    "融资融券", "转融券标的", "沪股通", "深股通", "京股通", "中证500成份股",
    "同花顺漂亮100", "富时罗素概念", "MSCI中国", "标普道琼斯A股", "深成500",
    "上证50样本股", "上证180成份股", "证金持股", "汇金持股",
}
BLACKLIST_PATTERNS = ("股通", "融资融券", "成份股", "样本股", "融券",
                      "富时罗素", "MSCI", "标普道琼斯")
MAX_CONCEPT_MEMBERS = 150

FILTERS = {"F0_不过滤": None, "F1_黑名单": "blacklist", "F2_黑名单+150": 150}
SORTS = ("contribution", "hit", "concept_chg")
KS = (3, 5, 10)
HORIZONS = (1, 2, 3, 5)


def is_pseudo_concept(name: str) -> bool:
    return name in CONCEPT_BLACKLIST or any(p in name for p in BLACKLIST_PATTERNS)


def load_data():
    """K线用按代码的紧凑数组（交易日对齐，NaN 表缺失），省内存。"""
    db = sqlite3.connect(DB_PATH)
    days = [r[0] for r in db.execute(
        "SELECT DISTINCT trade_date FROM bt_progress WHERE done=1 ORDER BY 1")]
    day_idx = {d: i for i, d in enumerate(days)}
    n_days = len(days)

    members = defaultdict(dict)  # day -> index_code -> [stocks]
    for d, ic, sc in db.execute("SELECT trade_date, index_code, stock_code FROM bt_member"):
        members[d].setdefault(ic, []).append(sc)

    stock_map = defaultdict(dict)  # day -> stock -> [concept_code]
    concept_name = {}             # concept_code -> name（全窗口并集）
    for d, sc, cc, cn in db.execute(
            "SELECT trade_date, stock_code, concept_code, concept_name FROM bt_map"):
        stock_map[d].setdefault(sc, []).append(cc)
        concept_name.setdefault(cc, cn)

    kline = {}  # code -> array('d')，按 day_idx 对齐
    cur = {}
    for code, d, chg in db.execute(
            "SELECT code, trade_date, change_ratio FROM bt_kline "
            "WHERE change_ratio IS NOT NULL"):
        arr = cur.get(code)
        if arr is None:
            arr = cur[code] = array("d", [nan] * n_days)
        i = day_idx.get(d)
        if i is not None:
            arr[i] = chg
    kline = cur

    sizes = defaultdict(dict)  # snap_date -> concept_code -> n_members
    for snap, cc, n in db.execute(
            "SELECT snap_date, concept_code, n_members FROM bt_concept_size"):
        sizes[snap][cc] = n

    db.close()
    return days, day_idx, dict(members), dict(stock_map), kline, dict(sizes), concept_name


def size_lookup(snapshots, snaps_sorted, concept_code, day):
    """就近向前找概念规模快照；找不到返回 None（保守放行）。"""
    for s in reversed(snaps_sorted):
        if s <= day:
            n = snapshots[s].get(concept_code)
            if n is not None:
                return n
    return None


def base_attribution(day, mems, stock_map, chg_of, concept_name):
    """当日等权贡献（不过滤），返回 {concept_code: {name, contribution, hit}}。"""
    n = len(mems)
    agg = {}
    for sc in mems:
        chg = chg_of(sc, day)
        if chg is None:
            continue
        for cc in stock_map.get(day, {}).get(sc, []):
            a = agg.setdefault(cc, {"name": concept_name.get(cc, ""),
                                    "contribution": 0.0, "hit": 0})
            a["contribution"] += chg / n
            a["hit"] += 1
    return agg


def filter_variants(base, fname, chg_of, day, sizes, snaps_sorted, concept_name):
    """按过滤变体裁剪并挂上概念当日行情。"""
    attr = {}
    for cc, a in base.items():
        if fname != "F0_不过滤" and is_pseudo_concept(a["name"]):
            continue
        if fname == "F2_黑名单+150":
            sz = size_lookup(sizes, snaps_sorted, cc, day)
            if sz is not None and sz > MAX_CONCEPT_MEMBERS:
                continue
        chg_c = chg_of(cc, day)
        if chg_c is None:
            continue
        b = dict(a)
        b["concept_chg"] = chg_c
        attr[cc] = b
    return attr


def spearman(pairs_a, pairs_b):
    """两个同键排序的 Spearman 秩相关。"""
    common = sorted(set(pairs_a) & set(pairs_b))
    if len(common) < 10:
        return None
    by_a = sorted(common, key=lambda c: -pairs_a[c])
    by_b = sorted(common, key=lambda c: -pairs_b[c])
    rk_a = {c: r for r, c in enumerate(by_a)}
    rk_b = {c: r for r, c in enumerate(by_b)}
    nn = len(common)
    d2 = sum((rk_a[c] - rk_b[c]) ** 2 for c in common)
    return 1 - 6 * d2 / (nn * (nn * nn - 1))


def stats_of(vals):
    if not vals:
        return None
    win = sum(1 for v in vals if v > 0) / len(vals)
    mean = sum(vals) / len(vals)
    sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    # 简单 t 值；逐日观测有重叠（T+1 滚动），显著性解读时按报告注的保守口径
    tval = (mean / (sd / len(vals) ** 0.5)) if sd > 0 else 0.0
    return {"n": len(vals), "mean": round(mean, 4), "win": round(win, 4),
            "t": round(tval, 2)}


def run(days, day_idx, members, stock_map, kline, sizes, concept_name, idx_codes, seed):
    rnd = random.Random(seed)
    snaps_sorted = sorted(sizes)

    def chg_of(code, day):
        arr = kline.get(code)
        if arr is None:
            return None
        v = arr[day_idx[day]]
        return None if v != v else v  # NaN 检查

    # 概念截面：当日全部 885 概念指数行情（超额基准 = 截面中位数）
    all_concepts = sorted({cc for stocks in stock_map.values()
                           for lst in stocks.values() for cc in lst})
    concept_days = {d: {} for d in days}
    for cc in all_concepts:
        arr = kline.get(cc)
        if arr is None:
            continue
        for i, d in enumerate(days):
            v = arr[i]
            if v == v:  # 非 NaN
                concept_days[d][cc] = v

    results = {ic: {f: {s: {k: {h: [] for h in HORIZONS} for k in KS}
                        for s in SORTS} for f in FILTERS} for ic in idx_codes}
    ic_series = {ic: {f: [] for f in FILTERS} for ic in idx_codes}
    placebo = {ic: {k: {h: [] for h in HORIZONS} for k in KS} for ic in idx_codes}
    displaced = {ic: [] for ic in idx_codes}
    ewcheck = {ic: [] for ic in idx_codes}
    n_days_used = {ic: 0 for ic in idx_codes}

    for i, day in enumerate(days):
        future = {h: (days[i + h] if i + h < len(days) else None) for h in HORIZONS}
        if future[1] is None:
            continue
        for ic in idx_codes:
            mems = members.get(day, {}).get(ic)
            if not mems:
                continue
            chgs = [c for s in mems if (c := chg_of(s, day)) is not None]
            if not chgs:
                continue
            n_days_used[ic] += 1
            idx_chg = chg_of(ic, day)
            if idx_chg is not None:
                ewcheck[ic].append(abs(sum(chgs) / len(chgs) - idx_chg))

            base = base_attribution(day, mems, stock_map, chg_of, concept_name)
            nxt1 = concept_days.get(future[1], {})

            for fname in FILTERS:
                attr = filter_variants(base, fname, chg_of, day, sizes,
                                       snaps_sorted, concept_name)
                if not attr:
                    continue
                if nxt1:
                    contribs = {cc: a["contribution"] for cc, a in attr.items()}
                    ic_val = spearman(contribs, nxt1)
                    if ic_val is not None:
                        ic_series[ic][fname].append(ic_val)
                for skey in SORTS:
                    ranked = sorted(attr, key=lambda c: -attr[c][skey])
                    for K in KS:
                        topk = ranked[:K]
                        for h in HORIZONS:
                            fd = future[h]
                            if fd is None:
                                continue
                            fday = concept_days.get(fd, {})
                            if not fday:
                                continue
                            medh = statistics.median(fday.values())
                            results[ic][fname][skey][K][h].extend(
                                fday[cc] - medh for cc in topk if cc in fday)

            # 安慰剂1：当日概念全集随机抽 K
            cd_all = list(concept_days.get(day, {}))
            if cd_all:
                for K in KS:
                    pick = rnd.sample(cd_all, min(K, len(cd_all)))
                    for h in HORIZONS:
                        fd = future[h]
                        if fd is None:
                            continue
                        fday = concept_days.get(fd, {})
                        if not fday:
                            continue
                        medh = statistics.median(fday.values())
                        placebo[ic][K][h].extend(
                            fday[cc] - medh for cc in pick if cc in fday)

            # 安慰剂2：T 的 F2 Top5 配 T+6 的超额（与 T 无因果，期望≈0）
            if i + 6 < len(days):
                fb = concept_days.get(days[i + 6], {})
                if fb:
                    attr2 = filter_variants(base, "F2_黑名单+150", chg_of, day,
                                            sizes, snaps_sorted, concept_name)
                    if attr2:
                        top5 = sorted(attr2, key=lambda c: -attr2[c]["contribution"])[:5]
                        medb = statistics.median(fb.values())
                        displaced[ic].extend(fb[cc] - medb for cc in top5 if cc in fb)

    return summarize(results, ic_series, placebo, displaced, ewcheck, n_days_used)


def summarize(results, ic_series, placebo, displaced, ewcheck, n_days_used):
    out = {"n_days": {INDICES[ic]: n for ic, n in n_days_used.items()},
           "indices": {}, "placebo": {}, "ewcheck": {}}
    for ic in results:
        tag = INDICES[ic]
        out["indices"][tag] = {}
        for f in FILTERS:
            icv = ic_series[ic][f]
            out["indices"][tag][f] = {
                "rank_ic_mean": round(sum(icv) / len(icv), 4) if icv else None,
                "rank_ic_n": len(icv),
                "by_sort": {s: {f"K{k}": {f"T+{h}": stats_of(
                    results[ic][f][s][k][h]) for h in HORIZONS} for k in KS}
                    for s in SORTS},
            }
        out["placebo"][tag] = {
            f"random_K{k}_T+{h}": stats_of(placebo[ic][k][h])
            for k in KS for h in HORIZONS}
        out["placebo"][tag]["displaced_top5_at_T+6"] = stats_of(displaced[ic])
        dev = ewcheck[ic]
        out["ewcheck"][tag] = {
            "n": len(dev),
            "mean_abs_dev_bp": round(sum(dev) / len(dev) * 100, 2) if dev else None,
            "max_abs_dev_bp": round(max(dev) * 100, 2) if dev else None,
            "p99_abs_dev_bp": round(sorted(dev)[int(len(dev) * 0.99)] * 100, 2)
            if dev else None,
        }
    return out


def fmt_cell(st):
    if not st:
        return "—"
    return f"{st['mean']:+.3f}% / {st['win']:.0%} (t={st['t']})"


def write_report(rep):
    L = ["# 三指数归因口径回测报告", "",
         f"> 生成：{datetime.now().strftime('%Y-%m-%d %H:%M')} | "
         "命题 2：归因口径有效性（延续性 + 消融 + 安慰剂）", "",
         "> 单元格格式：T+k 平均超额 / 胜率（t 值）。超额 = 概念指数涨跌幅 − "
         "当日全概念截面中位数。t 值按逐日独立近似，Top-K 观测存在重叠，"
         "解读以均值幅度与安慰剂对照为主。", "",
         "## 数据完备性与等权校验", "",
         "| 指数 | 有效回测天数 | 等权均值偏差(bp) | 最大偏差(bp) | p99(bp) |",
         "|---|---|---|---|---|"]
    for tag, v in rep["ewcheck"].items():
        L.append(f"| {tag} | {rep['n_days'].get(tag, '?')} | {v['mean_abs_dev_bp']} "
                 f"| {v['max_abs_dev_bp']} | {v['p99_abs_dev_bp']} |")
    L.append("")

    for tag, fv in rep["indices"].items():
        L += [f"## {tag}", ""]
        for f, body in fv.items():
            L += [f"### 过滤变体：{f}（rank-IC 均值 {body['rank_ic_mean']}, "
                  f"n={body['rank_ic_n']}）", "",
                  "| 排序键 | K | T+1 超额/胜率(t) | T+2 | T+3 | T+5 |",
                  "|---|---|---|---|---|---|"]
            for s, kv in body["by_sort"].items():
                for kk, hv in kv.items():
                    row = [s, kk] + [fmt_cell(hv.get(f"T+{h}")) for h in HORIZONS]
                    if any(c != "—" for c in row[2:]):
                        L.append("| " + " | ".join(row) + " |")
            L.append("")
        pl = rep["placebo"][tag]
        r5 = pl.get("random_K5_T+1")
        dis = pl.get("displaced_top5_at_T+6")
        L += [f"**安慰剂对照**：随机 K5 T+1 = "
              f"{fmt_cell(r5)}；错位（T 的 Top5 @T+6）= {fmt_cell(dis)}", ""]

    (OUT_DIR / "report.md").write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=None, help="只测单一指数（默认三指数全测）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    days, day_idx, members, stock_map, kline, sizes, concept_name = load_data()
    idx_codes = [args.index] if args.index else list(INDICES)
    print(f"[LOAD] 交易日 {len(days)} 天，K线代码 {len(kline)} 个")

    rep = run(days, day_idx, members, stock_map, kline, sizes, concept_name,
              idx_codes, args.seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "metrics.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report(rep)
    print(f"[OUT] {OUT_DIR / 'report.md'}")


if __name__ == "__main__":
    main()
