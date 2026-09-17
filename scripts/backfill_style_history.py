#!/usr/bin/env python3
"""三指数归因回测：历史数据回填（一次性，断点续跑）。

回填内容（全部 point-in-time，落独立库 data/style_backtest.db）：
  1. 交易日序列：取 883926.TI 日K 的 time 列（2023-01 起）
  2. 每日成分：p03473 传历史日期 → bt_member
  3. 每日概念映射：basic_data_service 传历史日期 → bt_map（仅 885 前缀）
  4. 概念规模快照：半年度日期 × 当期概念全集 → bt_concept_size（150 过滤 / lift 用）
  5. 日K：全部历史成分股 + 885 概念指数 + 三风格指数 + 沪深300 → bt_kline

用法：
  python scripts/backfill_style_history.py                    # 全流程（自动跳过已完成）
  python scripts/backfill_style_history.py --step klines      # 单跑某步 members/sizes/klines
  python scripts/backfill_style_history.py --sleep 0.5        # 调慢限速
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ifind_client  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "style_backtest.db"
INDICES = {"883926.TI": "高贝塔值", "883409.TI": "近期强势", "883910.TI": "同花顺热股"}
BENCHMARK = "000300.SH"
DEFAULT_START = "2023-01-01"
# 概念规模快照日（就近回溯取不早于交易日的最近一个快照）
SIZE_SNAP_DATES = [
    "2023-01-03", "2023-07-03", "2024-01-02", "2024-07-01",
    "2025-01-02", "2025-07-01", "2026-01-05", "2026-07-01", "2026-09-11",
]


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS bt_member (
            trade_date TEXT, index_code TEXT, stock_code TEXT,
            PRIMARY KEY (trade_date, index_code, stock_code));
        CREATE TABLE IF NOT EXISTS bt_map (
            trade_date TEXT, stock_code TEXT, concept_code TEXT, concept_name TEXT,
            PRIMARY KEY (trade_date, stock_code, concept_code));
        CREATE TABLE IF NOT EXISTS bt_concept_size (
            snap_date TEXT, concept_code TEXT, n_members INTEGER,
            PRIMARY KEY (snap_date, concept_code));
        CREATE TABLE IF NOT EXISTS bt_kline (
            code TEXT, trade_date TEXT, close REAL, change_ratio REAL,
            PRIMARY KEY (code, trade_date));
        CREATE TABLE IF NOT EXISTS bt_progress (
            trade_date TEXT PRIMARY KEY, done INTEGER);
        CREATE TABLE IF NOT EXISTS bt_meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    return db


class Backfiller:
    def __init__(self, sleep_s: float = 0.25):
        self.client = ifind_client.IFindClient()
        self.sleep_s = sleep_s
        self.db = get_db()
        self._calls = 0

    def _throttle(self):
        self._calls += 1
        time.sleep(self.sleep_s)

    def _fetch_kline(self, codes, start, end):
        """拉日K，返回 {code: {date: (close, change_ratio)}}；50 码/批。"""
        out = {}
        for i in range(0, len(codes), 50):
            batch = codes[i:i + 50]
            try:
                resp = self.client.get_history_quotation(
                    batch, start, end, indicators="close,changeRatio")
                self._throttle()
                for item in resp.get("tables", []):
                    code = item.get("thscode", "")
                    dates = item.get("time", [])
                    tbl = item.get("table", {})
                    closes = tbl.get("close", [])
                    chgs = tbl.get("changeRatio", [])
                    series = {}
                    for idx, d in enumerate(dates):
                        if idx < len(closes) and closes[idx] is not None:
                            chg = chgs[idx] if idx < len(chgs) else None
                            series[d.replace("-", "")] = (closes[idx], chg)
                    if series:
                        out[code] = series
            except Exception as e:
                print(f"[KLINE] 批次失败({batch[0]}..): {e}")
        return out

    def save_kline(self, fetched):
        rows = [(c, d, v[0], v[1]) for c, series in fetched.items()
                for d, v in series.items()]
        self.db.executemany(
            "INSERT OR REPLACE INTO bt_kline VALUES (?,?,?,?)", rows)
        self.db.commit()
        return len(rows)

    # ---------- 步骤 1：交易日 ----------
    def trade_days(self, end, start=DEFAULT_START):
        # 缓存键必须含窗口：不同 start/end 的回填不能复用（冒烟测试教训）
        cache_key = f"trade_days:{start}:{end}"
        days = self.db.execute(
            "SELECT value FROM bt_meta WHERE key=?", (cache_key,)).fetchone()
        if days:
            return days[0].split(",")
        fetched = self._fetch_kline(["883926.TI"], start, end)
        self.save_kline(fetched)  # 顺带存锚点指数K线
        series = fetched.get("883926.TI", {})
        # 高贝塔值从 2021 年中才有数据，截到窗口内
        day_list = sorted(d for d in series if d >= start.replace("-", ""))
        self.db.execute(
            "INSERT OR REPLACE INTO bt_meta VALUES (?, ?)",
            (cache_key, ",".join(day_list),))
        self.db.commit()
        print(f"[DAYS] 交易日 {len(day_list)} 天: {day_list[0]}~{day_list[-1]}")
        return day_list

    # ---------- 步骤 2+3：每日成分 + 概念映射 ----------
    def backfill_daily(self, end, start=DEFAULT_START):
        days = self.trade_days(end, start)
        todo = [d for d in days
                if not self.db.execute(
                    "SELECT done FROM bt_progress WHERE trade_date=?",
                    (d,)).fetchone()]
        print(f"[DAILY] 待回填 {len(todo)}/{len(days)} 天")
        for n, d in enumerate(todo, 1):
            iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            members = set()
            ok = True
            for idx_code in INDICES:
                try:
                    r = self.client.get_concept_members(idx_code, iso)
                    self._throttle()
                    tbl = (r.get("tables") or [{}])[0].get("table", {})
                    codes = tbl.get("p03473_f002", [])
                    # 指数未发布日（近期强势 2023-07 前）返回空，属正常
                    if not codes and idx_code == "883926.TI":
                        print(f"[DAILY] {d} 高贝塔值成分空，异常，跳过该日")
                        ok = False
                        break
                    self.db.executemany(
                        "INSERT OR REPLACE INTO bt_member VALUES (?,?,?)",
                        [(d, idx_code, c) for c in codes])
                    members.update(codes)
                except Exception as e:
                    print(f"[DAILY] {d} {idx_code} 成分失败: {e}")
                    ok = False
                    break
            if not ok:
                continue
            if members:
                try:
                    mapping = self.client.batch_get_stock_concepts(
                        sorted(members), iso)
                    self._throttle()
                except Exception as e:
                    print(f"[DAILY] {d} 概念映射失败: {e}")
                    continue
                rows = []
                for sc, concepts in mapping.items():
                    for cpt in concepts:
                        cc = cpt.get("concept_code", "")
                        if cc.startswith("885"):
                            rows.append((d, sc, cc, cpt.get("concept_name", "")))
                self.db.executemany(
                    "INSERT OR REPLACE INTO bt_map VALUES (?,?,?,?)", rows)
            self.db.execute("INSERT OR REPLACE INTO bt_progress VALUES (?,1)", (d,))
            self.db.commit()
            if n % 20 == 0 or n == len(todo):
                print(f"[DAILY] {n}/{len(todo)} 完成（累计 API {self._calls} 次）")

    # ---------- 步骤 4：概念规模快照 ----------
    def backfill_sizes(self, end, start=DEFAULT_START):
        days = self.trade_days(end, start)
        concepts = [r[0] for r in self.db.execute(
            "SELECT DISTINCT concept_code FROM bt_map")]
        print(f"[SIZE] 当期概念全集 {len(concepts)} 个")
        for snap in SIZE_SNAP_DATES:
            key = snap.replace("-", "")
            done = self.db.execute(
                "SELECT COUNT(*) FROM bt_concept_size WHERE snap_date=?",
                (key,)).fetchone()[0]
            if done >= len(concepts) * 0.9:  # 已基本完成
                continue
            # 快照日对齐到其后第一个交易日
            iso = next((d for d in days if d >= key), None)
            if iso is None:
                continue
            iso_fmt = f"{iso[:4]}-{iso[4:6]}-{iso[6:]}"
            saved = 0
            for n, cc in enumerate(concepts, 1):
                try:
                    r = self.client.get_concept_members(cc, iso_fmt)
                    self._throttle()
                    tbl = (r.get("tables") or [{}])[0].get("table", {})
                    cnt = len(tbl.get("p03473_f002", []))
                    if cnt:
                        self.db.execute(
                            "INSERT OR REPLACE INTO bt_concept_size VALUES (?,?,?)",
                            (key, cc, cnt))
                        saved += 1
                except Exception as e:
                    print(f"[SIZE] {cc} 失败: {e}")
                if n % 100 == 0:
                    self.db.commit()
                    print(f"[SIZE] {snap}: {n}/{len(concepts)}（保存 {saved}）")
            self.db.commit()
            print(f"[SIZE] {snap} 完成: {saved}/{len(concepts)}")

    # ---------- 步骤 5：日K ----------
    def backfill_klines(self, end, start=DEFAULT_START):
        member_codes = [r[0] for r in self.db.execute(
            "SELECT DISTINCT stock_code FROM bt_member")]
        concept_codes = [r[0] for r in self.db.execute(
            "SELECT DISTINCT concept_code FROM bt_map")]
        style_codes = list(INDICES) + [BENCHMARK]
        all_codes = sorted(set(member_codes) | set(concept_codes) | set(style_codes))
        # 断点：已有完整起始日数据的代码跳过（起止均有行即视为完整）
        have = {r[0] for r in self.db.execute(
            "SELECT DISTINCT code FROM bt_kline")}
        # 概念指数与个股不同：只要在 have 里且行数>500 就不再拉
        rowcount = dict(self.db.execute(
            "SELECT code, COUNT(*) FROM bt_kline GROUP BY code"))
        need = [c for c in all_codes
                if c not in have or rowcount.get(c, 0) < 200]
        print(f"[KLINE] 需拉取 {len(need)}/{len(all_codes)} 个代码")
        total = 0
        for i in range(0, len(need), 500):
            chunk = need[i:i + 500]
            fetched = self._fetch_kline(chunk, start, end)
            total += self.save_kline(fetched)
            print(f"[KLINE] 进度 {min(i+500, len(need))}/{len(need)}"
                  f"（本轮入库 {total} 行，累计 API {self._calls} 次）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", choices=["members", "sizes", "klines", "all"],
                    default="all")
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()

    bf = Backfiller(sleep_s=args.sleep)
    t0 = time.time()
    if args.step in ("members", "all"):
        bf.backfill_daily(args.end, args.start)
    if args.step in ("sizes", "all"):
        bf.backfill_sizes(args.end, args.start)
    if args.step in ("klines", "all"):
        bf.backfill_klines(args.end, args.start)
    print(f"[DONE] 总耗时 {(time.time()-t0)/60:.1f} 分钟，API 调用 {bf._calls} 次")


if __name__ == "__main__":
    main()
