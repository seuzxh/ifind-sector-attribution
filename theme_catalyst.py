# -*- coding: utf-8 -*-
"""题材催化反向归因引擎（方案书 v1.1 首版实现，2026-09-17）。

从"股票一起涨"反向识别"资金在交易什么"：对目标股票集合 S 生成候选题材，
八因子评分（CatalystScore）→ Jaccard 重叠压缩 → 加权集合覆盖提取 1~3 条主线。

v1 口径（用户确认）：
  1. 不含资讯验证（REQ-14~17 P4）、不依赖盘中行情快照接口
  2. 个股因子全部由 1min/分时序列（kline-fetcher，实时+历史）推导
  3. 涨跌停按市场规则：主板 10%（ST 5%）/ 创业科创 20% / 北交所 30%；
     池内个股用精确涨停价（昨收×(1+限幅) 四舍五入到分），
     题材全成分横截面用涨跌幅阈值近似（±0.2pp 缓冲）
  4. 股池剔除：ST、当日停牌（无成交）、收盘价 < 5 元

数据依赖（全部现有接口/本地库）：
  - 成分：data_pool p03473（三指数池）；历史复用 style_backtest.db bt_member
  - 概念映射：basic_data_service（当日）；历史复用 bt_map
  - 分时：kline-fetcher TrendFetcher（经 intraday_fetcher.IntradayFetcher）
  - 题材全成分与规模：生产库 concept_members（最新快照，结果标注时点）；
    历史规模 bt_concept_size
  - 概念指数/个股日K：style_backtest.db bt_kline（Z20/连板高度）
  - 全市场横截面（仅当日）：smart_stock_picking（U 全集 + 收盘价/涨跌幅/名称）
"""

import functools
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from intraday_fetcher import IntradayFetcher
import open_scan_engine as ose
from stock_scorer import get_board_type

ROOT = Path(__file__).resolve().parent
BT_DB = ROOT / "data" / "style_backtest.db"
PROD_DB = ROOT / "data" / "sector_attribution.db"
TC_DB = ROOT / "data" / "theme_catalyst.db"

# 方案书 v1.1 权重（无资讯维度；分钟数据可用故 Synchronization 保留）
WEIGHTS = {
    "coverage": 0.15, "lift": 0.18, "contribution": 0.17,
    "abnormal": 0.13, "sync": 0.12, "limit": 0.10,
    "leader": 0.10, "liquidity": 0.05,
}
PARAMS = {
    "min_hit_count": 3,
    "min_theme_members": 6,        # 板块最小有效成分数（候选过滤）
    "overlap_jaccard": 0.70,
    "max_primary": 3,
    "target_explain": 0.60,        # 主线集合期望解释的上涨贡献比例下限
    "z20_window": 20,
    "board_height_days": 10,       # 连板高度回看天数
    "trigger_pct": 3.0,            # 首次异动：分钟累计涨幅阈值
    "sync_windows": (1, 3, 5),     # 分钟
    "min_price": 5.0,
    "limit_buf": 0.2,              # 涨跌幅阈值判定的缓冲（pp）
}

STYLE_INDICES = {"883926.TI": "高贝塔值", "883409.TI": "近期强势", "883910.TI": "同花顺热股"}


# ==================== 涨跌停规则（REQ-07A） ====================

def limit_pct(code: str, name: str = "") -> float:
    """按市场与 ST 状态返回涨跌幅限制（%）。"""
    bt = get_board_type(code)
    is_st = "ST" in (name or "").upper()
    if bt == "main":
        return 5.0 if is_st else 10.0
    if bt == "gem_star":
        return 20.0          # 创业板/科创板 ST 同为 20%
    return 30.0              # 北交所（ST 同为 30%）


def exact_limit_prices(pre_close: float, code: str, name: str = ""):
    """精确涨停/跌停价 = round(昨收 × (1±限幅), 2)（交易所四舍五入规则）。"""
    pct = Decimal(str(limit_pct(code, name))) / Decimal(100)
    pc = Decimal(str(pre_close))
    up = (pc * (1 + pct)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    dn = (pc * (1 - pct)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(up), float(dn)


def limit_state_from_chg(code, name, change_ratio, buf=None):
    """横截面近似判定（无精确价格时）：涨跌幅 + 板块阈值缓冲。"""
    buf = PARAMS["limit_buf"] if buf is None else buf
    pct = limit_pct(code, name)
    if change_ratio is None:
        return ""
    if change_ratio >= pct - buf:
        return "up"
    if change_ratio <= -(pct - buf):
        return "down"
    return ""


# ==================== 分时 → 个股特征 ====================

def stock_features_from_trend(code, series, name=""):
    """由分时序列推导个股日频特征（全部字段 1min/分时口径）。

    返回 dict：ret/body_ret/amount/open/pre_close/close + 涨跌停状态 + 首次异动分钟。
    涨跌停用精确涨停价判分钟收盘价序列：触板/炸板/回封/收盘封住。
    """
    tr = series.get("trading") or []
    if not tr:
        return None  # 当日停牌/无成交
    pre_close = series.get("pre_close")
    if not pre_close or pre_close <= 0:
        return None
    open_px = ose._open_price(series)
    closes = [p["last_price"] for p in tr]
    amount = sum(p.get("turnover", 0) for p in tr)
    close = closes[-1]

    up_px, dn_px = exact_limit_prices(pre_close, code, name)
    touched_up = any(c >= up_px for c in closes)
    touched_dn = any(c <= dn_px for c in closes)
    sealed_up = close >= up_px
    sealed_dn = close <= dn_px
    state = ""
    if sealed_up:
        state = "seal_up"
        # 回封：中途曾跌破涨停价后收盘再封
        below_after_touch = False
        seen_touch = False
        for c in closes:
            if c >= up_px:
                seen_touch = True
            elif seen_touch and c < up_px:
                below_after_touch = True
        reseal = below_after_touch
    elif touched_up:
        state = "break_up"  # 炸板
        reseal = False
    elif sealed_dn:
        state = "seal_down"
        reseal = False
    elif touched_dn:
        state = "break_dn"
        reseal = False
    else:
        reseal = False

    # 首次异动分钟：累计涨幅首次 ≥ trigger_pct
    trig_min = None
    th = PARAMS["trigger_pct"]
    for p in tr:
        if p["last_price"] >= pre_close * (1 + th / 100):
            trig_min = p["time"]
            break

    ret = (close / pre_close - 1) * 100
    body = (close / open_px - 1) * 100 if open_px and open_px > 0 else None
    return {
        "code": code, "name": name, "ret": ret, "body_ret": body,
        "amount": amount, "open": open_px, "pre_close": pre_close,
        "close": close, "limit_state": state, "touched_up": touched_up,
        "reseal": reseal, "trigger_min": trig_min,
    }


# ==================== 数据访问 ====================

def bt_conn():
    return sqlite3.connect(f"file:{BT_DB}?mode=ro", uri=True)


def get_style_members(day: str):
    """三指数成分（历史复用回测库；当日直接 p03473 拉取后写回由 runner 负责）。"""
    con = bt_conn()
    rows = con.execute(
        "SELECT stock_code FROM bt_member WHERE trade_date=?", (day,)).fetchall()
    con.close()
    return sorted({r[0] for r in rows})


def get_mapping(day: str, codes, client):
    """股票→概念/行业映射。历史优先回测库（point-in-time），缺失或当日走 API。"""
    con = bt_conn()
    q = ",".join("?" * len(codes))
    rows = con.execute(
        f"SELECT stock_code, concept_code, concept_name FROM bt_map "
        f"WHERE trade_date=? AND stock_code IN ({q})", [day, *codes]).fetchall()
    con.close()
    if len({r[0] for r in rows}) >= len(codes) * 0.8:
        m = defaultdict(list)
        for sc, cc, cn in rows:
            if cc.startswith(("885", "881", "884")):
                m[sc].append((cc, cn))
        return dict(m)
    # 当日/覆盖不足：API 实时拉
    api = client.batch_get_stock_concepts(sorted(codes), iso_day(day))
    m = defaultdict(list)
    for sc, concepts in api.items():
        for cpt in concepts:
            cc = cpt.get("concept_code", "")
            if cc.startswith(("885", "881", "884")):
                m[sc].append((cc, cpt.get("concept_name", "")))
    return dict(m)


def iso_day(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}" if "-" not in day else day


def theme_members_and_size(day: str):
    """题材全成分（生产库最新快照，标注时点）+ 题材规模。"""
    con = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    snap = con.execute(
        "SELECT MAX(member_date) FROM concept_members").fetchone()[0]
    rows = con.execute(
        "SELECT concept_code, stock_code, stock_name FROM concept_members "
        "WHERE member_date=?", (snap,)).fetchall()
    names = dict(con.execute(
        "SELECT concept_code, concept_name FROM ths_concept_dict").fetchall())
    con.close()
    members = defaultdict(set)
    for cc, sc, _ in rows:
        members[cc].add(sc)
    sizes = {cc: len(v) for cc, v in members.items()}
    # 历史规模快照（回测库）覆盖历史日
    btc = bt_conn()
    for snap_d, cc, n in btc.execute(
            "SELECT snap_date, concept_code, n_members FROM bt_concept_size"):
        if snap_d <= day and (cc not in sizes or abs(sizes[cc] - n) > n * 0.3):
            sizes.setdefault(cc, n)
    btc.close()
    return members, sizes, names, snap


def concept_index_z20(day: str, concept_codes):
    """概念指数 20 日 Z 值（异常强度主成分）。"""
    con = bt_conn()
    out = {}
    for cc in concept_codes:
        rows = con.execute(
            "SELECT trade_date, change_ratio FROM bt_kline "
            "WHERE code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 21",
            (cc, day)).fetchall()
        if len(rows) >= 11 and rows[0][0] == day and rows[0][1] is not None:
            hist = [r[1] for r in rows[1:] if r[1] is not None]
            if len(hist) >= 10:
                sd = statistics.pstdev(hist)
                if sd > 1e-9:
                    out[cc] = (rows[0][1] - statistics.mean(hist)) / sd
    con.close()
    return out


@functools.lru_cache(maxsize=200000)
def board_height(day: str, code, name, chg_today):
    """连板高度：自当日向前逐日涨停判定（涨跌幅阈值口径）。"""
    con = bt_conn()
    rows = con.execute(
        "SELECT trade_date, change_ratio FROM bt_kline WHERE code=? "
        "AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        (code, day, PARAMS["board_height_days"])).fetchall()
    con.close()
    if not rows or rows[0][0] != day:
        rows = [(day, chg_today)] + rows
    h = 0
    for _, chg in rows:
        if chg is not None and limit_state_from_chg(code, name, chg) == "up":
            h += 1
        else:
            break
    return h


# ==================== 归因主流程 ====================

class ThemeCatalystEngine:
    def __init__(self, client=None, workers=32):
        self.client = client
        self.fetcher = IntradayFetcher(workers=workers)

    # ---- 市场横截面（仅当日，smart_pick；历史日由 runner 预备后传入） ----
    def market_snapshot(self, day: str):
        """全市场快照：U（过滤后全集）、名称、收盘价、涨跌幅。仅支持当日。"""
        rows = self.client.smart_pick_stocks(
            "非ST、收盘价大于5元、今日成交额大于0的A股，收盘价，涨跌幅")
        u = {}
        for r in rows:
            if r["stock_code"] and r["change_ratio"] is not None:
                u[r["stock_code"]] = {
                    "name": r["stock_name"], "chg": r["change_ratio"],
                    "close": None,  # smart_pick 列名浮动，价格用于过滤已含在条件里
                }
        return u

    # ---- 主入口 ----
    def run(self, day: str, pool_codes, market=None, log=print):
        """执行归因。market=None 时仅支持当日（smart_pick）；历史日由 runner 传入
        {code: {name, chg}}（来自日K横截面）。pool_codes 已是候选池（引擎内再剔除）。"""
        day = day.replace("-", "")
        codes = sorted(set(pool_codes))

        # 1) 分时拉取 + 个股特征（剔除停牌：无分时即剔除）
        series = self.fetcher.fetch_batch(codes, date=None if is_today(day) else day)
        feats = {}
        excluded = {"suspended": 0, "low_price": 0, "st": 0}
        name_of = {}
        if market:
            name_of = {c: v.get("name", "") for c, v in market.items()}
        for c in codes:
            s = series.get(c)
            if not s or not (s.get("trading") or []):
                excluded["suspended"] += 1
                continue
            f = stock_features_from_trend(c, s, name=name_of.get(c, ""))
            if f is None:
                excluded["suspended"] += 1
                continue
            if f["close"] < PARAMS["min_price"]:
                excluded["low_price"] += 1
                continue
            if "ST" in name_of.get(c, "").upper():
                excluded["st"] += 1
                continue
            feats[c] = f
        S = set(feats)
        log(f"[POOL] 输入 {len(codes)} → 有效池 {len(S)}（剔除 {excluded}）")
        if len(S) < 10:
            return {"error": "有效股票池不足 10 只", "pool_size": len(S)}

        # 2) U / 横截面（题材全成分涨跌停结构用）
        if market is None:
            market = self.market_snapshot(day)
        U = set(market)
        base_rate = len(S) / max(len(U), 1)

        # 3) 概念映射 + 候选生成
        mapping = get_mapping(day, sorted(S), self.client)
        hit = defaultdict(set)          # theme -> S∩theme
        for sc in S:
            for cc, _cn in mapping.get(sc, []):
                hit[cc].add(sc)
        members, sizes, theme_names, snap_date = theme_members_and_size(day)
        cands = {}
        for cc, hs in hit.items():
            if len(hs) < PARAMS["min_hit_count"]:
                continue
            size = sizes.get(cc)
            if size is not None and (size < PARAMS["min_theme_members"] or size > 1500):
                continue
            cands[cc] = {"hit": hs, "size": size or len(members.get(cc, hs)),
                         "name": theme_names.get(cc, "")}
        log(f"[CAND] 候选题材 {len(cands)} 个（成分快照时点 {snap_date}）")

        # 4) 因子计算
        total_amount = sum(f["amount"] for f in feats.values()) or 1.0
        pos_ret_w = sum(max(f["ret"], 0) * f["amount"] for f in feats.values()) or 1.0
        z20 = concept_index_z20(day, list(cands))
        raw = {}
        for cc, c in cands.items():
            hs, size = c["hit"], c["size"]
            cov = len(hs) / len(S)
            purity = len(hs) / max(size, 1)
            lift = purity / base_rate if base_rate > 0 else 0
            contrib = sum(max(feats[s]["ret"], 0) * feats[s]["amount"]
                          for s in hs) / pos_ret_w
            # 同步性：触发率 + 5 分钟窗口集中度
            trigs = [feats[s]["trigger_min"] for s in hs if feats[s]["trigger_min"]]
            trig_rate = len(trigs) / len(hs)
            conc = max_window_concentration(trigs, 5)
            sync = 0.5 * trig_rate + 0.5 * conc
            # 涨跌停结构（双口径）
            lim_pool = limit_structure(hs, feats, pool_scope=True)
            lim_theme = limit_structure_cross(cc, members.get(cc, hs), market)
            # 龙头结构（池内）
            heights = [(board_height(day, s, feats[s]["name"], feats[s]["ret"]),
                        feats[s]["amount"], s) for s in hs]
            max_h = max(h for h, _, _ in heights)
            ladder = ladder_of(heights)
            lead_amount = max(a for _, a, _ in heights) / total_amount
            liq = sum(feats[s]["amount"] for s in hs) / total_amount
            raw[cc] = {
                "name": c["name"], "hit": len(hs), "size": size,
                "coverage": cov, "purity": purity, "lift": lift,
                "contribution": contrib, "z20": z20.get(cc),
                "sync": sync, "trig_rate": trig_rate, "conc5": conc,
                "lim_pool": lim_pool, "lim_theme": lim_theme,
                "max_height": max_h, "ladder": ladder, "lead_amount": lead_amount,
                "liquidity": liq,
                "hit_codes": sorted(hs),
            }

        # 5) 标准化 0~100 + 加权
        scores = standardize_and_score(raw)
        for cc in raw:
            raw[cc]["score"] = scores[cc]
        # 全候选因子矩阵（验收独立复算用，REQ-21/NFR-02）
        # 不做舍入：验收需用原值精确复算百分位（舍入会在近并列处改变排序）
        factor_matrix = {
            cc: {"coverage": v["coverage"],
                 "lift": v["lift"],
                 "contribution": v["contribution"],
                 "z20": v["z20"],
                 "sync": v["sync"],
                 "lim_val": v["_lim_val"],
                 "lead_val": v["_lead_val"],
                 "liq_val": v["_liq_val"],
                 "neg_flag": v["_neg_flag"],
                 "score": scores[cc]}
            for cc, v in raw.items()}

        # 6) Jaccard 压缩 + 主线提取
        clusters = overlap_clusters(raw)
        reps = pick_representatives(raw, clusters)
        primary, explain_ratio, expansion = set_cover(raw, reps, feats)

        # 7) 组织输出
        ranked = sorted(raw.items(), key=lambda kv: -kv[1]["score"])
        result = {
            "trade_date": day, "pool_size": len(S), "universe": len(U),
            "excluded": excluded,
            "excluded_codes": sorted(set(codes) - S),
            "base_rate": round(base_rate, 5),
            "member_snapshot_date": snap_date,
            "weights_version": "v1.1-20260917",
            "weights": WEIGHTS, "factor_matrix": factor_matrix,
            "primary": [
                {"theme_code": cc, **{k: v for k, v in raw[cc].items()
                                      if k != "cluster_members"},
                 "role": "核心催化"} for cc in primary],
            "explain_ratio": round(explain_ratio, 4),
            "has_single_theme": explain_ratio >= PARAMS["target_explain"],
            "candidates": [
                {"theme_code": cc, "name": v["name"], "score": v["score"],
                 "hit": v["hit"], "lift": round(v["lift"], 2),
                 "role": "主线" if cc in primary else
                         ("扩散" if cc in expansion else "候选")}
                for cc, v in ranked[:10]],
            "detail": {cc: {k: (round(x, 4) if isinstance(x, float) else x)
                            for k, x in v.items() if k != "hit_codes"}
                       for cc, v in ranked[:10]},
        }
        persist_run(result)
        return result


def is_today(day: str) -> bool:
    return day == datetime.now().strftime("%Y%m%d")


def max_window_concentration(trig_minutes, window=5):
    """触发时间的最大 window 分钟集中度（无触发返回 0）。"""
    if not trig_minutes:
        return 0.0
    mins = sorted(int(t[:2]) * 60 + int(t[3:5]) for t in trig_minutes)
    best = 1
    j = 0
    for i, mv in enumerate(mins):
        while mins[j] < mv - window:
            j += 1
        best = max(best, i - j + 1)
    return best / len(mins)


def limit_structure(hs, feats, pool_scope):
    """池内口径涨跌停结构（精确价格判定）。"""
    n = len(hs)
    up = sum(1 for s in hs if feats[s]["limit_state"] == "seal_up")
    dn = sum(1 for s in hs if feats[s]["limit_state"] == "seal_down")
    touch = sum(1 for s in hs if feats[s]["touched_up"])
    break_ = sum(1 for s in hs if feats[s]["limit_state"] == "break_up")
    reseal = sum(1 for s in hs if feats[s]["reseal"])
    return {
        "up": up, "up_rate": up / n, "down": dn, "down_rate": dn / n,
        "touch": touch, "seal_rate": (up / touch) if touch else None,
        "break_rate": (break_ / touch) if touch else None,
        "reseal": reseal,
        "balance": (up - dn) / (up + dn) if (up + dn) else None,
    }


def limit_structure_cross(cc, theme_stocks, market):
    """题材全成分口径涨跌停结构（横截面涨跌幅阈值判定）。"""
    stats_ = {"n": 0, "up": 0, "down": 0}
    for sc in theme_stocks:
        info = market.get(sc)
        if not info:
            continue
        stats_["n"] += 1
        st = limit_state_from_chg(sc, info.get("name", ""), info.get("chg"))
        if st == "up":
            stats_["up"] += 1
        elif st == "down":
            stats_["down"] += 1
    n = stats_["n"] or 1
    up, dn = stats_["up"], stats_["down"]
    return {"n": stats_["n"], "up": up, "down": dn,
            "up_rate": up / n, "down_rate": dn / n,
            "balance": (up - dn) / (up + dn) if (up + dn) else None}


def ladder_of(heights):
    """连板梯队：首板/2板/3板+ 家数。heights=[(高度,额,码),...]"""
    d = defaultdict(int)
    for h, _, _ in heights:
        d[min(h, 3)] += 1
    return {"1": d[1], "2": d[2], "3+": d[3]}


def standardize_and_score(raw):
    """横截面 percentile → 0~100，加权合成（负向指标取反）。"""
    def pct_rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        for pos, i in enumerate(order):
            r[i] = pos / max(len(vals) - 1, 1)
        return r

    ccs = list(raw)
    sub = {}
    # 各分项的合成值（部分含负向子项）
    for cc in ccs:
        v = raw[cc]
        z = v["z20"] if v["z20"] is not None else 0.0
        ls_pool = v["lim_pool"]
        ls_theme = v["lim_theme"]
        # LimitStructure 综合值：涨停聚集 + 封板质量 − 跌停/炸板惩罚（双口径各半）
        seal = ls_pool["seal_rate"] if ls_pool["seal_rate"] is not None else 0.5
        pen = (ls_pool["down_rate"] + (ls_pool["break_rate"] or 0)) * 2
        lim_val = (math.tanh(ls_pool["up_rate"] * 3) * 60
                   + seal * 20 + math.tanh(ls_theme["up_rate"] * 5) * 20
                   - pen * 50)
        neg_flag = (ls_pool["down_rate"] > 0.05
                    or (ls_pool["break_rate"] or 0) > 0.5)
        v["_lim_val"], v["_neg_flag"] = lim_val, neg_flag
        # Leader：高度 + 梯队完整性 + 龙头成交占比
        lad = v["ladder"]
        v["_lead_val"] = (v["max_height"] * 2
                          + (1 if lad["2"] else 0) + (1.5 if lad["3+"] else 0)
                          + v["lead_amount"] * 50)
        v["_abn_val"] = z  # 异常强度暂以指数 Z20 为主成分
        v["_liq_val"] = math.log1p(v["liquidity"] * 1000)
        sub[cc] = v

    cols = {
        "coverage": [sub[c]["coverage"] for c in ccs],
        "lift": [sub[c]["lift"] for c in ccs],
        "contribution": [sub[c]["contribution"] for c in ccs],
        "abnormal": [sub[c]["_abn_val"] for c in ccs],
        "sync": [sub[c]["sync"] for c in ccs],
        "limit": [sub[c]["_lim_val"] for c in ccs],
        "leader": [sub[c]["_lead_val"] for c in ccs],
        "liquidity": [sub[c]["_liq_val"] for c in ccs],
    }
    ranks = {k: pct_rank(v) for k, v in cols.items()}
    scores = {}
    for i, cc in enumerate(ccs):
        s = sum(WEIGHTS[k] * ranks[k][i] * 100 for k in WEIGHTS)
        if sub[cc]["_neg_flag"]:
            s *= 0.85  # 高分歧/退潮风险降权（REQ 负向处理）
        scores[cc] = round(s, 2)
    return scores


def overlap_clusters(raw):
    """Jaccard(Hc1,Hc2) ≥ 阈值 → 并查集聚簇。"""
    ccs = list(raw)
    parent = {c: c for c in ccs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(ccs):
        for b in ccs[i + 1:]:
            ha, hb = set(raw[a]["hit_codes"]), set(raw[b]["hit_codes"])
            if not ha or not hb:
                continue
            j = len(ha & hb) / len(ha | hb)
            if j >= PARAMS["overlap_jaccard"]:
                parent[find(a)] = find(b)
    clusters = defaultdict(list)
    for c in ccs:
        clusters[find(c)].append(c)
    return list(clusters.values())


def pick_representatives(raw, clusters):
    """簇代表：CatalystScore 高者优先，平分时取 Lift 高/规模小。"""
    reps = []
    for cl in clusters:
        rep = max(cl, key=lambda c: (raw[c]["score"], raw[c]["lift"],
                                     -raw[c]["size"]))
        raw[rep]["cluster_members"] = cl
        reps.append(rep)
    return reps


def set_cover(raw, reps, feats):
    """加权集合覆盖：贪心选 ≤max_primary 个主线，权重=成交额×正收益贡献。"""
    w = {s: max(feats[s]["ret"], 0) * feats[s]["amount"] for s in feats}
    total = sum(w.values()) or 1.0
    covered = set()
    chosen = []
    explain = 0.0
    for _ in range(PARAMS["max_primary"]):
        best, best_gain = None, 0.0
        for cc in reps:
            if cc in chosen:
                continue
            gain = sum(w[s] for s in raw[cc]["hit_codes"] if s not in covered) / total
            if gain > best_gain:
                best, best_gain = cc, gain
        if not best or best_gain < 0.05:  # 增量过小不再扩线
            break
        chosen.append(best)
        covered |= set(raw[best]["hit_codes"])
        explain = sum(w[s] for s in covered) / total
    # 扩散题材：簇内非代表、且对已覆盖集有新增解释
    expansion = set()
    for cc in raw:
        if cc in chosen:
            continue
        inc = len(set(raw[cc]["hit_codes"]) - covered)
        if inc >= 2 and raw[cc].get("score", 0) >= 30:
            expansion.add(cc)
    return chosen, explain, expansion


def persist_run(result):
    """证据链落库（REQ-21）。"""
    con = sqlite3.connect(TC_DB)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS tc_run ("
        " run_id TEXT, created_at TEXT, trade_date TEXT, pool_rule TEXT,"
        " pool_size INTEGER, result_json TEXT);")
    con.execute(
        "INSERT INTO tc_run VALUES (?,?,?,?,?,?)",
        (f"{result['trade_date']}-{datetime.now().strftime('%H%M%S')}",
         datetime.now().isoformat(), result["trade_date"], "pool",
         result["pool_size"], json.dumps(result, ensure_ascii=False)))
    con.commit()
    con.close()


# ==================== 管线封装（CLI 与 API 共用） ====================

def run_pipeline(day: str, pool_spec: str, engine=None, client=None):
    """解析池 → 历史日补横截面 → 执行归因。CLI（scripts/theme_catalyst_run.py）
    与 API（/api/theme/attribution）共用入口。"""
    day = (day or datetime.now().strftime("%Y%m%d")).replace("-", "")
    if client is None:
        import ifind_client
        client = ifind_client.IFindClient()
    pool, rule = resolve_pool(pool_spec, day, client)
    market = None
    if not is_today(day):
        market = build_market_historical(day, client, extra_codes=pool)
    eng = engine or ThemeCatalystEngine(client=client)
    result = eng.run(day, pool, market=market)
    result["pool_rule"] = rule
    return result


def resolve_pool(spec, day, client):
    if spec == "style_indices":
        if is_today(day):
            codes = set()
            for ic in STYLE_INDICES:
                r = client.get_concept_members(ic, iso_day(day))
                tbl = (r.get("tables") or [{}])[0].get("table", {})
                codes.update(tbl.get("p03473_f002", []))
            return sorted(codes), "三指数成分(当日)"
        return get_style_members(day), "三指数成分(回测库)"
    if spec.startswith("smart:"):
        rows = client.smart_pick_stocks(spec[5:])
        return sorted({r["stock_code"] for r in rows}), f"smart_pick({spec[5:]})"
    if spec.startswith("codes:"):
        return [c.strip() for c in spec[6:].split(",") if c.strip()], "外部代码"
    if spec.startswith("file:"):
        txt = Path(spec[5:]).read_text()
        return [c.strip() for c in txt.replace("\n", ",").split(",") if c.strip()], "代码文件"
    raise ValueError(f"无法识别的池定义: {spec}")


def build_market_historical(day, client, extra_codes=()):
    """历史日全市场横截面：概念成分全集 + 池代码，日K涨跌幅（缓存 tc_market）。"""
    con = sqlite3.connect(TC_DB)
    con.execute("CREATE TABLE IF NOT EXISTS tc_market ("
                "trade_date TEXT, code TEXT, name TEXT, chg REAL, "
                "PRIMARY KEY (trade_date, code))")
    cached = con.execute(
        "SELECT code, name, chg FROM tc_market WHERE trade_date=?", (day,)
    ).fetchall()
    if len(cached) > 3000:
        con.close()
        return {c: {"name": n, "chg": g} for c, n, g in cached}

    prod = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    codes = {r[0] for r in prod.execute(
        "SELECT DISTINCT stock_code FROM concept_members")}
    names = dict(prod.execute(
        "SELECT stock_code, MAX(stock_name) FROM concept_members GROUP BY stock_code"
    ).fetchall())
    prod.close()
    btc = sqlite3.connect(f"file:{BT_DB}?mode=ro", uri=True)
    codes |= {r[0] for r in btc.execute(
        "SELECT DISTINCT stock_code FROM bt_member")}
    btc.close()
    codes |= set(extra_codes)
    codes = sorted(codes)

    have = {}
    bt2 = sqlite3.connect(f"file:{BT_DB}?mode=ro", uri=True)
    for c, g in bt2.execute(
            "SELECT code, change_ratio FROM bt_kline WHERE trade_date=?", (day,)):
        if g is not None:
            have[c] = g
    bt2.close()
    need = [c for c in codes if c not in have]
    for i in range(0, len(need), 50):
        batch = need[i:i + 50]
        resp = client.get_history_quotation(
            batch, iso_day(day), iso_day(day), indicators="changeRatio")
        for item in resp.get("tables", []):
            c = item.get("thscode", "")
            chgs = item.get("table", {}).get("changeRatio", [])
            if chgs and chgs[0] is not None:
                have[c] = chgs[0]
    rows = [(day, c, names.get(c, ""), g) for c, g in have.items() if g is not None]
    con.executemany("INSERT OR REPLACE INTO tc_market VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()
    return {c: {"name": n, "chg": g} for _, c, n, g in rows}
