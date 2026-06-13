# -*- coding: utf-8 -*-
"""
iFinD API 客户端封装
支持 5 个核心接口：
  1. basic_data_service - 个股所属同花顺概念
  2. data_pool (p03473) - 概念板块成分股
  3. cmd_history_quotation - 历史行情日K
  4. high_frequency - 高频序列1min K
  5. basic_data_service - 概念基本信息（字典初始化）
"""

import time
import requests
from typing import List, Dict, Optional, Any

import config


class IFindClient:
    """iFinD API 客户端"""

    def __init__(self):
        self.base_url_quant = config.BASE_URL_QUANT
        self.base_url_ft = config.BASE_URL_FT
        self.headers = config.HEADERS.copy()
        self.timeout = config.REQUEST_TIMEOUT
        self.max_retries = config.MAX_RETRIES

    def _post(self, url: str, payload: dict) -> dict:
        """带重试的 POST 请求"""
        for attempt in range(self.max_retries):
            try:
                resp = requests.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout
                )
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(2 ** attempt)  # 指数退避
        return {}

    # ========== 接口1: 个股所属同花顺概念 ==========
    def get_stock_concepts(self, stock_codes: List[str], date: str) -> dict:
        """
        获取个股所属同花顺概念板块
        :param stock_codes: 股票代码列表，如 ["688001.SH", "600004.SH"]
        :param date: 查询日期，如 "2026-06-13"
        :return: API 原始响应
        """
        url = f"{self.base_url_quant}/basic_data_service"
        codes_str = ",".join(stock_codes)
        payload = {
            "codes": codes_str,
            "indipara": [
                {
                    "indicator": "ths_the_ths_concept_index_stock",
                    "indiparams": [date]
                },
                {
                    "indicator": "ths_the_ths_concept_index_code_stock",
                    "indiparams": [date]
                }
            ]
        }
        return self._post(url, payload)

    # ========== 接口2: 概念板块成分股 ==========
    def get_concept_members(self, concept_code: str, date: str) -> dict:
        """
        获取同花顺概念板块成分股（p03473）
        :param concept_code: 概念指数代码，如 "886102.TI"
        :param date: 查询日期，如 "20260613"
        :return: API 原始响应
        """
        url = f"{self.base_url_quant}/data_pool"
        payload = {
            "reportname": "p03473",
            "functionpara": {
                "iv_date": date,
                "iv_zsdm": concept_code
            },
            "outputpara": "p03473_f001,p03473_f002,p03473_f003"
        }
        return self._post(url, payload)

    # ========== 接口3: 历史行情日K ==========
    def get_history_quotation(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        indicators: str = "preClose,open,high,low,close,changeRatio",
        interval: str = "D",
        cps: str = "6"
    ) -> dict:
        """
        获取历史行情日K线
        :param codes: 股票/指数代码列表
        :param start_date: 开始日期，如 "2026-06-01"
        :param end_date: 结束日期，如 "2026-06-13"
        :param indicators: 指标列表，逗号分隔
        :param interval: 周期，D=日 W=周 M=月
        :param cps: 复权方式，6=前复权(现金分红)
        :return: API 原始响应
        """
        url = f"{self.base_url_ft}/cmd_history_quotation"
        codes_str = ",".join(codes)
        payload = {
            "codes": codes_str,
            "indicators": indicators,
            "startdate": start_date,
            "enddate": end_date,
            "functionpara": {
                "Fill": "Previous",
                "Interval": interval,
                "CPS": cps
            }
        }
        return self._post(url, payload)

    # ========== 接口4: 高频序列1min K ==========
    def get_high_frequency(
        self,
        codes: List[str],
        start_time: str,
        end_time: str,
        indicators: str = "open,high,low,close,changeRatio"
    ) -> dict:
        """
        获取高频序列（1min K线）
        :param codes: 股票/指数代码列表
        :param start_time: 开始时间，如 "2026-06-13 09:30:00"
        :param end_time: 结束时间，如 "2026-06-13 10:00:00"
        :param indicators: 指标列表
        :return: API 原始响应
        """
        url = f"{self.base_url_ft}/high_frequency"
        codes_str = ",".join(codes)
        payload = {
            "codes": codes_str,
            "indicators": indicators,
            "starttime": start_time,
            "endtime": end_time,
            "functionpara": {}
        }
        return self._post(url, payload)

    # ========== 接口5: 概念基本信息（字典初始化） ==========
    def get_concept_basic_info(self, concept_codes: List[str]) -> dict:
        """
        获取同花顺概念指数基本信息
        :param concept_codes: 概念指数代码列表
        :return: API 原始响应
        """
        url = f"{self.base_url_quant}/basic_data_service"
        codes_str = ",".join(concept_codes)
        payload = {
            "codes": codes_str,
            "indipara": [
                {"indicator": "ths_index_short_name_index", "indiparams": []},
                {"indicator": "ths_index_full_name_index", "indiparams": []},
                {"indicator": "ths_index_code_index", "indiparams": []},
                {"indicator": "ths_main_sec_code_index", "indiparams": []},
                {"indicator": "ths_thscode_index", "indiparams": []}
            ]
        }
        return self._post(url, payload)

    # ========== 批量查询工具 ==========
    def batch_get_stock_concepts(
        self,
        all_stock_codes: List[str],
        date: str,
        batch_size: int = None
    ) -> Dict[str, List[Dict[str, str]]]:
        """
        批量获取全市场个股所属概念（自动分批）
        :param all_stock_codes: 全市场股票代码列表
        :param date: 查询日期
        :param batch_size: 每批数量，默认取 config.BATCH_SIZE
        :return: {stock_code: [{concept_name, concept_code}, ...]}
        """
        batch_size = batch_size or config.BATCH_SIZE
        result = {}

        for i in range(0, len(all_stock_codes), batch_size):
            batch = all_stock_codes[i:i + batch_size]
            resp = self.get_stock_concepts(batch, date)

            # 解析响应 - 每个股票一个 table 对象
            if "tables" in resp and len(resp["tables"]) > 0:
                for item in resp["tables"]:
                    stock_code = item.get("thscode", "")
                    table = item.get("table", {})

                    concept_names = table.get("ths_the_ths_concept_index_stock", [])
                    concept_codes_list = table.get("ths_the_ths_concept_index_code_stock", [])

                    if concept_names and len(concept_names) > 0:
                        name_str = concept_names[0]
                        code_str = concept_codes_list[0] if concept_codes_list else ""

                        # 同花顺返回的是逗号分隔的字符串
                        names = [n.strip() for n in str(name_str).split(",") if n.strip()]
                        codes = [c.strip() for c in str(code_str).split(",") if c.strip()]

                        result[stock_code] = [
                            {"concept_name": n, "concept_code": c}
                            for n, c in zip(names, codes)
                        ]

        return result

    def batch_get_concept_basic_info(
        self,
        all_concept_codes: List[str],
        batch_size: int = 100
    ) -> List[Dict[str, str]]:
        """
        批量获取概念基本信息（自动分批）
        :param all_concept_codes: 概念代码列表
        :param batch_size: 每批数量
        :return: [{concept_code, short_name, full_name, index_code, main_code, thscode}, ...]
        """
        result = []

        for i in range(0, len(all_concept_codes), batch_size):
            batch = all_concept_codes[i:i + batch_size]
            resp = self.get_concept_basic_info(batch)

            # 解析响应 - 每个概念一个 table 对象
            if "tables" in resp and len(resp["tables"]) > 0:
                for item in resp["tables"]:
                    concept_code = item.get("thscode", "")
                    table = item.get("table", {})

                    short_names = table.get("ths_index_short_name_index", [])
                    full_names = table.get("ths_index_full_name_index", [])
                    index_codes = table.get("ths_index_code_index", [])
                    main_codes = table.get("ths_main_sec_code_index", [])
                    thscodes = table.get("ths_thscode_index", [])

                    result.append({
                        "concept_code": concept_code,
                        "short_name": short_names[0] if short_names else "",
                        "full_name": full_names[0] if full_names else "",
                        "index_code": index_codes[0] if index_codes else "",
                        "main_code": main_codes[0] if main_codes else "",
                        "thscode": thscodes[0] if thscodes else ""
                    })

        return result
