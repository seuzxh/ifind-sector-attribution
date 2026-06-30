#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量补 daily 数据（日K + 板块强度 + 归因）。

用法：
    python scripts/backfill_daily.py 20260616 20260617 20260618 ...
    # 或不传参数，自动补 daily_kline 缺失的最近 N 个交易日
    python scripts/backfill_daily.py

特性：
  - 幂等：已入库的日期跳过（断点续跑）
  - 非交易日自动跳过
  - 失败不中断：某日失败记录错误，继续下一日
  - 逐日打印进度
"""
import sys
import os
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa
from database import Database
from trade_calendar import TradeCalendar


def existing_dates(db_path: str) -> set:
    """已入库的 daily_kline 日期集合"""
    if not os.path.exists(db_path):
        return set()
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM daily_kline")}
    finally:
        conn.close()


def main():
    db = Database()
    cal = TradeCalendar.instance()

    # 确定要补的日期列表
    if len(sys.argv) > 1:
        dates = [d.replace("-", "") for d in sys.argv[1:]]
    else:
        # 自动找缺失的最近交易日：从 daily_kline 最新日 + 1 到昨天
        conn = sqlite3.connect(db.db_path)
        latest = conn.execute("SELECT MAX(trade_date) FROM daily_kline").fetchone()[0]
        conn.close()
        if not latest:
            print("daily_kline 为空，无法自动推断，请手动传日期")
            return
        start = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        end = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")  # 昨天（今天可能未收盘）
        dates = []
        d = datetime.strptime(start, "%Y%m%d")
        while d.strftime("%Y%m%d") <= end:
            ds = d.strftime("%Y%m%d")
            if cal.is_trading_day(ds):
                dates.append(ds)
            d += timedelta(days=1)

    print("=" * 60)
    print(f"  批量补 daily 数据，共 {len(dates)} 个候选日期")
    print(f"  {dates}")
    print("=" * 60)

    existing = existing_dates(db.db_path)
    from sync_pipeline import SyncPipeline
    pipeline = SyncPipeline()

    ok, skipped, failed = [], [], []
    for i, date in enumerate(dates, 1):
        # 非交易日跳过
        if not cal.is_trading_day(date):
            print(f"\n[{i}/{len(dates)}] {date} 非交易日，跳过")
            skipped.append(date)
            continue
        # 已入库跳过（断点续跑）
        if date in existing:
            print(f"\n[{i}/{len(dates)}] {date} 已入库，跳过")
            skipped.append(date)
            continue

        print(f"\n[{i}/{len(dates)}] === 同步 {date} ===")
        t0 = datetime.now()
        try:
            pipeline.run_daily(date)
            ok.append(date)
            print(f"[{i}/{len(dates)}] {date} ✓ 完成，耗时 {(datetime.now()-t0).total_seconds():.0f}s")
        except Exception as e:
            failed.append((date, str(e)))
            print(f"[{i}/{len(dates)}] {date} ✗ 失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"  完成：成功 {len(ok)} 个，跳过 {len(skipped)} 个，失败 {len(failed)} 个")
    if ok:
        print(f"  成功: {ok}")
    if failed:
        print(f"  失败: {[(d, e[:40]) for d, e in failed]}")
    print("=" * 60)


if __name__ == "__main__":
    main()
