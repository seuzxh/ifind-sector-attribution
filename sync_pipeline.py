# -*- coding: utf-8 -*-
"""
数据同步管线
- 首次部署：初始化概念字典、个股-概念映射、成分股列表
- 每日收盘后：批量补日K线、计算板块强度、计算个股归因
"""

import sys
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict

from ifind_client import IFindClient
from database import Database
from core_calculator import calc_all_sectors_strength, l1_stock_attribution
import config


class SyncPipeline:
    """数据同步管线"""

    def __init__(self):
        self.client = IFindClient()
        self.db = Database()

    # ========== 首次部署：初始化 ==========
    def init_concept_dict(self):
        """步骤0: 初始化概念板块字典（永久缓存）"""
        print("[INIT] 开始初始化概念板块字典...")
        concepts = self.client.batch_get_concept_basic_info(
            config.ALL_CONCEPT_CODES,
            batch_size=100
        )
        self.db.save_concept_dict(concepts)
        print(f"[INIT] 已保存 {len(concepts)} 个概念板块到字典")
        return concepts

    def init_stock_concept_map(self, stock_codes: List[str], map_date: str = None):
        """步骤1: 初始化个股-概念映射（永久缓存）"""
        map_date = map_date or datetime.now().strftime("%Y-%m-%d")
        print(f"[INIT] 开始初始化个股-概念映射，日期={map_date}...")

        mappings = self.client.batch_get_stock_concepts(stock_codes, map_date)
        self.db.save_stock_concept_map(mappings, map_date)
        print(f"[INIT] 已保存 {len(mappings)} 只个股的概念映射")
        return mappings

    def _fetch_one_concept_members(self, concept_code: str, member_date: str):
        """
        拉取单个概念的成分股（供线程池 worker 调用）
        :return: (concept_code, members) 成功； (concept_code, None) 失败
        """
        try:
            resp = self.client.get_concept_members(concept_code, member_date)
            if "tables" in resp and len(resp["tables"]) > 0:
                table = resp["tables"][0].get("table", {})
                stock_codes = table.get("p03473_f002", [])
                stock_names = table.get("p03473_f003", [])

                members = []
                for i in range(len(stock_codes)):
                    members.append({
                        "stock_code": stock_codes[i],
                        "stock_name": stock_names[i] if i < len(stock_names) else ""
                    })
                return (concept_code, members)
            # 无成分股数据（如部分指数类概念），视为空成功
            return (concept_code, [])
        except Exception as e:
            print(f"[WARN] 获取 {concept_code} 成分股失败: {e}")
            return (concept_code, None)

    def init_concept_members(self, member_date: str = None):
        """步骤2: 初始化概念板块成分股（永久缓存，并发拉取）"""
        member_date = member_date or datetime.now().strftime("%Y%m%d")
        print(f"[INIT] 开始初始化概念板块成分股，日期={member_date}...")

        concept_codes = self.db.get_all_concept_codes()
        total_concepts = len(concept_codes)
        concurrency = getattr(config, "CONCEPT_MEMBERS_CONCURRENCY", 8)
        progress_every = getattr(config, "CONCEPT_MEMBERS_PROGRESS_EVERY", 100)
        print(f"[INIT] 共 {total_concepts} 个概念，并发度={concurrency}")

        # 计数器与进度打印（多线程共享，需加锁）
        done_count = 0
        saved_records = 0
        counter_lock = threading.Lock()

        failed_codes = []
        success_count = 0

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            # 提交全部任务
            future_to_code = {
                executor.submit(self._fetch_one_concept_members, cc, member_date): cc
                for cc in concept_codes
            }

            # 主线程串行入库（避免并发写 sqlite）
            for future in as_completed(future_to_code):
                code, members = future.result()
                if members is None:
                    failed_codes.append(code)
                else:
                    if members:
                        self.db.save_concept_members(code, members, member_date)
                    success_count += 1
                    saved_records += len(members)

                # 进度打印
                with counter_lock:
                    done_count += 1
                    if done_count % progress_every == 0 or done_count == total_concepts:
                        print(
                            f"[INIT] 成分股进度 {done_count}/{total_concepts}"
                            f"（成功 {success_count}，失败 {len(failed_codes)}，已入库 {saved_records} 条）"
                        )

        # 汇总
        print(
            f"[INIT] 已保存共 {saved_records} 条成分股记录"
            f"（{total_concepts} 个概念中成功 {success_count} 个，失败 {len(failed_codes)} 个）"
        )
        if failed_codes:
            preview = ", ".join(failed_codes[:20])
            more = "" if len(failed_codes) <= 20 else f" ...（共 {len(failed_codes)} 个）"
            print(f"[INIT] 失败概念码: {preview}{more}")

        return saved_records

    # ========== 每日收盘后：行情同步 ==========
    def sync_daily_kline(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        batch_size: int = 50
    ):
        """步骤3: 批量同步日K线"""
        print(f"[SYNC] 开始同步日K线 {start_date} ~ {end_date}...")
        total_records = 0

        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            try:
                resp = self.client.get_history_quotation(batch, start_date, end_date)
                if "tables" not in resp:
                    continue

                for item in resp["tables"]:
                    code = item.get("thscode", "")
                    time_list = item.get("time", [])
                    table = item.get("table", {})

                    records = []
                    for idx, trade_date in enumerate(time_list):
                        record = {
                            "code": code,
                            "trade_date": trade_date.replace("-", "")
                        }
                        for field in ["preClose", "open", "high", "low", "close", "changeRatio", "volume", "amount"]:
                            values = table.get(field, [])
                            if idx < len(values):
                                # 字段名转小写
                                key = field.lower()
                                if field == "preClose":
                                    key = "pre_close"
                                elif field == "changeRatio":
                                    key = "change_ratio"
                                record[key] = values[idx]
                        records.append(record)

                    self.db.save_daily_kline(records)
                    total_records += len(records)

            except Exception as e:
                print(f"[WARN] 同步批次失败: {e}")

        print(f"[SYNC] 已保存 {total_records} 条日K线记录")
        return total_records

    # ========== 每日收盘后：计算 ==========
    def calc_daily_strength(self, calc_date: str):
        """步骤4: 计算板块强度评分"""
        print(f"[CALC] 开始计算板块强度评分，日期={calc_date}...")

        # 获取当日全部日K数据
        daily_data = self.db.get_daily_kline_by_date(calc_date)
        if not daily_data:
            print("[WARN] 当日无日K数据")
            return pd.DataFrame()

        daily_df = pd.DataFrame(daily_data)

        # 获取概念代码列表
        concept_codes = self.db.get_all_concept_codes()

        # 构建成分股映射（取最新缓存，永久缓存不依赖 calc_date）
        members_map = {}
        for cc in concept_codes:
            members = self.db.get_concept_members(cc)
            if members:
                members_map[cc] = [m["stock_code"] for m in members]

        # 计算强度评分
        result_df = calc_all_sectors_strength(daily_df, members_map)
        if result_df.empty:
            print("[WARN] 板块强度计算结果为空")
            return result_df

        # 保存到数据库
        records = []
        for _, row in result_df.iterrows():
            records.append({
                "calc_date": calc_date,
                "concept_code": row["concept_code"],
                "s1_return": row.get("s1_return"),
                "s2_breadth": row.get("s2_breadth"),
                "s4_relative": row.get("s4_relative"),
                "score_1d": row.get("score"),
                "score_final": row.get("score"),
                "rank_1d": row.get("rank_1d")
            })

        self.db.save_concept_strength(records)
        print(f"[CALC] 已保存 {len(records)} 个板块的强度评分")
        return result_df

    def calc_daily_attribution(self, calc_date: str, stock_codes: List[str] = None):
        """步骤5: 计算个股归因"""
        print(f"[CALC] 开始计算个股归因，日期={calc_date}...")

        # 获取当日全部日K数据
        daily_data = self.db.get_daily_kline_by_date(calc_date)
        if not daily_data:
            print("[WARN] 当日无日K数据")
            return []

        daily_df = pd.DataFrame(daily_data)
        returns_map = dict(zip(daily_df["code"], daily_df["change_ratio"]))

        # 获取概念收益映射：用概念成分股当日涨幅均值（复用已入库的股票K线）
        # 不再查概念指数本身的K线（daily 同步的 codes 是股票，不含概念指数）
        concept_codes = self.db.get_all_concept_codes()
        concept_returns = {}
        for cc in concept_codes:
            members = self.db.get_concept_members(cc)  # 取最新缓存
            if not members:
                continue
            member_returns = [
                returns_map[m["stock_code"]]
                for m in members
                if m["stock_code"] in returns_map
            ]
            if member_returns:
                concept_returns[cc] = sum(member_returns) / len(member_returns)

        # 计算归因：默认只对有概念映射的个股计算（避免对全市场无映射股票空查）
        if stock_codes is None:
            stock_codes = self.db.get_all_mapped_stock_codes()

        records = []
        for stock_code in stock_codes:
            stock_return = returns_map.get(stock_code, 0)
            concepts = self.db.get_stock_concepts(stock_code)  # 取最新缓存

            if not concepts:
                continue

            # 等权
            weight = 1.0 / len(concepts)
            concept_weights = {c["concept_code"]: weight for c in concepts}

            attributions = l1_stock_attribution(
                stock_code, stock_return, concept_weights, concept_returns
            )

            if attributions:
                top = attributions[0]
                records.append({
                    "stock_code": stock_code,
                    "calc_date": calc_date,
                    "total_return": stock_return,
                    "top_concept": top["concept_code"],
                    "top_contrib_pct": top.get("contrib_pct", 0),
                    "attributions": attributions
                })

        self.db.save_stock_attribution(records)
        print(f"[CALC] 已保存 {len(records)} 只个股的归因结果")
        return records

    # ========== 一键执行 ==========
    def run_init(self, stock_codes: List[str]):
        """首次部署：执行全部初始化步骤"""
        print("=" * 60)
        print("  首次部署：数据初始化")
        print("=" * 60)
        self.init_concept_dict()
        self.init_stock_concept_map(stock_codes)
        today = datetime.now().strftime("%Y%m%d")
        self.init_concept_members(today)
        print("=" * 60)
        print("  初始化完成")
        print("=" * 60)

    def run_daily(self, calc_date: str, all_codes: List[str] = None):
        """
        每日收盘后：执行全部同步和计算步骤
        :param all_codes: 需同步K线的代码列表；不传则自动从成分股表反查全市场股票池
        """
        print("=" * 60)
        print(f"  每日同步：{calc_date}")
        print("=" * 60)

        # 代码列表：未指定则反查全市场股票池
        if not all_codes:
            all_codes = self.db.get_all_member_stock_codes()
            print(f"[DAILY] 未指定代码列表，从成分股表反查全市场股票池: {len(all_codes)} 只")
        else:
            print(f"[DAILY] 使用指定代码列表: {len(all_codes)} 只")

        # 同步日K线
        self.sync_daily_kline(all_codes, calc_date, calc_date)

        # 计算板块强度
        self.calc_daily_strength(calc_date)

        # 计算个股归因
        self.calc_daily_attribution(calc_date)

        print("=" * 60)
        print("  每日同步完成")
        print("=" * 60)
