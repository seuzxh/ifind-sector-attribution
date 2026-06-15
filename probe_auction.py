# -*- coding: utf-8 -*-
"""
集合竞价数据探针脚本

目的：验证 9:15~9:25 集合竞价阶段，kline-fetcher 返回的 pre_market 数据形态，
      确认能否用 ref_price 计算涨跌幅（用于盘前强弱监控）。

用法（在生产环境，装了 kline_fetcher 的机器上）：
    cd /root/projects/2.monitor_940/ifind-sector-attribution
    python3 probe_auction.py                  # 默认拉 watchlist 前 5 只股票
    python3 probe_auction.py --codes 600519.SH 000001.SZ   # 指定股票
    python3 probe_auction.py --count 10        # 拉 watchlist 前 10 只

建议运行时机：
    - 9:15~9:25 集合竞价期间（核心验证目标）
    - 9:25~9:30 空窗期（pre_market 应已完成，trading 还没开始）
    - 盘中 9:30+（对照：trading 应有数据）

输出内容：
    1. 每只股票 pre_market 的前 3 点 + 末 3 点（time / ref_price）
    2. pre_market 点数、首末 ref_price、计算出的 pre_close / open
    3. trading 点数（集合竞价期间应为 0，盘中应有数据）
    4. 用末点 ref_price 算出的涨跌幅（验证核心目标）
    5. intraday_fetcher 当前返回结构（确认它丢弃了 pre_market 逐点序列）
"""

import sys
import os
import argparse
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: F401  确保加载 config_local.py 的 KLINE_API_BASE_URL

# 注入 KLINE_API_BASE_URL 到环境变量（复用 intraday_fetcher 的注入逻辑）。
# 否则 TrendFetcher() 直接实例化时读不到 config 里的 URL。
_url = getattr(config, "KLINE_API_BASE_URL", "")
if _url and not os.environ.get("KLINE_API_BASE_URL"):
    os.environ["KLINE_API_BASE_URL"] = _url


def probe_raw(code: str) -> dict:
    """直接调 TrendFetcher，看原始 pre_market / trading 结构。"""
    from kline_fetcher import TrendFetcher
    # 复用 intraday_fetcher 的代码格式转换（600519.SH → SH600519）
    from intraday_fetcher import _to_kf_code
    fetcher = TrendFetcher()
    data = fetcher.fetch_trend(_to_kf_code(code))
    return data or {}


def probe_via_fetcher(code: str) -> dict:
    """走 intraday_fetcher（项目当前用的路径），看它返回什么。"""
    from intraday_fetcher import IntradayFetcher
    f = IntradayFetcher()
    return f._fetch_one(code, date=None) or {}


def fmt_points(points: list, keys: list, n: int = 3) -> str:
    """格式化打印序列的前 n + 末 n 个点。"""
    if not points:
        return "  (空)"
    lines = []
    show = points[:n] + (["..."] if len(points) > 2 * n else []) + points[-n:]
    if len(points) <= 2 * n:
        show = points
    for p in show:
        if p == "...":
            lines.append(f"    ...")
            continue
        vals = " ".join(f"{k}={p.get(k)}" for k in keys if k in p)
        lines.append(f"    {vals}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="集合竞价数据探针")
    parser.add_argument("--codes", nargs="*", default=None, help="股票代码列表（.SH/.SZ/.BJ），不传则用 watchlist")
    parser.add_argument("--count", type=int, default=5, help="从 watchlist 取前 N 只（默认 5）")
    args = parser.parse_args()

    # 取待探测的股票列表
    codes: List[str] = args.codes or []
    if not codes:
        try:
            from database import Database
            db = Database()
            wl_date = db.get_latest_watchlist_date()
            if not wl_date:
                print("无 watchlist，请先跑盘前筛选，或用 --codes 指定股票")
                return
            codes = db.get_watchlist_stock_codes(wl_date)[:args.count]
            print(f"从 watchlist({wl_date}) 取前 {len(codes)} 只：{codes}\n")
        except Exception as e:
            print(f"读取 watchlist 失败：{e}\n请用 --codes 指定股票，如 --codes 600519.SH 000001.SZ")
            return

    from datetime import datetime
    now = datetime.now()
    print(f"探测时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（{['周一','周二','周三','周四','周五','周六','周日'][now.weekday()]}）")
    print(f"探测股票：{len(codes)} 只\n")
    print("=" * 70)

    for i, code in enumerate(codes, 1):
        print(f"\n[{i}/{len(codes)}] {code}")
        try:
            raw = probe_raw(code)
        except Exception as e:
            print(f"  原始拉取失败：{e}")
            continue

        pm = raw.get("pre_market") or []
        tr = raw.get("trading") or []
        print(f"  pre_market 点数：{len(pm)}")
        print(f"  trading 点数：{len(tr)}")

        if pm:
            print(f"  pre_market 前/末点（关键字段）：")
            print(fmt_points(pm, ["time", "ref_price", "matched_vol"], n=3))
            refs = [p.get("ref_price") for p in pm if p.get("ref_price") is not None]
            if refs:
                pre_close = refs[0]
                open_price = refs[-1]
                change_pct = (open_price / pre_close - 1) * 100 if pre_close else 0
                print(f"  → pre_close(首点 ref_price)={pre_close}")
                print(f"  → open/最新撮合价(末点 ref_price)={open_price}")
                print(f"  → 集合竞价涨跌幅 = (末ref/首ref - 1)*100 = {change_pct:+.2f}%")

        if tr:
            print(f"  trading 前/末点：")
            print(fmt_points(tr, ["time", "last_price", "avg_price"], n=2))

        # 对比：走 intraday_fetcher（项目实际路径）看它返回什么
        try:
            rec = probe_via_fetcher(code)
            print(f"  intraday_fetcher 返回：pre_close={rec.get('pre_close')}, "
                  f"open={rec.get('open')}, trading点数={len(rec.get('trading') or [])}")
            print(f"  ⚠ intraday_fetcher 是否保留了 pre_market 逐点序列：{'pre_market' in rec}")
        except Exception as e:
            print(f"  intraday_fetcher 调用失败：{e}")

    print("\n" + "=" * 70)
    print("结论判断：")
    print("- 若 pre_market 点数 > 0 且末点 ref_price 有效 → 集合竞价可算涨跌幅 ✓")
    print("- 若 intraday_fetcher 未保留 pre_market 逐点 → 需改 intraday_fetcher 带出该序列")
    print("- 将本输出贴回，我据此调整 realtime_engine 计算逻辑")


if __name__ == "__main__":
    main()
