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
from core_calculator import calc_all_sectors_strength, calc_multi_period_score, l1_stock_attribution
import config


class SyncPipeline:
    """数据同步管线"""

    def __init__(self):
        self.client = IFindClient()
        self.db = Database()

    # ========== 首次部署：初始化 ==========
    def init_concept_dict(self):
        """步骤0: 初始化概念板块字典（永久缓存，仅 A股相关概念）"""
        print("[INIT] 开始初始化概念板块字典...")
        # 过滤掉海外行业指数（861xxx[US]/871xxx[HK] 等），只保留 A股概念
        a_share_codes = [c for c in config.ALL_CONCEPT_CODES if config.is_a_share_concept(c)]
        skipped = len(config.ALL_CONCEPT_CODES) - len(a_share_codes)
        if skipped:
            print(f"[INIT] 过滤 {skipped} 个海外行业指数概念，仅保留 A股概念 {len(a_share_codes)} 个")
        concepts = self.client.batch_get_concept_basic_info(
            a_share_codes,
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
        concept_codes = self.db.get_a_share_concept_codes()
        print(f"[INIT] 共 {len(concept_codes)} 个概念")
        return self._fetch_concept_members_batch(concept_codes, member_date)

    def init_concept_universe(self, map_date: str = None):
        """
        扫描全市场股票收集实际在用的概念板块码（885xxx/886xxx 等），
        并补全这些概念的字典信息与成分股。

        背景：config.ALL_CONCEPT_CODES 只含行业分类码（700xxx/884xxx），
        而接口1 返回的个股概念是另一套编码（885xxx），两套交集为 0，
        导致 get_stock_concepts 的 JOIN 恒为空、归因无法工作。
        本方法增量补充概念板块码，让两套体系统一可用于归因。

        注意：板块池启用（仅 884）时跳过此扫描——884 池不依赖 885/886，
        且归因改从 concept_members 反推，无需补全概念标签码。
        """
        if config.SECTOR_POOL_ENABLED and config.SECTOR_POOL_CODES:
            print("[UNIVERSE] 板块池已启用（仅 884），跳过 885/886 概念扫描")
            return
        print("=" * 60)
        print("  补全概念板块全集（扫描全市场股票）")
        print("=" * 60)
        map_date = map_date or datetime.now().strftime("%Y-%m-%d")

        # 1) 全市场股票
        all_stocks = self.db.get_all_member_stock_codes()
        print(f"[UNIVERSE] 全市场股票 {len(all_stocks)} 只")

        # 2) 分批扫描接口1，收集概念码 + 累积全市场个股-概念映射
        collected = {}  # {concept_code: concept_name}（接口1 同时返回名字）
        all_mappings = {}  # {stock_code: [{concept_name, concept_code}, ...]} 全市场映射
        batch_size = config.BATCH_SIZE
        for i in range(0, len(all_stocks), batch_size):
            batch = all_stocks[i:i + batch_size]
            mappings = self.client.batch_get_stock_concepts(batch, map_date)
            # 过滤掉海外概念，只保留 A股概念映射
            for stock_code, concepts in mappings.items():
                a_concepts = [c for c in concepts if config.is_a_share_concept(c.get("concept_code", ""))]
                if a_concepts:
                    all_mappings[stock_code] = a_concepts
                    for c in a_concepts:
                        cc = c.get("concept_code")
                        if cc and cc not in collected:
                            collected[cc] = c.get("concept_name", "")
            if (i // batch_size) % 5 == 0:
                print(f"[UNIVERSE] 扫描进度 {min(i + batch_size, len(all_stocks))}/{len(all_stocks)}，已收集 A股概念码 {len(collected)}，映射 {len(all_mappings)} 只")
        print(f"[UNIVERSE] 扫描完成，共收集 A股概念码 {len(collected)} 个，映射 {len(all_mappings)} 只股票")

        # 2.5) 把全市场映射存入 stock_concept_map（归因依赖此表）
        self.db.save_stock_concept_map(all_mappings, map_date)
        print(f"[UNIVERSE] 已更新 stock_concept_map：{len(all_mappings)} 只股票")

        # 3) 筛选字典里还没有的 A股概念码（过滤海外概念 861/871 等）
        existing = set(self.db.get_a_share_concept_codes())
        new_codes = [
            cc for cc in collected
            if cc not in existing and config.is_a_share_concept(cc)
        ]
        skipped_overseas = len(collected) - len([c for c in collected if config.is_a_share_concept(c)])
        if skipped_overseas:
            print(f"[UNIVERSE] 过滤 {skipped_overseas} 个海外概念，仅补充 A股概念")
        print(f"[UNIVERSE] 其中字典里已有的: {len(collected) - len(new_codes) - skipped_overseas}，需新增: {len(new_codes)}")

        if not new_codes:
            print("[UNIVERSE] 无需补充，概念字典已覆盖")
            return 0

        # 4) 接口5 批量补全概念字典（名字等）
        print(f"[UNIVERSE] 调用接口5 补全 {len(new_codes)} 个概念的字典信息...")
        concepts_info = self.client.batch_get_concept_basic_info(new_codes, batch_size=100)
        # 接口5 返回的结构与 save_concept_dict 期望一致
        self.db.save_concept_dict(concepts_info)
        print(f"[UNIVERSE] 字典已补全 {len(concepts_info)} 个概念")

        # 5) 接口2 并发补全成分股（复用现有并发逻辑）
        print(f"[UNIVERSE] 调用接口2 补全 {len(new_codes)} 个概念的成分股...")
        today_compact = datetime.now().strftime("%Y%m%d")
        # 直接复用 init_concept_members 的并发内核，传入限定概念列表
        self._fetch_concept_members_batch(new_codes, today_compact)

        print("=" * 60)
        print("  概念板块全集补全完成")
        print("=" * 60)
        return len(new_codes)

    def _fetch_concept_members_batch(self, concept_codes: List[str], member_date: str):
        """
        并发拉取给定概念列表的成分股并入库（从 init_concept_members 抽出的复用内核）。
        """
        total_concepts = len(concept_codes)
        concurrency = getattr(config, "CONCEPT_MEMBERS_CONCURRENCY", 8)
        progress_every = getattr(config, "CONCEPT_MEMBERS_PROGRESS_EVERY", 100)

        done_count = 0
        saved_records = 0
        counter_lock = threading.Lock()
        failed_codes = []
        success_count = 0

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_code = {
                executor.submit(self._fetch_one_concept_members, cc, member_date): cc
                for cc in concept_codes
            }
            for future in as_completed(future_to_code):
                code, members = future.result()
                if members is None:
                    failed_codes.append(code)
                else:
                    if members:
                        self.db.save_concept_members(code, members, member_date)
                    success_count += 1
                    saved_records += len(members)
                with counter_lock:
                    done_count += 1
                    if done_count % progress_every == 0 or done_count == total_concepts:
                        print(
                            f"[UNIVERSE] 成分股进度 {done_count}/{total_concepts}"
                            f"（成功 {success_count}，失败 {len(failed_codes)}，已入库 {saved_records} 条）"
                        )

        print(
            f"[UNIVERSE] 已保存共 {saved_records} 条成分股记录"
            f"（{total_concepts} 个概念中成功 {success_count} 个，失败 {len(failed_codes)} 个）"
        )
        if failed_codes:
            preview = ", ".join(failed_codes[:20])
            more = "" if len(failed_codes) <= 20 else f" ...（共 {len(failed_codes)} 个）"
            print(f"[UNIVERSE] 失败概念码: {preview}{more}")
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
        """步骤4: 计算板块强度评分（多周期融合）"""
        print(f"[CALC] 开始计算板块强度评分，日期={calc_date}...")

        # 获取当日全部日K数据（融合函数内部还会按需取多日数据）
        daily_data = self.db.get_daily_kline_by_date(calc_date)
        if not daily_data:
            print("[WARN] 当日无日K数据")
            return pd.DataFrame()

        # 获取概念代码列表
        concept_codes = self.db.get_a_share_concept_codes()

        # 构建成分股映射（取最新缓存，永久缓存不依赖 calc_date）
        members_map = {}
        for cc in concept_codes:
            members = self.db.get_concept_members(cc)
            if members:
                members_map[cc] = [m["stock_code"] for m in members]

        # 多周期融合评分（1d/5d/20d）；5d/20d 依赖历史K线，缺失则退化为 1d
        result_df = calc_multi_period_score(self.db, calc_date, members_map)
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
                "score_1d": row.get("score_1d"),
                "score_5d": row.get("score_5d", 0.0),
                "score_20d": row.get("score_20d", 0.0),
                "score_final": row.get("score_final"),
                "rank_1d": row.get("rank_1d")
            })

        self.db.save_concept_strength(records)
        print(f"[CALC] 已保存 {len(records)} 个板块的多周期融合评分")
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
        # 用 nanmean 忽略停牌等导致的 nan，避免单个 nan 传染整个均值
        concept_codes = self.db.get_a_share_concept_codes()
        concept_returns = {}
        for cc in concept_codes:
            members = self.db.get_concept_members(cc)  # 取最新缓存
            if not members:
                continue
            member_returns = [
                returns_map[m["stock_code"]]
                for m in members
                if m["stock_code"] in returns_map
                and not pd.isna(returns_map[m["stock_code"]])
            ]
            if member_returns:
                concept_returns[cc] = sum(member_returns) / len(member_returns)

        # 计算归因：默认只对有概念映射的个股计算（避免对全市场无映射股票空查）
        # 板块池启用（884）时，用成分股表反推的股票池（884 不在 stock_concept_map）
        pool_enabled = config.SECTOR_POOL_ENABLED and config.SECTOR_POOL_CODES
        if stock_codes is None:
            stock_codes = (self.db.get_all_member_stock_codes() if pool_enabled
                           else self.db.get_all_mapped_stock_codes())

        records = []
        for stock_code in stock_codes:
            stock_return = returns_map.get(stock_code, 0)
            # 跳过当日停牌等导致涨幅为 nan 的股票
            if pd.isna(stock_return):
                continue
            # 板块池启用时从 concept_members 反推归属（884 无 stock_concept_map 标签）
            concepts = (self.db.get_stock_concepts_from_members(stock_code) if pool_enabled
                        else self.db.get_stock_concepts(stock_code))

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
    def run_init(self, stock_codes: List[str] = None):
        """
        首次部署：执行全部初始化步骤
        :param stock_codes: 个股列表；不传则用全市场股票（从成分股表反查）
        """
        print("=" * 60)
        print("  首次部署：数据初始化")
        print("=" * 60)

        # 0) 行业分类概念字典（config.ALL_CONCEPT_CODES）
        self.init_concept_dict()

        # 1) 个股-概念映射（接口1）；未指定股票则用全市场
        if not stock_codes:
            # 首次部署时成分股表可能为空，回退到接口2 已知能返回数据的样本概念
            # 这里用 config 默认测试股票兜底，保证有起步数据
            stock_codes = getattr(config, "DEFAULT_INIT_STOCKS", None) or [
                "688001.SH", "600004.SH", "000001.SZ", "300001.SZ",
                "300033.SZ", "600030.SH", "000063.SZ", "000066.SZ"
            ]
            print(f"[INIT] 未指定股票列表，使用默认样本 {len(stock_codes)} 只（后续可用 init_concept_universe 扩展全市场）")
        self.init_stock_concept_map(stock_codes)

        # 2) 行业码成分股
        today = datetime.now().strftime("%Y%m%d")
        self.init_concept_members(today)

        # 3) 扫描已有映射，补全概念板块码（885xxx 等）的字典+成分股
        #    让归因的 JOIN 能打通（行业码与概念码两套体系统一）
        self.init_concept_universe()

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
