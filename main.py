# -*- coding: utf-8 -*-
"""
入口脚本
用法:
  python main.py init --stocks stocks.txt    # 首次部署初始化
  python main.py daily --date 20260613       # 每日同步
  python main.py server                      # 启动 API 服务
  python main.py test                        # 运行接口测试
  python main.py purge --vacuum              # 删除海外数据，仅保留 A股
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


def cmd_push(args):
    """股池归因定时推送：按 slot 选股归类并推送飞书（由 crontab 在 4 个时刻调用）。"""
    from scan_push import run_push, PUSH_SLOTS
    if args.slot not in PUSH_SLOTS:
        print(f"[PUSH] 未知 slot: {args.slot}（可选: {list(PUSH_SLOTS)}）")
        sys.exit(1)
    result = run_push(args.slot, dry_run=args.dry_run)
    if result.get("skipped"):
        print(f"[PUSH] 非交易日，跳过（slot={args.slot}）")
        return
    cls = result.get("classification", {})
    custom = cls.get("custom", {})
    market = cls.get("market", {})
    c_err = custom.get("error") if isinstance(custom, dict) else None
    m_err = market.get("error") if isinstance(market, dict) else None
    print(f"[PUSH] slot={args.slot} query={cls.get('query', '')[:60]}")
    if c_err:
        print(f"  自选归类: ❌ {c_err}")
    else:
        print(f"  自选归类: 池{custom.get('pool_size',0)} 命中{custom.get('hit_total',0)} 分组{custom.get('group_hit_count',0)}")
    if m_err:
        print(f"  全市场:   ❌ {m_err}")
    else:
        print(f"  全市场:   池{market.get('pool_size',0)} 可归类{market.get('hit_total',0)} 分组{market.get('group_hit_count',0)}")
    # 自选/全市场分别推送（两条独立卡片）
    pushed = result.get("pushed", {})
    if args.dry_run:
        print("  推送: dry-run 未推送（自选/全市场各一条）")
    else:
        pc = pushed.get("custom")
        pm = pushed.get("market")
        print(f"  自选推送:   {'✅ 成功' if pc else '❌ 失败/未配置'}")
        print(f"  全市场推送: {'✅ 成功' if pm else '❌ 失败/未配置'}")


def cmd_kg_init(args):
    """知识图谱首次构建（P1）：4 表落库 + 统计报告 + 族群初探。"""
    from kg_builder import kg_bootstrap
    result = kg_bootstrap(
        Database(),
        force=args.force,
        refetch=args.refetch,
        skip_verify=args.skip_verify,
    )
    if result.get("error") == "snapshot_exists":
        sys.exit(1)


def cmd_refresh_boards(args):
    """
    统一以 smart_stock_picking 枚举结果刷新板块字典（行业 + 概念全集）。

    流程：动态枚举 710 板块 → 全量替换 ths_concept_dict（清理遗留旧码）
        → 被清理的勾选板块按名称自动迁移（白酒Ⅲ→白酒）→ 新板块补拉成分股（接口2）
    观察/归因白名单不变（884/885/886），881 二级行业仅入字典不进观察池。
    """
    from datetime import datetime
    from database import Database
    from ifind_client import IFindClient
    import config as _config

    db = Database()
    client = IFindClient()

    # 1. 动态枚举板块全集
    print("[REFRESH-BOARDS] 枚举同花顺板块全集（行业+概念）...")
    boards = client.get_all_ths_boards()
    if not boards:
        print("[REFRESH-BOARDS] ❌ 枚举失败（接口无返回），中止")
        sys.exit(1)
    from collections import Counter
    prefix_cnt = Counter(b["concept_code"][:3] for b in boards)
    print(f"[REFRESH-BOARDS] 枚举到 {len(boards)} 个板块: {dict(prefix_cnt)}")

    # 2. 全量替换字典 + 级联清理 + 勾选名称迁移
    result = db.refresh_concept_dict_replace(boards)
    print(f"[REFRESH-BOARDS] 字典已刷新: 新增 {result['added']} / 移除 {result['removed']} / 总数 {result['total']}")
    for old, new, name in result["migrated"]:
        print(f"  勾选迁移: {name} {old} → {new}")
    for code, name in result["dropped_watched"]:
        print(f"  ⚠ 勾选被删（无同名新码，需手动重选）: {name} {code}")

    # 3. 新板块补拉成分股（接口2，仅观察池前缀且缺成分股的）
    if args.skip_members:
        print("[REFRESH-BOARDS] --skip-members，跳过成分股补拉")
    else:
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            have_members = {r[0] for r in conn.execute(
                "SELECT DISTINCT concept_code FROM concept_members")}
        need = [
            cc for cc in db.get_observe_concept_codes()
            if cc not in have_members
        ]
        # 迁移后的新码也需补拉
        need += [new for _, new, _ in result["migrated"] if new not in have_members and new not in need]
        print(f"[REFRESH-BOARDS] 需补拉成分股的新板块: {len(need)} 个（接口2）")
        if need:
            member_date = datetime.now().strftime("%Y%m%d")
            ok, fail = 0, 0
            for cc in need:
                try:
                    resp = client.get_concept_members(cc, member_date)
                    tables = resp.get("tables", [])
                    if tables:
                        rows = []
                        t = tables[0].get("table", {})
                        # p03473 字段映射：f001=日期, f002=股票代码, f003=股票名称
                        codes = t.get("p03473_f002", [])
                        names = t.get("p03473_f003", [])
                        for i, sc in enumerate(codes):
                            if _config.is_a_share_code(sc):
                                rows.append({"stock_code": sc,
                                             "stock_name": names[i] if i < len(names) else ""})
                        if rows:
                            db.save_concept_members(cc, rows, member_date)
                            ok += 1
                            continue
                    fail += 1
                except Exception as e:
                    fail += 1
                    print(f"  {cc} 拉取失败: {e}")
            print(f"[REFRESH-BOARDS] 成分股补拉完成: 成功 {ok} / 失败 {fail}")

    print("[REFRESH-BOARDS] ✅ 完成。板块管理页候选将以新字典为准。")


def cmd_kg_query(args):
    """知识图谱简易查询：个股→板块 / 板块→成分股 / 联动股。"""
    import sqlite3
    from database import Database
    db = Database()
    key = args.code.strip().upper()

    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row

        def _resolve_sector(k):
            for cand in (k if k.endswith(".TI") else k + ".TI",):
                row = conn.execute(
                    "SELECT node_id, code, name FROM kg_node WHERE node_type='sector' AND code=?",
                    (cand,)).fetchone()
                if row:
                    return row
            return None

        def _resolve_stock(k):
            cands = [k] if "." in k else [k + s for s in (".SH", ".SZ", ".BJ")]
            for cand in cands:
                row = conn.execute(
                    "SELECT node_id, code, name FROM kg_node WHERE node_type='stock' AND code=?",
                    (cand,)).fetchone()
                if row:
                    return row
            return None

        sector = _resolve_sector(key) if key[:3] in ("884", "885", "886") else None
        stock = None if sector else _resolve_stock(key)

        if sector:
            print(f"\n板块【{sector['name']}】({sector['code']}) 成分股：")
            rows = conn.execute("""
                SELECT n1.code, n1.name FROM kg_edge e
                JOIN kg_node n1 ON e.src_id = n1.node_id
                WHERE e.dst_id = ? AND e.valid_to IS NULL ORDER BY n1.code
            """, (sector["node_id"],)).fetchall()
            for i, r in enumerate(rows, 1):
                print(f"  {i:>3}. {r['code']:<10} {r['name']}")
            print(f"共 {len(rows)} 只\n")
        elif stock:
            print(f"\n个股【{stock['name']}】({stock['code']}) 归属板块：")
            rows = conn.execute("""
                SELECT n2.code, n2.name,
                       CASE n2.sector_type WHEN 'industry' THEN '行业' ELSE '概念' END AS kind
                FROM kg_edge e JOIN kg_node n2 ON e.dst_id = n2.node_id
                WHERE e.src_id = ? AND e.valid_to IS NULL
                ORDER BY kind, n2.code
            """, (stock["node_id"],)).fetchall()
            for i, r in enumerate(rows, 1):
                print(f"  {i:>2}. [{r['kind']}] {r['name']}")
            print(f"共 {len(rows)} 个板块\n")

            if args.linked:
                n = args.linked
                print(f"联动股 Top{n}（共享板块最多的股票）：")
                lk = conn.execute("""
                    SELECT n2.code, n2.name, COUNT(DISTINCT e2.dst_id) AS shared,
                           GROUP_CONCAT((SELECT name FROM kg_node WHERE node_id=e2.dst_id), '、') AS via
                    FROM kg_edge e1
                    JOIN kg_edge e2 ON e1.dst_id = e2.dst_id AND e2.src_id != e1.src_id
                    JOIN kg_node n2 ON e2.src_id = n2.node_id
                    WHERE e1.src_id = ? AND e1.valid_to IS NULL AND e2.valid_to IS NULL
                    GROUP BY e2.src_id ORDER BY shared DESC LIMIT ?
                """, (stock["node_id"], n)).fetchall()
                for i, r in enumerate(lk, 1):
                    print(f"  {i:>2}. {r['name']:<8} {r['code']:<10} 共享{r['shared']}个板块")
                    print(f"      └ {r['via']}")
                print()
        else:
            print(f"[KG-QUERY] 未找到：{args.code}（板块需 884/885/886 开头，如 884091；个股如 600519）")
            sys.exit(1)


def cmd_kg_update(args):
    """知识图谱周维护（P2）：diff 两源归属 → 开/关边 + 升降级 + 变更日志 + 快照。"""
    from kg_builder import kg_update
    result = kg_update(Database(), skip_verify=args.skip_verify, force=args.force)
    if result.get("error") == "snapshot_exists":
        sys.exit(1)


def cmd_kg_corr(args):
    """知识图谱 P3：算 20 日滚动 ρ 挂边 + 重算族群（每日盘后跑，手动或 cron）。"""
    from kg_analysis import compute_corr_20d, detect_communities
    db = Database()
    stats = compute_corr_20d(db, window=args.window)
    comm = detect_communities(db, use_corr=not args.no_corr_weight)
    print(f"[KG-CORR] 完成：ρ {stats['updated']}/{stats['edges']} 条（覆盖 {stats['coverage']:.1%}），"
          f"族群 {comm['communities']} 个")


def cmd_import_groups(args):
    """导入同花顺自选股分组 JSON 到 custom_group 表（幂等，可重复导入更新）"""
    result = import_groups_from_json(args.json)
    if result is None:
        return
    print(f"[IMPORT-GROUPS] 导入完成：{result['group_count']} 个分组，{result['row_count']} 条成员（{result['stock_count']} 只独立股票）")
    print(f"[IMPORT-GROUPS] 已过滤 {result['skipped']} 条非 A 股标的（指数/ETF/可转债等）")
    print(f"[IMPORT-GROUPS] 来源: {args.json}")


# 自选股分组 JSON 默认路径（cmd_import_groups 与 /api/custom/check_reload 共用）
CUSTOM_GROUPS_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "ths-custom-block-data", "同花顺自选分组导出.json"
)

# market_code → A 股后缀（仅真正的 A 股个股，指数/ETF/可转债等过滤掉）
_A_SHARE_MARKET = {"17": ".SH", "33": ".SZ", "151": ".BJ"}


def import_groups_from_json(json_path: str):
    """
    从同花顺 custom_block 导出 JSON 全量导入 custom_group 表（幂等覆盖）。
    供 cmd_import_groups（CLI）与 /api/custom/check_reload（API 自动重导）复用。

    :return: {group_count, stock_count, row_count, skipped, json_path} 或 None（文件不存在/格式错）
    """
    import json

    if not os.path.exists(json_path):
        print(f"[IMPORT-GROUPS] 文件不存在: {json_path}")
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[IMPORT-GROUPS] JSON 解析失败: {e}")
        return None

    groups = data.get("groups", [])
    rows = []
    skipped = 0
    for g in groups:
        gid = g.get("block_id", "")
        gname = g.get("block_name", gid)
        for s in g.get("stocks", []):
            mc = s.get("market_code", "")
            if mc not in _A_SHARE_MARKET:
                skipped += 1
                continue  # 过滤非 A 股（指数/ETF/可转债/B股等）
            code = s.get("code", "")
            rows.append({
                "group_id": str(gid),
                "group_name": gname,
                "stock_code": f"{code}{_A_SHARE_MARKET[mc]}",
            })

    db = Database()
    db.save_custom_groups(rows)

    return {
        "group_count": len({r["group_id"] for r in rows}),
        "stock_count": len({r["stock_code"] for r in rows}),
        "row_count": len(rows),
        "skipped": skipped,
        "json_path": json_path,
    }


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

    # import-groups
    ig_parser = subparsers.add_parser("import-groups", help="导入同花顺自选股分组 JSON（幂等，可重复导入更新）")
    ig_parser.add_argument("--json", type=str, default=CUSTOM_GROUPS_JSON, help="自选分组 JSON 文件路径")
    ig_parser.set_defaults(func=cmd_import_groups)

    # refresh-boards
    rb_parser = subparsers.add_parser(
        "refresh-boards",
        help="以 smart_stock_picking 枚举刷新板块字典（统一行业+概念全集，清理遗留旧码，勾选自动迁移）")
    rb_parser.add_argument("--skip-members", action="store_true", help="跳过新板块成分股补拉（接口2）")
    rb_parser.set_defaults(func=cmd_refresh_boards)

    # push（股池归因定时推送，由 crontab 调用）
    push_parser = subparsers.add_parser("push", help="股池归因定时推送（按时间槽选股归类并推送飞书）")
    push_parser.add_argument("--slot", type=str, required=True,
                             choices=["933", "945", "1000", "1430"],
                             help="时间槽：933=9:33 / 945=9:45 / 1000=10:00 / 1430=14:30")
    push_parser.add_argument("--dry-run", action="store_true", help="只选股归类并打印消息，不推送")
    push_parser.set_defaults(func=cmd_push)

    # kg_init（知识图谱首次构建，P1）
    kg_parser = subparsers.add_parser("kg_init", help="构建知识图谱（首次构建 + 统计报告 + 族群初探）")
    kg_parser.add_argument("--force", action="store_true", help="已存在快照时清空 kg 表重建")
    kg_parser.add_argument("--refetch", action="store_true", help="强制重拉接口2成分股（否则复用当天快照）")
    kg_parser.add_argument("--skip-verify", action="store_true", help="跳过接口1交叉验证源（只建主源边）")
    kg_parser.set_defaults(func=cmd_kg_init)

    # kg_query（知识图谱简易查询）
    kgq_parser = subparsers.add_parser("kg_query", help="查图谱：个股→板块 / 板块→成分股 / 联动股")
    kgq_parser.add_argument("code", type=str, help="股票代码(600519) 或板块码(884091)，可带或不带后缀")
    kgq_parser.add_argument("--linked", type=int, default=0, metavar="N", help="查个股时附带联动股 TopN（按共享板块数）")
    kgq_parser.set_defaults(func=cmd_kg_query)

    # kg_update（知识图谱周维护，P2，crontab 周日调用）
    kgu_parser = subparsers.add_parser("kg_update", help="知识图谱周维护：拉两源→diff→开/关边+变更日志+快照")
    kgu_parser.add_argument("--skip-verify", action="store_true", help="跳过接口1验证源")
    kgu_parser.add_argument("--force", action="store_true", help="当日已有快照也重做")
    kgu_parser.set_defaults(func=cmd_kg_update)

    # kg_corr（知识图谱 P3：ρ 边权 + 族群，每日盘后）
    kgc_parser = subparsers.add_parser("kg_corr", help="算 20 日滚动 ρ 挂边 + 重算族群（盘后任务）")
    kgc_parser.add_argument("--window", type=int, default=20, help="滚动窗口交易日数，默认 20")
    kgc_parser.add_argument("--no-corr-weight", action="store_true", help="族群发现不用 ρ 加权（退化等权）")
    kgc_parser.set_defaults(func=cmd_kg_corr)

    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
