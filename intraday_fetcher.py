# -*- coding: utf-8 -*-
"""
分时数据批量获取封装（基于 kline-fetcher 的 TrendFetcher）

数据源：中焯行情 API（KLINE_API_BASE_URL），通过 kline-fetcher 包调用。
返回每只股票的完整分时序列（集合竞价 + 盘中逐分钟），供上层切片计算。

返回结构（per stock）:
    {
        "code": "600519.SH",          # 归一化为项目用的 .SH/.SZ/.BJ 格式
        "pre_close": 1279.0,          # 昨收 = pre_market[0].ref_price
        "open": 1271.18,              # 开盘价 = pre_market[-1].ref_price（09:25 集合竞价价）
        "trading": [                  # 盘中逐分钟点（09:30 ~ 15:00）
            {"time": "09:30", "last_price": 1271.18, "avg_price": 1271.18,
             "volume": 41537.0, "turnover": 52801004.0},
            ...
        ],
    }
"""

import os
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from kline_fetcher import TrendFetcher


def _to_kf_code(code: str) -> str:
    """
    项目代码（600519.SH）→ kline-fetcher 代码（SH600519）。
    TrendFetcher 会自动推断市场，但显式前缀更稳妥。
    """
    if "." in code:
        pure, suffix = code.split(".")
        return f"{suffix}{pure}"
    return code


def _norm_time(t: str) -> str:
    """'09:30:00' → '09:30'（去掉秒，分钟粒度对齐）"""
    return t[:5] if t and len(t) >= 5 else t


class IntradayFetcher:
    """
    分时数据批量获取器。

    用法：
        f = IntradayFetcher()
        series = f.fetch_batch(["600519.SH", "000001.SZ"], date="20260612")
        # series = {code: {pre_close, open, trading:[...]}, ...}
    """

    def __init__(self, workers: int = None):
        self.workers = workers or config.INTRADAY_WORKERS
        # 确保 TrendFetcher 能拿到 base_url（环境变量或 config_local 已注入 config.KLINE_API_BASE_URL）
        if not os.environ.get("KLINE_API_BASE_URL") and getattr(config, "KLINE_API_BASE_URL", ""):
            os.environ["KLINE_API_BASE_URL"] = config.KLINE_API_BASE_URL
        if not os.environ.get("KLINE_API_BASE_URL"):
            raise EnvironmentError(
                "KLINE_API_BASE_URL 未配置。请在 config_local.py 设置或 export KLINE_API_BASE_URL。"
            )

    def _fetch_one(self, code: str, date: Optional[str]) -> Optional[dict]:
        """拉单只股票分时，归一化为项目格式。每线程独立 TrendFetcher 实例（绕过共享 throttle）。"""
        try:
            fetcher = TrendFetcher()
            data = fetcher.fetch_trend(_to_kf_code(code), date=date)
        except Exception as e:
            print(f"[INTRADAY] {code} 拉取失败: {e}")
            return None

        if not data:
            return None

        pm = data.get("pre_market") or []
        tr = data.get("trading") or []
        if not pm and not tr:
            return None

        # 昨收 = pre_market 起始 ref_price；开盘价 = pre_market 末尾 ref_price
        refs = [p.get("ref_price") for p in pm if p.get("ref_price") is not None]
        pre_close = refs[0] if refs else None
        open_price = refs[-1] if refs else None

        # 盘中序列归一化：时间去秒、字段对齐
        trading = []
        for p in tr:
            lp = p.get("last_price")
            if lp is None:
                continue
            trading.append({
                "time": _norm_time(p.get("time", "")),
                "last_price": float(lp),
                "avg_price": float(p.get("avg_price", 0) or 0),
                "volume": float(p.get("volume", 0) or 0),
                "turnover": float(p.get("turnover", 0) or 0),
            })

        # 无昨收时回退用第一笔 last_price（极端情况，避免除零）
        if pre_close is None and trading:
            pre_close = trading[0]["last_price"]
        if open_price is None and trading:
            open_price = trading[0]["last_price"]

        return {
            "code": code,
            "pre_close": pre_close,
            "open": open_price,
            "trading": trading,
        }

    def fetch_batch(
        self,
        codes: List[str],
        date: Optional[str] = None,
    ) -> Dict[str, dict]:
        """
        多线程批量拉取分时序列。

        :param codes: 项目格式代码列表（600519.SH）
        :param date: None 或 "0" = 当日实时；"YYYYMMDD" = 历史某日全天
        :return: {code: {pre_close, open, trading:[...]}, ...}，失败的 code 不出现
        """
        if not codes:
            return {}

        result = {}
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self._fetch_one, c, date): c for c in codes}
            for fu in as_completed(futs):
                code = futs[fu]
                try:
                    rec = fu.result()
                    if rec:
                        result[code] = rec
                except Exception as e:
                    print(f"[INTRADAY] {code} 异常: {e}")
                    continue

        ok = len(result)
        total = len(codes)
        print(f"[INTRADAY] 拉取完成: {ok}/{total} 只成功"
              f"{'（当日实时）' if not date or date == '0' else f'（{date} 历史）'}")
        return result
