#!/usr/bin/env python3
"""开盘强势扫描：历史分时回填（9:40 四维快照 + 当日 9:40→收盘前瞻收益）。

成分口径复用 data/style_backtest.db 的 bt_member（point-in-time）；
收盘价取该库 bt_kline；分时走 kline-fetcher（IntradayFetcher，32 线程）。
产出落 data/open_scan.db 的 snapshot 表，供 open_weight_learner.py 学习权重。

用法：
  python scripts/backfill_open_scan_history.py               # 默认近 120 个交易日
  python scripts/backfill_open_scan_history.py --days 60     # 指定窗口
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intraday_fetcher import IntradayFetcher  # noqa: E402
import open_scan_engine as ose  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BT_DB = ROOT / "data" / "style_backtest.db"
OUT_DB = ROOT / "data" / "open_scan.db"
VOL_BASELINE_DAYS = 5
FREEZE = "09:40"


def get_out_db():
    db = sqlite3.connect(OUT_DB)
    # v2 schema：加 close_raw（当日收盘=分时末点，同源防前复权污染）。
    # 旧表无该列则整体重建（快照可由分时重算）。
    cols = [r[1] for r in db.execute("PRAGMA table_info(snapshot)")]
    if cols and "close_raw" not in cols:
        db.executescript(
            "DROP TABLE IF EXISTS snapshot; DROP TABLE IF EXISTS progress;")
        db.commit()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshot (
            trade_date TEXT, stock_code TEXT,
            p940 REAL, auc_chg REAL, speed REAL, body REAL, vol_ratio REAL,
            fwd_ret REAL, close_raw REAL,
            PRIMARY KEY (trade_date, stock_code));
        CREATE TABLE IF NOT EXISTS cum_vol (
            trade_date TEXT, stock_code TEXT, cum_vol REAL,
            PRIMARY KEY (trade_date, stock_code));
        CREATE TABLE IF NOT EXISTS progress (
            trade_date TEXT PRIMARY KEY, done INTEGER);
        """
    )
    return db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    bt = sqlite3.connect(BT_DB)
    row = bt.execute(
        "SELECT value FROM bt_meta WHERE key LIKE 'trade_days:%' ORDER BY key DESC"
    ).fetchone()
    all_days = row[0].split(",")
    days = all_days[-args.days:]
    print(f"[BACKFILL] 窗口 {days[0]}~{days[-1]} 共 {len(days)} 个交易日")

    out = get_out_db()
    fetcher = IntradayFetcher(workers=args.workers)
    t0 = time.time()
    vol_hist = {}  # trade_date -> {stock: cum_vol}（滚动保留基线所需天数）

    for n, day in enumerate(days, 1):
        done = out.execute(
            "SELECT done FROM progress WHERE trade_date=?", (day,)).fetchone()
        if done and done[0]:
            # 已完成日也要补 cum_vol 进滚动基线（重启场景）
            rows = out.execute(
                "SELECT stock_code, cum_vol FROM cum_vol WHERE trade_date=?",
                (day,)).fetchall()
            vol_hist[day] = dict(rows)
            continue
        members = sorted({r[0] for r in bt.execute(
            "SELECT stock_code FROM bt_member WHERE trade_date=?", (day,))})
        if not members:
            continue
        series = fetcher.fetch_batch(members, date=day)
        # 昨收 = 前一交易日日K收盘（历史 pre_market 退化，序列内昨收不可用）
        prev_day = next((d for d in reversed(days) if d < day), None)
        pre_closes = dict(bt.execute(
            "SELECT code, close FROM bt_kline WHERE trade_date=?",
            (prev_day,))) if prev_day else {}

        baseline = {}
        prior = [d for d in days if d < day][-VOL_BASELINE_DAYS:]
        for sc in members:
            vals = [vol_hist[d][sc] for d in prior
                    if d in vol_hist and sc in vol_hist[d] and vol_hist[d][sc] > 0]
            if vals:
                baseline[sc] = sum(vals) / len(vals)

        snap_rows, cv_rows = [], []
        day_cv = {}
        for sc, s in series.items():
            cv = ose.cum_volume(s, FREEZE)
            day_cv[sc] = cv
            dims = ose.compute_dims(s, vol_baseline=baseline.get(sc),
                                    upto=FREEZE,
                                    pre_close_ext=pre_closes.get(sc))
            if not dims:
                continue
            # 当日收盘 = 分时末点（同源，规避日K前复权与原始价错位）
            tr = s.get("trading") or []
            close_raw = tr[-1]["last_price"] if tr else None
            fwd = ((close_raw / dims["p940"] - 1) * 100
                   if close_raw and close_raw > 0 else None)
            snap_rows.append((day, sc, dims["p940"], dims["auc_chg"],
                              dims["speed"], dims["body"], dims["vol_ratio"],
                              fwd, close_raw))
            cv_rows.append((day, sc, cv))
        out.executemany(
            "INSERT OR REPLACE INTO snapshot VALUES (?,?,?,?,?,?,?,?,?)",
            snap_rows)
        out.executemany(
            "INSERT OR REPLACE INTO cum_vol VALUES (?,?,?)", cv_rows)
        out.execute("INSERT OR REPLACE INTO progress VALUES (?,1)", (day,))
        out.commit()
        vol_hist[day] = day_cv
        # 只保留最近 5+1 天，控制内存
        if len(vol_hist) > VOL_BASELINE_DAYS + 2:
            for d in sorted(vol_hist)[:-VOL_BASELINE_DAYS - 1]:
                vol_hist.pop(d, None)
        if n % 10 == 0 or n == len(days):
            print(f"[BACKFILL] {n}/{len(days)}（{day} 快照 {len(snap_rows)} 只，"
                  f"累计 {(time.time()-t0)/60:.1f} 分钟）", flush=True)

    total = out.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0]
    days_done = out.execute("SELECT COUNT(*) FROM progress WHERE done=1").fetchone()[0]
    out.close()
    bt.close()
    print(f"[DONE] {days_done} 天 / {total} 条快照，总耗时 {(time.time()-t0)/60:.1f} 分钟")


if __name__ == "__main__":
    main()
