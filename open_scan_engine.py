# -*- coding: utf-8 -*-
"""开盘强势扫描：四维快照计算（纯函数，三场景共用）。

场景：
  1. 历史回填（scripts/backfill_open_scan_history.py）
  2. 实时 9:30~9:40 逐 30s 刷新（P1 接入）
  3. 历史回看（P1 历史模式）

四维（9:30~截点窗口，截点默认 09:40）：
  auc_chg   竞价涨幅 = (开盘价 − 昨收)/昨收，开盘价 = pre_market 末点 ref_price
  speed     涨速 = 截点价相对前一分钟的涨幅（1min 滚动，口径同 stock_scorer）
  body      实体涨幅 = (截点价 − 开盘价)/开盘价
  vol_ratio 量能比 = 今日 9:30~截点累计量 ÷ 前 5 个交易日同期累计量均值（基线外部传入）
"""

WINDOW_END_DEFAULT = "09:40"


def window_slice(series: dict, upto: str = WINDOW_END_DEFAULT):
    """取截点前的盘中序列与截点价。

    :param series: IntradayFetcher 归一化的单股分时 dict（trading/pre_market）
    :param upto: 截点 HH:MM
    :return: (points, last_price) 或 (None, None)（无有效点）
    """
    trading = series.get("trading") or []
    pts = [p for p in trading if p["time"] <= upto]
    if not pts or pts[-1]["last_price"] <= 0:
        return None, None
    return pts, pts[-1]["last_price"]


def cum_volume(series: dict, upto: str = WINDOW_END_DEFAULT) -> float:
    """9:30~截点累计成交量（股）。"""
    pts, _ = window_slice(series, upto)
    return sum(p["volume"] for p in pts) if pts else 0.0


def compute_dims(series: dict, vol_baseline: float = None,
                 upto: str = WINDOW_END_DEFAULT,
                 pre_close_ext: float = None) -> dict:
    """计算四维快照（不含综合分；vol_baseline 为空时 vol_ratio=None）。

    :param pre_close_ext: 外部昨收（如日K前收）。历史分时的 pre_market 序列常退化
        （单点，ref=昨收），导致开盘价=昨收 → auc 恒 0（实测 97% 假平开）。
        传入时优先使用，开盘价亦退化为分时首分钟价（见 _open_price）。
    :return: {p940, auc_chg, speed, body, vol_ratio}；数据不足返回 {}
    """
    pts, last = window_slice(series, upto)
    if pts is None:
        return {}
    pre_close = pre_close_ext or series.get("pre_close")
    open_price = _open_price(series)
    if not pre_close or not open_price:
        return {}

    out = {"p940": last, "auc_chg": None, "speed": None, "body": None,
           "vol_ratio": None}
    if pre_close > 0:
        out["auc_chg"] = (open_price - pre_close) / pre_close * 100
    if len(pts) >= 2 and pts[-2]["last_price"] > 0:
        prev = pts[-2]["last_price"]
        out["speed"] = (last - prev) / prev * 100
    if open_price > 0:
        out["body"] = (last - open_price) / open_price * 100
    if vol_baseline is not None and vol_baseline > 0:
        cv = sum(p["volume"] for p in pts)
        out["vol_ratio"] = cv / vol_baseline
    return out


def _open_price(series: dict):
    """开盘价：pre_market 末点仅在其时间确为 09:25 时可信（真实竞价终点）；
    否则（历史序列退化）退化为分时首分钟价（09:30 首笔，含约 1 分钟漂移，口径注明近似）。"""
    pm = series.get("pre_market") or []
    if pm and pm[-1]["time"] == "09:25" and pm[-1]["ref_price"] > 0:
        return pm[-1]["ref_price"]
    tr = series.get("trading") or []
    return tr[0]["last_price"] if tr else None
