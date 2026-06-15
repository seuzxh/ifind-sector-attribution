# -*- coding: utf-8 -*-
"""
入口脚本
用法:
  python main.py init --stocks stocks.txt    # 首次部署初始化
  python main.py daily --date 20260613       # 每日同步
  python main.py server                      # 启动 API 服务
  python main.py test                        # 运行接口测试
  python main.py purge --vacuum              # 删除海外数据，仅保留 A股
  python main.py prescreen --date 20260612   # 盘前筛选，存入 watchlist
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import sqlite3
from datetime import datetime

from sync_pipeline import SyncPipeline
from database import Database


def cmd_init(args):
    """首次部署初始化"""
    pipeline = SyncPipeline()

    # 读取股票代码列表
    stock_codes = []
    if args.stocks and os.path.exists(args.stocks):
        with open(args.stocks, "r") as f:
            stock_codes = [line.strip() for line in f if line.strip()]
    else:
        # 默认测试股票
        stock_codes = [
            "688001.SH", "600004.SH", "000001.SZ", "300001.SZ",
            "300033.SZ", "600030.SH", "000063.SZ", "000066.SZ"
        ]

    pipeline.run_init(stock_codes)


def cmd_daily(args):
    """每日同步"""
    pipeline = SyncPipeline()
    date = args.date or datetime.now().strftime("%Y%m%d")

    # 代码列表：指定文件则读取，否则 run_daily 自动反查全市场股票池
    all_codes = None
    if args.codes and os.path.exists(args.codes):
        with open(args.codes, "r") as f:
            all_codes = [line.strip() for line in f if line.strip()]

    pipeline.run_daily(date, all_codes)


def cmd_server(args):
    """启动 API 服务"""
    import uvicorn
    uvicorn.run("api_server:app", host=args.host, port=args.port, reload=args.reload)


def cmd_test(args):
    """运行接口测试"""
    from tests.test_api import run_all_tests
    run_all_tests()


def cmd_purge(args):
    """删除所有海外数据（非 A股），仅保留沪深北"""
    db = Database()
    print("[PURGE] 开始删除海外数据...")
    deleted = db.purge_overseas_data()
    total = 0
    for table, n in deleted.items():
        print(f"  {table:<22} 删除 {n} 行")
        total += n
    print(f"[PURGE] 完成，共删除 {total} 行")
    if args.vacuum:
        print("[PURGE] 执行 VACUUM 回收空间...")
        with sqlite3.connect(db.db_path) as conn:
            conn.execute("VACUUM")
        print("[PURGE] VACUUM 完成")
    print("[PURGE] 提示：建议提前备份数据库，此操作不可逆")


def cmd_prescreen(args):
    """盘前筛选：5日涨幅前20板块 + 各前30成分股，存入 watchlist"""
    from prescreen import run_prescreen
    db = Database()
    date = args.date or datetime.now().strftime("%Y%m%d")
    result = run_prescreen(
        db, date,
        top_sector=args.top_sector,
        top_stock=args.top_stock,
    )
    if "error" in result:
        print(f"\n[PRESCREEN] 失败: {result['error']}")
    else:
        print(f"\n[PRESCREEN] 完成：{result['sector_count']} 板块，{result['stock_count']} 只股票已存入 watchlist")


def cmd_import_groups(args):
    """导入同花顺自选股分组 JSON 到 custom_group 表（幂等，可重复导入更新）"""
    import json
    import os

    # market_code → A 股后缀（仅真正的 A 股个股，指数/ETF/可转债等过滤掉）
    A_SHARE_MARKET = {"17": ".SH", "33": ".SZ", "151": ".BJ"}

    json_path = args.json
    if not os.path.exists(json_path):
        print(f"[IMPORT-GROUPS] 文件不存在: {json_path}")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    groups = data.get("groups", [])
    rows = []
    skipped = 0
    for g in groups:
        gid = g.get("block_id", "")
        gname = g.get("block_name", gid)
        for s in g.get("stocks", []):
            mc = s.get("market_code", "")
            if mc not in A_SHARE_MARKET:
                skipped += 1
                continue  # 过滤非 A 股（指数/ETF/可转债/B股等）
            code = s.get("code", "")
            rows.append({
                "group_id": str(gid),
                "group_name": gname,
                "stock_code": f"{code}{A_SHARE_MARKET[mc]}",
            })

    db = Database()
    db.save_custom_groups(rows)

    group_count = len({r["group_id"] for r in rows})
    stock_count = len({r["stock_code"] for r in rows})
    print(f"[IMPORT-GROUPS] 导入完成：{group_count} 个分组，{len(rows)} 条成员（{stock_count} 只独立股票）")
    print(f"[IMPORT-GROUPS] 已过滤 {skipped} 条非 A 股标的（指数/ETF/可转债等）")
    print(f"[IMPORT-GROUPS] 来源: {json_path}")


def main():
    parser = argparse.ArgumentParser(description="行业归因与板块强度检测系统")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # init
    init_parser = subparsers.add_parser("init", help="首次部署初始化")
    init_parser.add_argument("--stocks", type=str, help="股票代码列表文件路径")
    init_parser.set_defaults(func=cmd_init)

    # daily
    daily_parser = subparsers.add_parser("daily", help="每日同步")
    daily_parser.add_argument("--date", type=str, help="同步日期，如 20260613")
    daily_parser.add_argument("--codes", type=str, help="全部代码列表文件路径")
    daily_parser.set_defaults(func=cmd_daily)

    # server
    server_parser = subparsers.add_parser("server", help="启动 API 服务")
    server_parser.add_argument("--host", type=str, default="0.0.0.0")
    server_parser.add_argument("--port", type=int, default=8000)
    server_parser.add_argument("--reload", action="store_true")
    server_parser.set_defaults(func=cmd_server)

    # test
    test_parser = subparsers.add_parser("test", help="运行接口测试")
    test_parser.set_defaults(func=cmd_test)

    # purge
    purge_parser = subparsers.add_parser("purge", help="删除海外数据（仅保留 A股）")
    purge_parser.add_argument("--vacuum", action="store_true", help="删除后执行 VACUUM 回收空间")
    purge_parser.set_defaults(func=cmd_purge)

    # prescreen
    prescreen_parser = subparsers.add_parser("prescreen", help="盘前筛选（5日涨幅选板块+成分股）")
    prescreen_parser.add_argument("--date", type=str, help="筛选日期 YYYYMMDD，默认今天")
    prescreen_parser.add_argument("--top-sector", type=int, default=None, help="选出的板块数，默认20")
    prescreen_parser.add_argument("--top-stock", type=int, default=None, help="每板块成分股数，默认30")
    prescreen_parser.set_defaults(func=cmd_prescreen)

    # import-groups
    DEFAULT_JSON = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "ths-custom-block-data", "同花顺自选分组导出.json"
    )
    ig_parser = subparsers.add_parser("import-groups", help="导入同花顺自选股分组 JSON（幂等，可重复导入更新）")
    ig_parser.add_argument("--json", type=str, default=DEFAULT_JSON, help="自选分组 JSON 文件路径")
    ig_parser.set_defaults(func=cmd_import_groups)

    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
