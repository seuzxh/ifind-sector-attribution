# -*- coding: utf-8 -*-
"""
交易日历模块 - iFinD 板块监控系统

功能：
  1. 提供交易日列表 / 单日判断（is_trading_day / get_latest_trade_day / next_trade_day）
  2. 提供交易时段判断（session_phase：盘前/集合竞价/连续竞价/午休/收盘）

数据源：复用 kline-fetcher 的 KLineFetcher.fetch_trade_calendar
  —— 拉上证指数(000001)的日K，凡是有K线的日期即为交易日。

缓存策略（三级）：
  1. 进程级内存单例（TradeCalendar.instance()）
  2. 本地文件 data/trade_calendar.txt（每行一个 YYYYMMDD，启动时零网络读入）
  3. 网络全量拉取（仅首次或文件缺失时触发）

线程安全：懒加载用锁保护，避免并发首请求重复拉取。

日期格式约定：本模块对外统一用 YYYYMMDD（与项目 daily_kline.trade_date 一致）。
"""

import os
import threading
from datetime import datetime, timedelta
from typing import List, Optional


# ========== 交易时段规则（固定，A 股）==========
# 集合竞价 09:15-09:25；连续竞价 09:30-11:30 / 13:00-15:00
AUCTION_START = (9, 15)      # 集合竞价开始（此时刻起 pre_market 有数据）
AUCTION_END = (9, 25)        # 集合竞价结束（开盘价确定）
MORNING_START = (9, 30)
MORNING_END = (11, 30)
AFTERNOON_START = (13, 0)
AFTERNOON_END = (15, 0)

# 日历文件缓存
CALENDAR_FILE = os.path.join(os.path.dirname(__file__), "data", "trade_calendar.txt")
# 内存缓存有效期（天）：避免长期运行进程用过期日历（跨年后需补充新年交易日）
CACHE_TTL_DAYS = 1


def _now() -> datetime:
    """统一取当前时间（便于测试 mock）。"""
    return datetime.now()


def _to_compact(d: str) -> str:
    """'2024-01-05' → '20240105'；已是紧凑格式则原样返回。"""
    return d.replace("-", "") if d else d


def _to_hyphen(d: str) -> str:
    """'20240105' → '2024-01-05'。"""
    if not d or len(d) != 8 or "-" in d:
        return d
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"


class TradeCalendar:
    """
    交易日历单例。

    用法：
        cal = TradeCalendar.instance()
        cal.is_trading_day()                 # 今天是否交易日
        cal.session_phase()                  # 当前时段（pre_open/auction/morning/lunch/afternoon/closed）
        cal.get_trade_days(year=2026)        # 某年所有交易日
        cal.get_latest_trade_day()           # 最近一个交易日（含今天）
        cal.next_trade_day()                 # 下一个交易日（不含今天）
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        self._days: List[str] = []          # YYYYMMDD 排序列表
        self._day_set = set()               # YYYYMMDD 集合（O(1) 查询）
        self._loaded = False
        self._loaded_at: Optional[datetime] = None  # 加载时刻（TTL 用）

    # ---------- 单例 ----------
    @classmethod
    def instance(cls) -> "TradeCalendar":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ---------- 加载 ----------
    def _load_from_file(self) -> bool:
        """从本地文件读入交易日列表。返回是否成功。"""
        if not os.path.exists(CALENDAR_FILE):
            return False
        try:
            with open(CALENDAR_FILE, "r", encoding="utf-8") as f:
                days = sorted({
                    _to_compact(line.strip())
                    for line in f
                    if line.strip()
                })
            if not days:
                return False
            self._days = days
            self._day_set = set(days)
            return True
        except Exception as e:
            print(f"[CALENDAR] 读取本地日历失败: {e}")
            return False

    def _save_to_file(self) -> None:
        """持久化到本地文件。"""
        try:
            os.makedirs(os.path.dirname(CALENDAR_FILE), exist_ok=True)
            with open(CALENDAR_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(self._days) + "\n")
        except Exception as e:
            print(f"[CALENDAR] 写入本地日历失败: {e}")

    def _fetch_from_network(self) -> bool:
        """
        从 kline-fetcher 全量拉取近 3 年交易日。
        拉上证指数(000001)日K，有K线的日期即为交易日。
        """
        try:
            from kline_fetcher import TrendFetcher  # 与 intraday_fetcher 同源
        except ImportError as e:
            print(f"[CALENDAR] 无法导入 kline_fetcher: {e}")
            return False

        try:
            fetcher = TrendFetcher()
        except Exception as e:
            print(f"[CALENDAR] TrendFetcher 实例化失败（检查 KLINE_API_BASE_URL）: {e}")
            return False

        end_year = _now().year
        start_year = end_year - 2  # 近 3 年足够覆盖历史回看与当年判断
        try:
            raw = fetcher.fetch_trade_calendar(start_year=start_year, end_year=end_year)
        except Exception as e:
            print(f"[CALENDAR] 网络拉取交易日历失败: {e}")
            return False

        if not raw:
            print("[CALENDAR] 网络拉取返回空")
            return False

        # kline-fetcher 返回的 date 格式为 YYYY-MM-DD，统一转 YYYYMMDD
        days = sorted({_to_compact(d) for d in raw if d})
        if not days:
            return False

        self._days = days
        self._day_set = set(days)
        self._save_to_file()
        print(f"[CALENDAR] 网络拉取成功：{len(days)} 个交易日（{days[0]}~{days[-1]}），已写入 {CALENDAR_FILE}")
        return True

    def _ensure_loaded(self, force_network: bool = False) -> None:
        """
        懒加载：先读本地文件，再按 TTL 决定是否网络刷新。
        线程安全：拉取期间持锁，避免并发重复请求。
        """
        with self._lock:
            now = _now()
            # 已加载且未过期 → 直接返回
            if (self._loaded
                    and self._loaded_at
                    and (now - self._loaded_at) < timedelta(days=CACHE_TTL_DAYS)
                    and not force_network):
                return

            # 1) 先尝试本地文件（零网络）
            if force_network or not self._loaded:
                file_ok = self._load_from_file()
                # 2) 文件缺失或强制刷新 → 网络
                if force_network or not file_ok:
                    net_ok = self._fetch_from_network()
                    # 3) 网络也失败 → 用 daily_kline 表兜底（生产环境必有数据）
                    if not net_ok or not self._days:
                        self._load_from_db_fallback()
            self._loaded = True
            self._loaded_at = now

    def _load_from_db_fallback(self) -> None:
        """
        网络与文件都不可用时，从 daily_kline 表反推交易日。
        只能覆盖历史已同步日，无法预知未来节假日，仅作安全网。
        """
        try:
            from database import Database
            db = Database()
            with db._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT trade_date FROM daily_kline ORDER BY trade_date"
                ).fetchall()
            days = sorted({r[0] for r in rows if r[0]})
            if days:
                self._days = days
                self._day_set = set(days)
                print(f"[CALENDAR] DB 兜底加载：{len(days)} 个交易日（{days[0]}~{days[-1]}）")
        except Exception as e:
            print(f"[CALENDAR] DB 兜底失败: {e}")

    def force_refresh(self) -> bool:
        """强制网络刷新（手动调用，如节假日调整后）。"""
        self._ensure_loaded(force_network=True)
        return len(self._days) > 0

    # ---------- 查询 API ----------
    def get_trade_days(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        year: Optional[int] = None,
    ) -> List[str]:
        """
        获取交易日列表（YYYYMMDD）。
        :param start/end: YYYYMMDD，闭区间（可只给一个）
        :param year: 指定年份，等价于 start=YYYY0101, end=YYYY1231
        """
        self._ensure_loaded()
        if year:
            start = f"{year}0101"
            end = f"{year}1231"
        days = self._days
        if start:
            days = [d for d in days if d >= start]
        if end:
            days = [d for d in days if d <= end]
        return days

    def is_trading_day(self, date: Optional[str] = None) -> bool:
        """
        date 为 YYYYMMDD，默认今天。
        判断优先级：
          1. 日期在已加载范围内 → 查集合（精确，含节假日）
          2. 日期超出范围（未来）→ 强制网络刷新一次再查
          3. 网络不可用、仍超出范围 → 工作日规则兜底（周一~周五视为交易日）
             （节假日由实时接口无数据兜底，此处只做粗筛避免误判未来工作日为非交易日）
        """
        self._ensure_loaded()
        if date is None:
            date = _now().strftime("%Y%m%d")
        date = _to_compact(date)
        if date in self._day_set:
            return True
        # 不在集合中：若在已加载范围内，说明是周末/节假日（精确判断为非交易日）
        if self._days and self._days[0] <= date <= self._days[-1]:
            return False
        # 超出范围（未来日期）：尝试网络刷新补充
        if date > (self._days[-1] if self._days else ""):
            self._ensure_loaded(force_network=True)
            if date in self._day_set:
                return True
        # 仍无法确定 → 工作日规则兜底（避免误判未来工作日为非交易日）
        try:
            dt = datetime.strptime(date, "%Y%m%d")
            return dt.weekday() < 5  # 周一~周五
        except ValueError:
            return False

    def get_latest_trade_day(self, on_or_before: Optional[str] = None) -> Optional[str]:
        """
        <= on_or_before 的最近交易日。默认今天。
        若今天非交易日（周末/节假日），回退到最近一个已确认的交易日。
        """
        self._ensure_loaded()
        if on_or_before is None:
            on_or_before = _now().strftime("%Y%m%d")
        on_or_before = _to_compact(on_or_before)
        for d in reversed(self._days):
            if d <= on_or_before:
                return d
        return None

    def next_trade_day(self, on_or_after: Optional[str] = None) -> Optional[str]:
        """>= on_or_after 的下一个交易日。默认今天。"""
        self._ensure_loaded()
        if on_or_after is None:
            on_or_after = _now().strftime("%Y%m%d")
        on_or_after = _to_compact(on_or_after)
        # 若查询日期接近已加载末尾，强制刷新一次（补充新交易日）
        if on_or_after >= (self._days[-1] if self._days else ""):
            self._ensure_loaded(force_network=True)
        for d in self._days:
            if d >= on_or_after:
                return d
        # 日历不含该日期及之后的交易日（通常为未来日期）：
        # 用工作日规则从 on_or_after 当天起推断（>= 含等号），避免返回 None。
        # 节假日由实时接口无数据兜底，此处只做粗筛。
        try:
            dt = datetime.strptime(on_or_after, "%Y%m%d")
            # 从 on_or_after 当天起最多往后扫 15 天找第一个工作日
            for i in range(0, 15):
                nxt = dt + timedelta(days=i)
                if nxt.weekday() < 5:  # 周一~周五
                    return nxt.strftime("%Y%m%d")
        except ValueError:
            pass
        return None

    # ---------- 交易时段判断 ----------
    def session_phase(self, now: Optional[datetime] = None) -> str:
        """
        返回当前交易时段：
          'pre_open'   盘前（< 09:15，无任何数据）
          'auction'    集合竞价（09:15~09:25，pre_market 有数据但 trading 空）
          'pre_morning' 开盘前空窗（09:25~09:30）
          'morning'    上午连续竞价（09:30~11:30）
          'lunch'      午休（11:30~13:00）
          'afternoon'  下午连续竞价（13:00~15:00）
          'closed'     收盘后（>= 15:00）
        非交易日统一返回 'closed'。
        """
        now = now or _now()
        if not self.is_trading_day(now.strftime("%Y%m%d")):
            return "closed"

        hhmm = (now.hour, now.minute)
        if hhmm < AUCTION_START:
            return "pre_open"
        if hhmm < AUCTION_END:
            return "auction"
        if hhmm < MORNING_START:
            return "pre_morning"
        if hhmm < MORNING_END:
            return "morning"
        if hhmm < AFTERNOON_START:
            return "lunch"
        if hhmm < AFTERNOON_END:
            return "afternoon"
        return "closed"

    def next_open_time(self, now: Optional[datetime] = None) -> Optional[str]:
        """
        下一个"有数据时刻"，返回 HH:MM 字符串。
          盘前(pre_open) → 当日 '09:15'
          收盘后/非交易日(closed) → 下一交易日 '09:15'（具体日期由 next_trade_day 给出）
          交易中（auction/pre_morning/morning/lunch/afternoon）→ None（当前或即将有数据）
        """
        now = now or _now()
        phase = self.session_phase(now)
        if phase == "pre_open":
            return "09:15"
        if phase in ("auction", "pre_morning", "morning", "lunch", "afternoon"):
            return None
        # closed：收盘后或非交易日 → 下一交易日的 09:15
        return "09:15"

    def is_in_session(self, now: Optional[datetime] = None) -> bool:
        """当前是否在"可能有实时数据"的时段（集合竞价起 ~ 收盘）。"""
        return self.session_phase(now) in ("auction", "pre_morning", "morning", "lunch", "afternoon")


# ---------- 模块级便捷函数 ----------
def is_trading_day(date: Optional[str] = None) -> bool:
    return TradeCalendar.instance().is_trading_day(date)


def session_phase(now: Optional[datetime] = None) -> str:
    return TradeCalendar.instance().session_phase(now)


def get_latest_trade_day(on_or_before: Optional[str] = None) -> Optional[str]:
    return TradeCalendar.instance().get_latest_trade_day(on_or_before)
