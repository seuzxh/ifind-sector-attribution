# -*- coding: utf-8 -*-
"""历史看板编排：读取已入库收盘数据组装 top/bottom/涨停榜单（原 api_server 的 220 行 handler 下沉）"""

from datetime import datetime

import pandas as pd


def build_history_dashboard(db, date: str, top_n: int = 10, force_calc: bool = False,
                            scope: str = "sector") -> dict:
    """
    历史看板：读取已入库的收盘数据（concept_strength + daily_kline）。
    成分股排名按当日涨幅（历史模式无1min，降级为纯涨幅）。

    :param date: 历史日期 YYYYMMDD
    :param top_n: 返回前 N 个板块
    :param force_calc: 无数据时是否自动拉取并计算（耗时约2分钟）
    :param scope: sector=当前勾选监控板块；custom=自选分组
    """
    if scope not in ("sector", "custom"):
        return {"error": f"不支持的历史范围：{scope}", "date": date}

    watched_codes = set(db.get_watched_concept_codes())
    rankings = []
    if scope == "sector":
        # 历史表可能含旧监控范围，读取时必须再次按当前勾选集收口。
        rankings = [
            r for r in db.get_sector_rankings(date, top_n=999)
            if r["concept_code"] in watched_codes
        ]

    daily_data = db.get_daily_kline_by_date(date)
    needs_history_data = (
        (scope == "sector" and not rankings)
        or (scope == "custom" and not daily_data)
    )
    if needs_history_data:
        # force_calc：板块缺评分时重新计算；自选缺个股日K时按需拉取。
        if force_calc:
            from sync_pipeline import SyncPipeline
            # 校验日期格式
            try:
                datetime.strptime(date, "%Y%m%d")
            except ValueError:
                return {"error": f"日期格式错误，需 YYYYMMDD：{date}", "date": date}
            try:
                pipeline = SyncPipeline()
                if not daily_data:
                    stock_codes = (
                        pipeline.db.get_custom_all_stock_codes()
                        if scope == "custom"
                        else pipeline.db.get_all_member_stock_codes()
                    )
                    pipeline.sync_daily_kline(stock_codes, date, date)
                if scope == "sector":
                    pipeline.calc_daily_strength(date)
                    pipeline.calc_daily_attribution(date)
                    rankings = [
                        r for r in db.get_sector_rankings(date, top_n=999)
                        if r["concept_code"] in watched_codes
                    ]
                daily_data = db.get_daily_kline_by_date(date)
            except Exception as e:
                return {"error": f"计算失败：{e}", "date": date}
            if (scope == "sector" and not rankings) or not daily_data:
                return {"error": f"日期 {date} 可能非交易日或无行情数据", "date": date}
        else:
            return {"error": f"日期 {date} 无数据，可点击\"拉取并计算\"获取", "date": date, "can_calc": True}

    # 概念名映射
    concept_names = db.get_concept_names()

    # 当日个股涨幅（成分股排名用）
    daily_df = pd.DataFrame(daily_data) if daily_data else pd.DataFrame()
    change_map = dict(zip(daily_df["code"], daily_df["change_ratio"])) if not daily_df.empty else {}

    # 成分股名称映射
    stock_names = db.get_latest_member_stock_names()

    if scope == "custom":
        members_map = db.get_custom_members_map()
        group_names = db.get_custom_group_names()
        rankings = []
        for gid, codes in members_map.items():
            changes = [
                float(change_map[c]) for c in codes
                if c in change_map and not pd.isna(change_map[c])
            ]
            if not changes:
                continue
            rankings.append({
                "concept_code": gid,
                "concept_name": group_names.get(gid, gid),
                "score_final": sum(changes) / len(changes),
                "s1_return": sum(changes) / len(changes),
                "s2_breadth": sum(1 for v in changes if v > 0) / len(changes),
            })
        rankings.sort(key=lambda r: r["score_final"], reverse=True)

    def _group_members(group_code: str):
        if scope == "custom":
            return [{"stock_code": c} for c in db.get_custom_members_map().get(group_code, [])]
        return db.get_concept_members(group_code)

    def _build_member_ranking(concept_code: str, limit: int = 10, reverse: bool = True):
        """
        历史模式：成分股按当日涨幅排序。
        :param reverse: True=降序(涨幅最大在前，给top板块)；False=升序(跌幅最深在前，给bottom板块)
        :return: (排序后的前 limit 只, 有当日涨幅数据的成分股总数)
        """
        members = _group_members(concept_code)
        member_changes = []
        for m in members:
            chg = change_map.get(m["stock_code"])
            if chg is not None and not pd.isna(chg):
                member_changes.append({
                    "code": m["stock_code"],
                    "name": stock_names.get(m["stock_code"], ""),
                    "change_ratio": round(float(chg), 2),
                    "speed": 0.0,
                    "body": 0.0,
                    "limit": 0,
                    "score": round(float(chg), 4),
                })
        member_changes.sort(key=lambda x: x["change_ratio"], reverse=reverse)
        return member_changes[:limit], len(member_changes)

    # Top 板块（含成分股，涨幅最大前10）
    main_rankings = rankings
    zt_rankings = []
    if scope == "custom":
        zt_rankings = [
            r for r in rankings if r["concept_name"].strip().upper().startswith("ZT")
        ]
        main_rankings = [
            r for r in rankings if not r["concept_name"].strip().upper().startswith("ZT")
        ]

    def _sector_rows(src_rankings, reverse: bool):
        rows = []
        for r in src_rankings:
            cc = r["concept_code"]
            top10, total_cnt = _build_member_ranking(cc, 10, reverse=reverse)
            rows.append({
                "concept_code": cc,
                "concept_name": r.get("concept_name") or concept_names.get(cc, cc),
                "score": r.get("score_final", r.get("score_1d", 0)),
                "s1_return": r.get("s1_return", 0),
                "s2_breadth": r.get("s2_breadth", 0),
                "member_count": total_cnt,
                "members_top10": top10,
            })
        return rows

    top_sectors = _sector_rows(main_rankings[:top_n], reverse=True)
    # Bottom 板块（含成分股，跌幅最深前10）
    bottom_sectors = _sector_rows(main_rankings[-top_n:][::-1], reverse=False)
    zt_sectors = _sector_rows(zt_rankings, reverse=True)

    # 统计口径与当前页面范围一致，而不是整张 daily_kline 表。
    if not daily_df.empty:
        if scope == "custom":
            scope_codes = set(db.get_custom_all_stock_codes())
        else:
            scope_codes = set()
            for cc in watched_codes:
                scope_codes.update(m["stock_code"] for m in db.get_concept_members(cc))
        scoped_df = daily_df[daily_df["code"].isin(scope_codes)]
        chg_series = scoped_df["change_ratio"].dropna()

        def _is_limit(code, chg):
            pure = code.split(".")[0]
            if code.endswith(".BJ"):
                return chg >= 29.0
            if pure.startswith(("300", "688")):
                return chg >= 19.5
            return chg >= 9.8

        market_stats = {
            "stock_count": int(len(chg_series)),
            "market_avg_change": round(float(chg_series.mean()), 2),
            "up_count": int((chg_series > 0).sum()),
            "down_count": int((chg_series < 0).sum()),
            "flat_count": int((chg_series == 0).sum()),
            "limit_up_count": int(sum(
                1 for code, chg in zip(scoped_df["code"], scoped_df["change_ratio"])
                if pd.notna(chg) and _is_limit(code, chg)
            )),
        }
    else:
        market_stats = {}

    return {
        "date": date,
        "market_stats": market_stats,
        "top_sectors": top_sectors,
        "bottom_sectors": bottom_sectors,
        "zt_sectors": zt_sectors,
        "scope": scope,
    }
