# 架构设计

本文档记录系统的关键设计决策，特别是数据流、概念编码体系、缓存语义与过滤策略。

## 目录

- [1. 双概念编码体系](#1-双概念编码体系)
- [2. 永久缓存语义](#2-永久缓存语义)
- [3. 多周期融合算法](#3-多周期融合算法)
- [4. A 股过滤策略](#4-a-股过滤策略)
- [5. L1 权重归因法](#5-l1-权重归因法)
- [6. 数据流总览](#6-数据流总览)
- [7. 盘中实时监控](#7-盘中实时监控)
- [8. 盘前筛选与 watchlist](#8-盘前筛选与-watchlist)
- [9. 自选股分组看板与持仓标注](#9-自选股分组看板与持仓标注)

---

## 1. 双概念编码体系

同花顺的概念体系包含**两套独立的编码**，本系统需要同时使用两套才能完成归因。

### 问题背景

| 体系 | 编码前缀 | 来源 | 示例 |
|---|---|---|---|
| **行业分类** | `700xxx / 881xxx / 883xxx / 884xxx` | `config.ALL_CONCEPT_CODES`（静态配置） | `700471.TI` 铜(A股)、`884055.TI` 铅锌 |
| **概念板块** | `885xxx / 886xxx` | 接口1（个股→概念）返回 | `885338.TI` 融资融券、`885376.TI` 苹果概念 |

**两套体系的交集为 0**。如果只用行业分类码，则接口1 返回的个股概念（`885xxx`）在字典里查不到，`get_stock_concepts` 的 `JOIN ths_concept_dict` 会过滤掉所有映射，导致归因恒为空。

### 解决方案：`init_concept_universe`

首次部署时，在行业字典初始化后追加一步：

```
扫描全市场股票（接口1）
    ↓ 收集所有出现过的概念码（885xxx/886xxx）
    ↓ 按前缀过滤掉海外概念
补全字典（接口5）+ 成分股（接口2）+ 全市场个股映射
```

完成后字典同时包含两套编码，归因的 JOIN 打通。实测：归因覆盖股票数从 0 → 5500+。

### 海外行业指数（需排除）

同花顺还有**海外市场行业指数**，编码与含义：

| 前缀 | 市场 | 示例 |
|---|---|---|
| `861xxx / 864xxx / 865xxx` | 美股 | `861076.TI` 金融[US] |
| `871xxx / 875xxx` | 港股 | `871077.TI` 房地产管理和开发[HK] |

这些概念的成分股全是 `.O / .N / .HK` 等海外代码，会污染 A 股股票池。通过 `A_SHARE_CONCEPT_PREFIXES` 白名单（见第 4 节）排除。

---

## 2. 永久缓存语义

### 问题背景

`stock_concept_map` 和 `concept_members` 是"一次性永久缓存"（概念归属相对稳定），但原实现用每日变化的 `calc_date` 作为查询键，导致：

- init 用 `2026-06-14` 入库，daily 用 `2026-06-12` 查询 → 查不到
- 日期格式不统一（`stock_concept_map` 用 `2026-06-14` 带横杠，`concept_members` 用 `20260614` 无横杠）→ 键不匹配

### 解决方案：日期参数可选，不传取最新

`database.py` 的三个查询方法改为：

```python
def get_concept_members(self, concept_code: str, member_date: str = None):
    if member_date is None:
        # 取该概念最新一份快照
        WHERE member_date = (SELECT MAX(member_date) FROM ... WHERE concept_code = ?)
    else:
        WHERE member_date = ?   # 向后兼容：按日期查
```

- 计算链路（板块强度、归因）调用时**不传日期** → 永远取最新缓存，与 init 日期解耦
- API 层仍可按日期查（回溯历史快照）
- 同时根治了日期格式不统一问题

---

## 3. 多周期融合算法

### 设计目标

板块强度不仅看当日表现，还要融合中期趋势，避免单日噪音主导排名。

### 算法（`calc_multi_period_score`）

**三个周期分别计算板块强度，再按权重融合：**

| 周期 | 收益定义 | 数据来源 |
|---|---|---|
| 1d | 当日涨幅 `change_ratio` | `get_daily_kline_by_date(calc_date)` |
| 5d | 近 5 个交易日**累计涨幅** | `get_daily_kline_by_date_range(start, calc_date)` |
| 20d | 近 20 个交易日累计涨幅 | 同上，窗口更长 |

**累计涨幅** = `期末 close / 期初 preClose - 1`（每只股票算一个值，作为该周期的 `change_ratio` 喂给 `calc_all_sectors_strength`）。

**融合公式：**

```
score_final = 0.5 × score_1d + 0.3 × score_5d + 0.2 × score_20d
```

（权重由 `config.PERIOD_WEIGHTS` 控制）

### 退化处理

历史数据不足时（如刚部署、20d 窗口只有 5 天数据），对应周期 `score` 填 0，不阻断计算。系统会随数据积累自然增强多周期信号。

### 实测效果

铜板块在 2026-06-12 登顶 #1，但其前几日单日排名靠后（#147~#1049）。多周期融合准确捕捉到了它的中期走强趋势——这正是融合的价值。

---

## 4. A 股过滤策略

### 背景

成分股表里的海外代码（美股/港股）占比曾达 60%+，源于 `config.ALL_CONCEPT_CODES` 混入了海外行业指数概念。

### 四层过滤（互补）

| 层 | 位置 | 作用 |
|---|---|---|
| **源头** | `init_concept_dict` | 按 `is_a_share_concept` 跳过海外概念，不拉字典 |
| **补充** | `init_concept_universe` | 扫描收集时过滤海外概念，不入 `stock_concept_map` |
| **数据源** | `get_all_member_stock_codes` / `get_all_mapped_stock_codes` | 只返回 `.SH/.SZ/.BJ` 后缀的代码 |
| **计算** | `calc_daily_strength` / `calc_daily_attribution` | 用 `get_a_share_concept_codes` 仅处理 A 股概念 |

### 判定函数（`config.py`）

```python
A_SHARE_SUFFIXES = (".SH", ".SZ", ".BJ")
A_SHARE_CONCEPT_PREFIXES = ("700", "881", "883", "884", "885", "886")

def is_a_share_code(code): ...        # 个股代码后缀判定
def is_a_share_concept(concept_code): # 概念代码前缀判定
```

### 清理工具

已入库的海外数据可用 `python main.py purge` 删除（`database.purge_overseas_data`，单事务，幂等）。

---

## 5. L1 权重归因法

### 算法（`l1_stock_attribution`）

对每只股票，将其总涨幅分解到所属的各个概念：

```
对每个概念 c:
    contribution_c = weight_c × concept_return_c

其中:
    weight_c = 1 / 该股票所属概念数（等权）
    concept_return_c = 概念 c 成分股当日涨幅均值（不含该股票自身）
```

**贡献占比** = `|contribution_c| / Σ|contribution_i|`，主导概念 = 贡献占比最大者。

### concept_return 的计算

关键设计：**概念的当日收益 = 其成分股当日涨幅的均值**，复用已入库的全市场股票 K 线，而非查概念指数本身的 K 线。

原因：daily 同步的代码列表是股票（不含概念指数），查概念指数 K 线会拿不到数据。用成分股均值既复用现有数据，又与板块强度的 S1 计算口径一致。

### nan 防御

停牌股的 `change_ratio` 为 nan，会传染均值计算。三层防御：
1. `concept_returns` 计算时过滤 nan 成分股
2. `calc_daily_attribution` 跳过 nan 涨幅的股票
3. `l1_stock_attribution` 对 nan 的 `concept_return` 视为 0

---

## 6. 数据流总览

```
┌─────────────────────────────────────────────────────────┐
│  init（首次部署）                                         │
│                                                         │
│  接口5 ─→ ths_concept_dict（行业码，过滤海外）            │
│  接口1 ─→ stock_concept_map（样本股票映射）              │
│  接口2 ─→ concept_members（行业码成分股，8线程并发）      │
│  接口1+5+2 ─→ 补全 885xxx 概念板块全集 + 全市场映射      │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  daily（每日盘后）                                       │
│                                                         │
│  接口3 ─→ daily_kline（全市场 A 股当日 K 线）            │
│                                                         │
│  daily_kline + concept_members                          │
│      ─→ calc_multi_period_score（1d/5d/20d 融合）       │
│      ─→ concept_strength（板块强度，含 score_final）     │
│                                                         │
│  daily_kline + stock_concept_map + concept_members      │
│      ─→ l1_stock_attribution（L1 权重归因）             │
│      ─→ stock_attribution（个股归因明细）                │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  api_server（查询服务）                                  │
│                                                         │
│  GET /                       ─ 可视化看板页面            │
│  GET /api/sector/rankings    ─ 板块强度排名              │
│  POST /api/attribution/stock     ─ 个股归因              │
│  POST /api/attribution/portfolio ─ 组合归因              │
│  GET /api/realtime/dashboard ─ 实时看板（盘中）          │
│  GET /api/history/dashboard  ─ 历史看板（收盘）          │
│  GET /api/concept/members    ─ 成分股（取最新缓存）       │
└─────────────────────────────────────────────────────────┘
```

---

## 7. 盘中实时监控

实时监控是一条**独立于 daily 的链路**，不写库、不走多周期融合，只在交易时段内存计算。
**数据源：kline-fetcher 的分时数据**（`TrendFetcher`，中焯行情 API），非 iFinD 1min K 线。

### 数据源：分时数据（intraday_fetcher.py）

每只股票返回完整分时序列（全天）：

| 阶段 | 字段 | 时间范围 | 粒度 | 点数 |
|---|---|---|---|---|
| `pre_market`（集合竞价） | `ref_price`（参考价）、`matched_vol` 等 | 09:15~09:25 | 每3秒 | ~201 |
| `trading`（盘中） | `last_price`、`avg_price`、`volume`、`turnover` | 09:30~15:00 | 每分钟 | 241 |

**关键：分时数据没有 OHLC、没有 changeRatio**，所有指标需用 `last_price` 自行推导：

| 锚点 | 来源 | 示例（茅台 6-12） |
|---|---|---|
| 昨收 | `pre_market[0].ref_price`（09:15 起始价 = 上一交易日收盘） | 1279.0 |
| 开盘价 | `pre_market[-1].ref_price`（09:25 集合竞价最终价） | 1271.18 |

### 实时链路（realtime_engine.py）

```
前端 3s 轮询 GET /api/realtime/dashboard?snapshot_time=09:50
                ↓
    检查分时序列缓存（按 trade_date+mode_key，TTL 5s）
        ├─ 命中 ─────────────────────→ 不拉网络，进入切片
        └─ 未命中（当日实时 TTL 过期 / 历史首次）
            ↓
        intraday_fetcher.fetch_batch()  ← 32 线程并发拉 watchlist 279 只（1.5s）
            ↓
        缓存完整分时序列 + 时间轴 available_times
            ↓
    snapshot_time 切片：trading[:snapshot_time]
            ↓
    按切片末点算每只股票指标（涨幅/body/涨速/加速）
            ↓
    calc_all_sectors_strength(rt_df, members_map)  ← 复用收盘计算
            ↓
    对 Top10 板块各调 stock_scorer.score_members() → 成分股四维排名
            ↓
    组装返回 top/bottom 板块 + 成分股 + 市场统计 + available_times
```

### 与 daily 的区别

| 维度 | daily（盘后） | 实时（盘中） |
|---|---|---|
| 数据源 | 接口3 日K，入库 | **kline-fetcher 分时数据**（中焯 API），不入库 |
| 板块强度 | 多周期融合（1d/5d/20d） | 仅 1d 实时强度（切片末点涨幅） |
| 成分股排名 | L1 归因（贡献占比） | 四维加权评分（涨幅/涨速/body/涨停） |
| 响应速度 | 入库后秒级 | watchlist 首次 ~1.5s，缓存命中 0.12s，切片毫秒级 |

### 成分股四维评分（stock_scorer.py）

```
综合分 = 0.4×z(涨幅) + 0.2×z(涨速) + 0.2×z(body) + 0.2×涨停分
```

| 维度 | 定义 | 数据来源 |
|---|---|---|
| 涨幅 | 相对昨收的涨跌幅 % | `(last_price - 昨收) / 昨收 × 100` |
| 涨速 | 1min 滚动末点：`(last[-1] - last[-2]) / last[-2] × 100` | 切片末两个 trading 点的 last_price |
| body | **开盘至今涨幅**：`(last - 开盘价) / 开盘价 × 100` | 切片末点 last_price vs 09:25 集合竞价价 |
| 涨停分 | 涨停=1.0，未涨停=0.0（二值，不标准化） | 按板块判定阈值 |

**附加展示指标（不进综合分）**：
- **涨速加速** `acceleration = speed[-1] - speed[-2]`：>0 加速上涨 / <0 减缓掉头。前端用 ▲/▼ 标识。

涨停判定阈值（用略低值规避四舍五入）：
- 沪深主板：≥ 9.8%
- 创业板（300xxx）/科创板（688xxx）：≥ 19.5%
- 北交所（.BJ）：≥ 29%

### 缓存与切片策略

**分时序列缓存**（`_series_cache`，按 `trade_date:mode_key` 键）：
- 当日实时：TTL 5s（watchlist 1.5s 拉取，3s 轮询大部分命中缓存）
- 历史日期：全天缓存（数据不变，首次拉取后永久有效，直至进程重启）
- 引擎实例（含成分股映射）全局复用

**snapshot_time 切片**（时间条回看）：
- 截取 `trading` 中 `time <= snapshot_time` 的前缀，用末点重算所有指标
- 纯内存操作（毫秒级），**不触发网络拉取**
- 应用场景：拖时间条观察任意时刻的板块排名，可看到板块轮动（如 9:50 半导体领涨、15:00 铜板块第一）
- `POST /api/realtime/clear_cache` 可手动清空（切日/调试用）

### watchlist 聚焦

实时模式默认 `watchlist_mode=True`（盘前筛出的 279 只股票）：
- 拉取量从全市场 5530 只降到 279 只，响应 30s → 1.5s
- 板块范围限定在 watchlist 的 20 个板块
- watchlist 为空时返回提示"请先盘前筛选"

### 历史模式降级

历史看板（`/api/history/dashboard`）读已入库的收盘数据：
- 板块强度：读 `concept_strength` 表
- 成分股排名：读 `daily_kline` 当日涨幅，**降级为纯涨幅排序**（无分时历史，无法算涨速/body）
- 页面标注"历史模式仅涨幅"

> 注意：实时模式选历史日期（如 `trade_date=20260612`）走的是**分时数据链路**（拉该日全天分时），与历史看板（读 concept_strength 入库数据）是两条不同路径。

### 集合竞价监控（09:15~09:25）

集合竞价阶段虽无连续竞价（`trading` 为空），但 `pre_market` 的 `ref_price`（撮合参考价）不断更新，可用于算涨跌幅，让监控从 9:15 就开始。

**两阶段计算逻辑**（`_build_indicator_df`，按 snapshot_time 严格判断归属）：

| snapshot_time 归属 | 判断条件 | 计算方式 |
|---|---|---|
| 盘中 | `trading` 有 `<= snapshot_time` 的点 | 用连续竞价切片算全部指标（涨幅/body/涨速/加速） |
| 集合竞价 | `trading` 切片为空，但 `pre_market` 有数据 | **仅用 pre_market 末点 ref_price 算涨幅**，`speed/body/acceleration` 置 0 |

**关键细节**：
- `trading` 切片**严格按 snapshot_time 过滤**，不兜底回退（否则 9:20 会误用 9:30 数据）
- pre_market 时间点（09:15~09:25 每分钟一个，共 11 个）纳入 `available_times`，**进度条自动从 09:15 开始**
- snapshot_time 回看：拖滑块到 9:20 即看当时撮合价的板块强弱
- 集合竞价末点 ref_price（开盘价）→ 盘中首点 last_price 平滑衔接

**实测**：6-15 9:20 集合竞价时，氟化工板块三美/巨化/中欣氟材均 +10% 涨停，正确反映开盘前抢筹信号。

### 交易日历与盘前处理

**交易日历模块**（`trade_calendar.py`，复用 kline-fetcher 的 `fetch_trade_calendar`）：

`TradeCalendar` 单例，三级缓存：进程内存 → `data/trade_calendar.txt`（本地文件）→ 网络（拉上证指数日K反推交易日）。网络不可用时用 `daily_kline` 表兜底；未来日期超出范围时用工作日规则粗筛。

`session_phase(now)` 区分七个时段：

| phase | 时段 | 含义 |
|---|---|---|
| `pre_open` | < 09:15 | 盘前，无任何数据 |
| `auction` | 09:15~09:25 | 集合竞价，pre_market 有 ref_price |
| `pre_morning` | 09:25~09:30 | 开盘前空窗 |
| `morning` | 09:30~11:30 | 上午连续竞价 |
| `lunch` | 11:30~13:00 | 午休 |
| `afternoon` | 13:00~15:00 | 下午连续竞价 |
| `closed` | ≥ 15:00 或非交易日 | 收盘后 |

**新增 API 端点**：
- `GET /api/trade_calendar?year=2026` — 返回交易日列表（日期选择器过滤非交易日用）
- `GET /api/session_status` — 返回 `{is_trading_day, phase, next_open_time, next_trade_day, now}`（前端盘前判断用）

**前端盘前处理**（基于 `/api/session_status`）：

| 时段 | 前端行为 |
|---|---|
| 非交易日 / `pre_open`(<9:15) / `closed`(收盘后) | **停止 3s 轮询**，显示友好提示（如"⏰ 盘前 · 09:15 后自动开始监控"），`scheduleResume` 每分钟检查 |
| `auction`(9:15-9:25) / 盘中 | 启动轮询（集合竞价阶段配合后端 ref_price 计算逻辑） |

设计要点：轮询启停完全由服务端 `session_phase` 决定（前端本地时间仅兜底），避免客户端时区偏差。集合竞价阶段数据可用后，前端无需特殊处理——后端返回的 `available_times` 含 9:15-9:25 点，进度条自然从 9:15 开始。

---

## 8. 盘前筛选与 watchlist

### 目标

盘前（如 9:15~9:25）从近 5 日强势标的中选出观察范围，开盘后实时监控聚焦这些标的，减少噪音和接口调用量。

### 筛选算法（prescreen.py）

**口径**：近 5 个交易日累计涨幅（`calc_period_return_df`，close_今日 / preClose_5天前 - 1）

**步骤1：板块 5 日涨幅 → 前 20**
```
对每只 A 股股票算 5d 累计涨幅（复用 calc_period_return_df）
    ↓
对每个 A 股概念，取成分股 5d 涨幅均值 = 板块 5d 涨幅%
    ↓
按板块 5d 涨幅降序，取前 20（过滤 member_count < 6 的迷你概念）
```

**步骤2：板块成分股 5 日涨幅 → 各前 30**
```
对选出的 20 板块，分别取其成分股 5d 涨幅降序前 30
```

注意：板块 5d 涨幅是成分股**均值**（复用 `calc_sector_strength` 的 s1 逻辑，跳过 Z-score），**不是** `concept_strength` 表里的 `score_5d`（那是横截面 Z-score 相对分，有正有负）。

### 新股过滤

筛选前剔除上市不足 `period_days`（默认 5）个交易日的新股，避免连板新股（如北交所上市首日 +300%）撑高板块涨幅均值。

判定方式（无需额外接口，从 `daily_kline` 反推）：
```
取 calc_date 及之前的交易日序列 [d1, d2, ..., dN]
cutoff = d(N - min_days + 1)   # 第6个交易日（含）
股票最早出现日期 > cutoff  →  视为新股，剔除
```
实测：6/12 筛选剔除 2 只新股（920206 首日 6/8、301669 首日 6/9），老股不受影响。

### watchlist 持久化

`watchlist` 表（PK: calc_date + concept_code + stock_code），每次 prescreen 用 `DELETE WHERE calc_date=?` + 批量插入覆盖当日。读取方法：
- `get_watchlist(date)` — 完整板块+成分股
- `get_watchlist_concepts(date)` — 板块代码列表
- `get_watchlist_stock_codes(date)` — 成分股去重列表

### 与实时监控的衔接

实时监控（第 7 章）的 `compute_dashboard` 支持 `watchlist_mode=True`：
1. 从 watchlist 表读当日 20 板块 + 去重成分股（约 290 只）
2. `fetch_realtime_data` 只拉这 290 只（接口4 调用从 110 次降到 6 次，**响应 2 分钟 → 30 秒**）
3. `calc_all_sectors_strength` 只对这 20 板块算强度

前端实时模式有"watchlist聚焦"开关，默认开启（盘前已筛选时）；watchlist 为空时返回提示。

### 复用的关键函数

`calc_period_return_df(db, calc_date, days)` —— 从 `calc_multi_period_score` 闭包提取的模块级函数，算任意天数的累计涨幅。prescreen 用它算 5d 涨幅，多周期融合用它算 5d/20d。

---

## 9. 自选股分组看板与持仓标注

### 双看板 Tab 隔离架构

可视化看板采用**顶层 Tab + iframe 隔离**，两套看板状态完全独立：

```
GET /  → tabs.html（顶层 Tab 容器）
         ├── [📊 板块强度监控] → iframe /?board=sector  → /api/realtime/dashboard
         └── [⭐ 自选分组监控] → iframe /?board=custom → /api/custom/dashboard
```

- 根路由 `/` 按 `?board` 参数分发：无参 → `tabs.html`（容器）；`?board=sector`/`=custom` → `index.html`（iframe 内页）
- `index.html` 据 `?board` 切换数据源（`BOARD_API`）与标题，其余逻辑（模式/时间条/播放/渲染）完全复用
- 两 iframe 各自独立 JS 环境，状态（模式/时间条/播放/autoFollow）互不影响，切 Tab 不丢状态

### 自选分组链路（复用 realtime_engine）

自选看板与板块看板的**唯一差异是 `members_map` 来源**：

| 维度 | 板块看板（sector） | 自选看板（custom） |
|---|---|---|
| members_map | `self._members_map`（概念板块成分股） | `db.get_custom_members_map()`（custom_group 表） |
| 分组名 | `self._concept_names`（概念字典） | `db.get_custom_group_names()`（导入的 block_name） |
| 缓存键 | `watchlist:{date}` / `market` | `custom_group`（独立） |
| 数据源 | 分时数据（kline-fetcher） | 同左（完全复用） |
| 计算 | `calc_all_sectors_strength` + 切片 | 同左（完全复用） |

`compute_dashboard(custom_mode=True)` 在 watchlist 分支之前注入自定义 `members_map`，其余流程（`_ensure_series` → `_build_indicator_df` → `calc_all_sectors_strength` → `_build_sector_entry`）原样复用。

### 数据导入（import-groups）

`main.py import-groups` 读同花顺 custom_block 导出的 JSON：

- 按 `market_code` 过滤：只保留 `{17:沪, 33:深, 151:北交}`，过滤指数(48/49)/ETF(20)/可转债(35)/B股(22)等
- 代码转 A 股格式：`002585` + `market_code=33` → `002585.SZ`
- 幂等：清表重导，分组更新后重跑即可
- 表结构：`custom_group(group_id, group_name, stock_code)`，PK(group_id, stock_code)

### 持仓醒目标注（CC 分组）

自选看板专属功能，识别持仓分组并对含持仓的分组金色标注：

**识别**：`config.HOLDING_GROUP_NAME = "CC"`，按 `block_name` 精确匹配，其成分股作为持仓股。

**后端透传**：
- `holding_stocks`：持仓股清单（顶层返回）
- `holding_in_group`：每个分组**完整成分股 ∩ 持仓股**的交集（基于全部成分股，非仅 top10）

**前端两层标注**（仅 `isCustomBoard` 生效）：
- 排行表行：含持仓的分组 → 金色渐变底 + 左金边 + "持仓N"徽章
- 成分股卡片：含持仓的分组 → 金色描边 + 光晕
- 成分股行：持仓个股 → 金色底 + "持仓"标签

> 注意：分组级标注基于 `holding_in_group`（分组全部成分股），不依赖该持仓股是否进了 top10 成分股排名——因为持仓股当日涨幅可能靠后，不在 top10 里，但其所在分组仍需标注。

### 时间条播放

`togglePlay` 用 `setInterval` 逐分钟推进滑块（`stepPlay` → `refresh`）：
- 速度档位 1.5x/2x/4x/8x（每步间隔 1500/800/400/150ms）
- 播放时自动 `autoFollow=false`（否则 3s 轮询拉回最新）
- 到末尾自动 `stopPlay`；切模式/切日期/拖滑块/点"回到最新"也自动停
- **请求序号守卫 `refreshSeq`**：每次 refresh 前 `++seq` 记 `mySeq`，响应回来若 `mySeq !== refreshSeq` 则丢弃——防止播放快进时多个并发请求乱序覆盖界面（时刻跳变）
