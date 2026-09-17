#!/usr/bin/env python3
"""开盘强势扫描：自适应权重学习器（滚动 IC 法）+ walk-forward 验证。

算法（见 docs/architecture/DESIGN-open-strength-scan.md 第十节）：
  IC_i(T) = spearman(z_i, 当日9:40→收盘涨幅)          # 池内横截面，逐日
  滚动 N=60 日：IC_IR_i = mean/std，t_i = mean/std×√N
  w_IC ∝ max(IC_IR_i, 0)（仅 t≥2 的维度），w = α·w_IC + (1−α)·等权

walk-forward：T 日用 [T−N, T−1] 拟合 → T 日评估 Top-K 前瞻超额（相对池内中位数）
与胜率；对照 = 固定权重（0.2/0.25/0.3/0.25）与随机权重。

输出：data/open_scan/weights.json（当前权重+诊断）+ walkforward_report.md

用法：python scripts/open_weight_learner.py [--window 60] [--alpha 0.5] [--seed 42]
"""
import argparse
import json
import random
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "open_scan"
DB = ROOT / "data" / "open_scan.db"

DIMS = ("auc_chg", "speed", "body", "vol_ratio")
FIXED_W = {"auc_chg": 0.20, "speed": 0.25, "body": 0.30, "vol_ratio": 0.25}
T_GATE = 2.0     # 显著性门控
TOPK = (10, 20)


def load_snapshots():
    db = sqlite3.connect(DB)
    days = [r[0] for r in db.execute(
        "SELECT DISTINCT trade_date FROM snapshot ORDER BY 1")]
    per_day = {}
    for d, sc, a, s, b, v, fwd in db.execute(
            "SELECT trade_date, stock_code, auc_chg, speed, body, vol_ratio, "
            "fwd_ret FROM snapshot WHERE fwd_ret IS NOT NULL"):
        per_day.setdefault(d, []).append(
            {"code": sc, "auc_chg": a, "speed": s, "body": b,
             "vol_ratio": v, "fwd": fwd})
    db.close()
    return days, per_day


def zscore(vals):
    m = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    if sd == 0:
        return [0.0] * len(vals)
    return [(v - m) / sd for v in vals]


def day_ic(rows):
    """单日各维度 z 值与 fwd 的 Spearman IC。样本不足返回 {}。"""
    if len(rows) < 30:
        return {}
    ics = {}
    fwds = [r["fwd"] for r in rows]
    for dim in DIMS:
        vals = [r[dim] for r in rows]
        if any(v is None for v in vals):
            # 该日该维度有缺失，剔除缺失样本后再算
            pairs = [(r[dim], r["fwd"]) for r in rows if r[dim] is not None]
            if len(pairs) < 30:
                continue
            vals = [p[0] for p in pairs]
            fwds2 = [p[1] for p in pairs]
        else:
            fwds2 = fwds
        ics[dim] = spearman(zscore(vals), fwds2)
    return ics


def spearman(xs, ys):
    """已算好的序列直接秩相关（对 ys 排名，xs 用 z 值转秩）。"""
    rx = rankof(xs)
    ry = rankof(ys)
    n = len(xs)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - 6 * d2 / (n * (n * n - 1))


def rankof(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def learn_weights(ic_window):
    """ic_window: {dim: [IC,...]}（滚动窗口内逐日 IC）→ 学习权重 + 诊断。"""
    diag, w_ic = {}, {}
    for dim in DIMS:
        ics = [x for x in ic_window.get(dim, []) if x is not None]
        if len(ics) < 20:
            diag[dim] = {"n": len(ics), "skip": "样本不足"}
            continue
        mean = statistics.mean(ics)
        sd = statistics.pstdev(ics) if len(ics) > 1 else 0.0
        t = mean / (sd / len(ics) ** 0.5) if sd > 0 else 0.0
        ir = mean / sd if sd > 0 else 0.0
        diag[dim] = {"n": len(ics), "ic_mean": round(mean, 4),
                     "ic_ir": round(ir, 4), "t": round(t, 2)}
        if mean > 0 and t >= T_GATE:
            w_ic[dim] = ir
    eq = 1.0 / len(DIMS)
    if not w_ic:
        weights = {d: eq for d in DIMS}
        note = "无显著维度，退回等权"
    else:
        s = sum(w_ic.values())
        raw = {d: v / s for d, v in w_ic.items()}
        weights = {d: round(args_alpha * raw.get(d, 0.0)
                            + (1 - args_alpha) * eq, 4) for d in DIMS}
        note = f"显著维度: {sorted(w_ic)}"
    return weights, diag, note


def composite(rows, weights):
    out = []
    for r in rows:
        s = 0.0
        for dim in DIMS:
            v = r[dim]
            if v is None:
                continue
            s += weights[dim] * r.get(f"_z_{dim}", 0.0)
        out.append((r["code"], s, r["fwd"]))
    return out


def eval_topk(scored, k):
    """Top-K 前瞻超额（相对池内中位数）与胜率。"""
    if len(scored) < k + 10:
        return None
    med = statistics.median([x[2] for x in scored])
    top = sorted(scored, key=lambda x: -x[1])[:k]
    excess = [x[2] - med for x in top]
    win = sum(1 for e in excess if e > 0) / len(excess)
    return {"mean": sum(excess) / len(excess), "win": win}


def main():
    global args_alpha
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=60)
    ap.add_argument("--alpha", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    args_alpha = args.alpha
    rnd = random.Random(args.seed)

    days, per_day = load_snapshots()
    days = [d for d in days if len(per_day.get(d, [])) >= 30]
    print(f"[LOAD] 有效交易日 {len(days)} 天"
          f"（{days[0]}~{days[-1]}，样本≥30）")
    if len(days) < args.window + 10:
        print("[ERR] 样本不足以做 walk-forward，先只输出当前全窗口学习权重")

    # 逐日 IC（预计算）
    ics_by_day = {}
    for d in days:
        icd = day_ic(per_day[d])
        if icd:
            ics_by_day[d] = icd

    # —— 当前权重（全窗口）——
    ic_window = {dim: [ics_by_day[d].get(dim) for d in days
                       if d in ics_by_day] for dim in DIMS}
    weights, diag, note = learn_weights(ic_window)
    print(f"[WEIGHTS] {weights}  # {note}")

    # —— walk-forward ——
    wf = {"learned": {k: [] for k in TOPK}, "fixed": {k: [] for k in TOPK},
          "random": {k: [] for k in TOPK}}
    ic_hist = []
    for i, d in enumerate(days):
        ic_hist.append(d)
        if i < args.window or d not in per_day:
            continue
        win_days = [x for x in ic_hist[-args.window - 1:-1]]
        icw = {dim: [ics_by_day[x].get(dim) for x in win_days
                     if x in ics_by_day] for dim in DIMS}
        wl, _, _ = learn_weights(icw)
        rows = per_day[d]
        # 当日 z-score
        zrows = []
        for dim in DIMS:
            vals = [r[dim] for r in rows]
            zs = zscore([v if v is not None else 0.0 for v in vals])
            for r, z in zip(rows, zs):
                r[f"_z_{dim}"] = z
        scored = composite(rows, wl)
        scored_f = composite(rows, FIXED_W)
        wr = {dim: rnd.random() for dim in DIMS}
        ssum = sum(wr.values())
        wr = {k: v / ssum for k, v in wr.items()}
        scored_r = composite(rows, wr)
        for k in TOPK:
            for tag, sc in (("learned", scored), ("fixed", scored_f),
                            ("random", scored_r)):
                e = eval_topk(sc, k)
                if e:
                    wf[tag][k].append(e)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result = {"as_of": days[-1], "window": args.window,
              "alpha": args.alpha, "weights": weights,
              "diagnostics": diag, "note": note,
              "walk_forward": {
                  tag: {f"K{k}": {
                      "n": len(v),
                      "mean_excess": round(statistics.mean(
                          [e["mean"] for e in v]), 4) if v else None,
                      "mean_win": round(statistics.mean(
                          [e["win"] for e in v]), 4) if v else None,
                  } for k, v in ks.items()}
                  for tag, ks in wf.items()}}
    (OUT_DIR / "weights.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["# 开盘强势扫描：自适应权重 walk-forward 验证", "",
         f"> 生成：{datetime.now().strftime('%Y-%m-%d %H:%M')} | "
         f"窗口 N={args.window}，α={args.alpha}，样本 {len(days)} 个交易日", "",
         f"## 当前学习权重（{note}）", "",
         "| 维度 | 权重 | IC均值 | IC_IR | t值 | n |", "|---|---|---|---|---|---|"]
    for dim in DIMS:
        dg = diag[dim]
        L.append(f"| {dim} | {weights[dim]} | {dg.get('ic_mean', '-')} | "
                 f"{dg.get('ic_ir', '-')} | {dg.get('t', '-')} | {dg.get('n', '-')} |")
    L += ["", "## walk-forward 对比（Top-K 前瞻超额/胜率）", "",
          "| 方案 | K | 平均超额% | 平均胜率 | n日 |", "|---|---|---|---|---|"]
    for tag, label in (("learned", "学习权重"), ("fixed", "固定权重"),
                       ("random", "随机权重")):
        for k in TOPK:
            e = result["walk_forward"][tag][f"K{k}"]
            L.append(f"| {label} | {k} | {e['mean_excess']} | "
                     f"{e['mean_win']} | {e['n']} |")
    L += ["", "> 判定：学习权重的超额/胜率须 ≥ 固定权重且显著高于随机权重，"
              "否则引擎退回等权（告警）。"]
    (OUT_DIR / "walkforward_report.md").write_text(
        "\n".join(L), encoding="utf-8")
    print(f"[OUT] {OUT_DIR / 'weights.json'}")
    print(f"[OUT] {OUT_DIR / 'walkforward_report.md'}")


if __name__ == "__main__":
    main()
