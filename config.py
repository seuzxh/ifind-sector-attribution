# -*- coding: utf-8 -*-
"""
配置模块 - iFinD 行业归因与板块强度检测系统

注意：Token 信息请通过环境变量或 config_local.py 本地配置，不要提交到版本控制。
"""

import os

# ========== iFinD API 配置 ==========
# Token 从环境变量读取，本地开发可创建 config_local.py 覆盖
BASE_URL_QUANT = "https://quantapi.51ifind.com/api/v1"
BASE_URL_FT = "https://ft.10jqka.com.cn/api/v1"

ACCESS_TOKEN = os.environ.get("IFIND_ACCESS_TOKEN", "")
REFRESH_TOKEN = os.environ.get("IFIND_REFRESH_TOKEN", "")

# 中焯行情 API 地址（kline-fetcher 分时数据源）。
# 默认从环境变量读，config_local.py（已 gitignore）的 * 导入会覆盖此默认值。
KLINE_API_BASE_URL = os.environ.get("KLINE_API_BASE_URL", "")

# 本地配置文件覆盖（config_local.py 已加入 .gitignore）
try:
    from config_local import *
except ImportError:
    pass

HEADERS = {
    "Content-Type": "application/json",
    "access_token": ACCESS_TOKEN
}

# ========== 数据库配置 ==========
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "sector_attribution.db")

# ========== 概念板块代码列表 ==========
# 已限定为 884 行业分类码（259 个）。完整列表见下方 SECTOR_POOL_CODES。
# 历史含 700/861/871/881 等前缀，已清理；init/daily 只处理 884 池。
# ALL_CONCEPT_CODES 在 SECTOR_POOL_CODES 定义后赋值（见下方）。

# ========== A股市场过滤 ==========
# 同花顺概念体系同时覆盖 A股 / 美股 / 港股 / 欧股等行业指数。
# 本系统只处理 A股（沪深北），以下工具用于在 init/daily 各环节过滤。

# A股个股代码后缀（沪深北交易所）
A_SHARE_SUFFIXES = (".SH", ".SZ", ".BJ")

# 有效 A股概念前缀白名单（成分股均为A股的概念编码段）
# 700/881/883/884/885/886 = A股行业与概念；其余 861/864/865/871/875 为海外
A_SHARE_CONCEPT_PREFIXES = ("700", "881", "883", "884", "885", "886")


def is_a_share_code(code: str) -> bool:
    """判断个股代码是否为 A股（沪深北交易所）"""
    return code is not None and code.endswith(A_SHARE_SUFFIXES)


def is_a_share_concept(concept_code: str) -> bool:
    """
    判断概念代码是否为 A股相关概念（按编码前缀白名单）。
    海外行业指数（861xxx[US]/871xxx[HK] 等）返回 False。
    """
    return concept_code is not None and concept_code[:3] in A_SHARE_CONCEPT_PREFIXES


# ========== 板块池白名单（限定观察的概念板块范围）==========
# 启用后，板块强度与个股归因只处理这些概念；为空/未启用则用全量 A 股概念。
# 当前限定为 884 行业分类码（有成分股数据，可用于板块强度和归因）。
# 注意：884 不在 stock_concept_map（个股从不被 API 打 884 标签），
# 所以归因改从 concept_members 反推个股所属 884 板块。
SECTOR_POOL_ENABLED = True
SECTOR_POOL_CODES = {
    "884001.TI","884002.TI","884003.TI","884004.TI","884005.TI","884006.TI","884009.TI","884010.TI",
    "884011.TI","884012.TI","884013.TI","884014.TI","884015.TI","884016.TI","884018.TI","884020.TI",
    "884021.TI","884022.TI","884023.TI","884024.TI","884025.TI","884026.TI","884027.TI","884028.TI",
    "884030.TI","884031.TI","884032.TI","884033.TI","884034.TI","884035.TI","884036.TI","884039.TI",
    "884041.TI","884043.TI","884044.TI","884045.TI","884046.TI","884048.TI","884050.TI","884051.TI",
    "884052.TI","884053.TI","884054.TI","884055.TI","884056.TI","884057.TI","884058.TI","884059.TI",
    "884060.TI","884062.TI","884063.TI","884064.TI","884065.TI","884066.TI","884067.TI","884068.TI",
    "884069.TI","884071.TI","884073.TI","884074.TI","884075.TI","884076.TI","884077.TI","884078.TI",
    "884080.TI","884081.TI","884082.TI","884083.TI","884084.TI","884085.TI","884086.TI","884088.TI",
    "884089.TI","884090.TI","884091.TI","884092.TI","884093.TI","884094.TI","884095.TI","884096.TI",
    "884098.TI","884099.TI","884100.TI","884101.TI","884105.TI","884106.TI","884107.TI","884112.TI",
    "884113.TI","884115.TI","884116.TI","884117.TI","884118.TI","884119.TI","884120.TI","884123.TI",
    "884124.TI","884125.TI","884126.TI","884128.TI","884130.TI","884131.TI","884132.TI","884136.TI",
    "884137.TI","884140.TI","884141.TI","884142.TI","884143.TI","884144.TI","884145.TI","884146.TI",
    "884147.TI","884149.TI","884150.TI","884152.TI","884153.TI","884154.TI","884155.TI","884156.TI",
    "884157.TI","884158.TI","884159.TI","884160.TI","884161.TI","884162.TI","884163.TI","884164.TI",
    "884165.TI","884167.TI","884168.TI","884172.TI","884176.TI","884177.TI","884178.TI","884180.TI",
    "884181.TI","884182.TI","884183.TI","884184.TI","884185.TI","884186.TI","884188.TI","884189.TI",
    "884191.TI","884192.TI","884193.TI","884195.TI","884197.TI","884199.TI","884200.TI","884201.TI",
    "884202.TI","884203.TI","884205.TI","884206.TI","884207.TI","884208.TI","884209.TI","884210.TI",
    "884211.TI","884212.TI","884213.TI","884214.TI","884215.TI","884217.TI","884218.TI","884219.TI",
    "884220.TI","884221.TI","884225.TI","884227.TI","884228.TI","884229.TI","884230.TI","884231.TI",
    "884232.TI","884233.TI","884234.TI","884235.TI","884236.TI","884237.TI","884238.TI","884239.TI",
    "884240.TI","884242.TI","884243.TI","884244.TI","884245.TI","884246.TI","884247.TI","884248.TI",
    "884249.TI","884250.TI","884251.TI","884252.TI","884253.TI","884254.TI","884255.TI","884256.TI",
    "884257.TI","884258.TI","884259.TI","884260.TI","884261.TI","884262.TI","884263.TI","884264.TI",
    "884265.TI","884266.TI","884267.TI","884268.TI","884269.TI","884270.TI","884271.TI","884272.TI",
    "884273.TI","884274.TI","884275.TI","884276.TI","884277.TI","884278.TI","884279.TI","884280.TI",
    "884281.TI","884282.TI","884283.TI","884284.TI","884285.TI","884286.TI","884287.TI","884288.TI",
    "884289.TI","884290.TI","884291.TI","884292.TI","884293.TI","884294.TI","884295.TI","884296.TI",
    "884297.TI","884298.TI","884299.TI","884300.TI","884301.TI","884302.TI","884303.TI","884304.TI",
    "884305.TI","884306.TI","884307.TI","884308.TI","884309.TI","884310.TI","884311.TI","884312.TI",
    "884313.TI","884314.TI","884315.TI",
}

# ALL_CONCEPT_CODES 复用板块池（init 字典/daily 同步只处理 884 池）
ALL_CONCEPT_CODES = list(SECTOR_POOL_CODES)


def is_in_sector_pool(concept_code: str) -> bool:
    """
    板块池过滤：池未启用或为空 → 返回 True（放行全部 A 股概念）；
    否则只放行 SECTOR_POOL_CODES 内的代码。
    """
    if not SECTOR_POOL_ENABLED or not SECTOR_POOL_CODES:
        return True
    return concept_code in SECTOR_POOL_CODES


# ========== 盘前筛选配置 ==========
# 盘前 watchlist 筛选参数
PRESCREEN_PERIOD_DAYS = 5      # 用近 N 个交易日的累计涨幅筛选
PRESCREEN_TOP_SECTOR = 20      # 选出的板块数
PRESCREEN_TOP_STOCK = 30       # 每个板块选出的成分股数
PRESCREEN_MIN_MEMBER = 6       # 板块最少成分股数（过滤迷你概念）
# 服务端定时自动筛选（留空则不启用，格式 "HH:MM" 如 "09:20"）
PRESCREEN_AUTO_TIME = ""

# ========== 分时数据配置（kline-fetcher / 中焯行情 API） ==========
# KLINE_API_BASE_URL 在文件顶部定义（走环境变量 + config_local.py 覆盖，敏感不入库）。
# 分时数据多线程拉取并发数（实测 32 并发即可打满服务端，64 无增益）
INTRADAY_WORKERS = 32
# 分时序列内存缓存 TTL（秒）。
# watchlist 模式拉取约 1.5s，TTL 设短一些让 3s 轮询大部分走缓存。
INTRADAY_CACHE_TTL = 5

# ========== 自选股分组配置 ==========
# 持仓分组名（自选股分组看板里，名称匹配此值的分组视为"持仓分组"，
# 其成分股作为持仓股，在含持仓的其他分组上做醒目标注）。按 block_name 精确匹配。
HOLDING_GROUP_NAME = "CC"


# ========== 计算配置 ==========
SCORE_WEIGHTS = {
    "s1": 0.40,   # 涨幅强度
    "s2": 0.30,   # 上涨广度
    "s4": 0.30    # 相对强度
}

PERIOD_WEIGHTS = {
    "1d": 0.50,
    "5d": 0.30,
    "20d": 0.20
}

# 板块强度计算时，命中 K 线数据的成分股数下限。
# 低于此值的概念不参与排名（样本过小导致 Z-score 失真）。
MIN_MEMBER_COUNT = 6

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
BATCH_SIZE = 100

# ========== init_concept_members 并发配置 ==========
# 接口2 (data_pool p03473) 不支持批量传多个概念代码，需逐概念拉取。
# 全量 1121 个概念用线程池并发，降低串行等待。
CONCEPT_MEMBERS_CONCURRENCY = 8       # 并发线程数
CONCEPT_MEMBERS_PROGRESS_EVERY = 100  # 每完成多少个概念打印一次进度
