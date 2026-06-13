# -*- coding: utf-8 -*-
"""
入口脚本
用法:
  python main.py init --stocks stocks.txt    # 首次部署初始化
  python main.py daily --date 20260613       # 每日同步
  python main.py server                      # 启动 API 服务
  python main.py test                        # 运行接口测试
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from datetime import datetime

from sync_pipeline import SyncPipeline


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

    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
