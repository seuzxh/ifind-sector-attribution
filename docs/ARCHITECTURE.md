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

### 实时链路（realtime_engine.py）

```
前端 15s 轮询 GET /api/realtime/dashboard
                ↓
        后端内存缓存（TTL 10s）命中？─→ 直接返回
                ↓ 未命中
    接口4 拉全市场 A 股 09:30~当前 的 1min K（批量50，约110次请求）
                ↓ 解析每只股票
    realtime_df（列：code, change_ratio, speed, body, open, close）
                ↓
    calc_all_sectors_strength(realtime_df, members_map)  ← 复用收盘计算
                ↓
    对 Top10 板块各调 stock_scorer.score_members() → 成分股四维排名
                ↓
    组装返回 top/bottom 板块 + 成分股 + 市场统计
```

### 与 daily 的区别

| 维度 | daily（盘后） | 实时（盘中） |
|---|---|---|
| 数据源 | 接口3 日K，入库 | 接口4 1min K，不入库 |
| 板块强度 | 多周期融合（1d/5d/20d） | 仅 1d 实时强度 |
| 成分股排名 | L1 归因（贡献占比） | 四维加权评分（涨幅/涨速/实体/涨停） |
| 响应速度 | 入库后秒级 | 首次约 2 分钟，缓存命中秒级 |

### 成分股四维评分（stock_scorer.py）

```
综合分 = 0.4×z(涨幅) + 0.2×z(涨速) + 0.2×z(实体涨幅) + 0.2×涨停分
```

| 维度 | 定义 | 数据来源 |
|---|---|---|
| 涨幅 | 相对昨收的累计涨跌幅 % | 最后一根 1min K 的 changeRatio |
| 涨速 | (当前 close − 3分钟前 close) / 3分钟前 close × 100 | 最近 4 根 1min K 的 close |
| 实体涨幅 | (close − open) / open × 100 | 最后一根 1min K 的 close/open |
| 涨停分 | 涨停=1.0，未涨停=0.0（二值，不标准化） | 按板块判定阈值 |

涨停判定阈值（用略低值规避四舍五入）：
- 沪深主板：≥ 9.8%
- 创业板（300xxx）/科创板（688xxx）：≥ 19.5%
- 北交所（.BJ）：≥ 29%

### 缓存策略

后端全局缓存单例（`_last_dashboard`），TTL 10 秒：
- 前端 15 秒轮询 → 后端多数请求命中缓存，避免打爆 iFinD
- 缓存只缓存成功结果，失败不缓存
- 引擎实例（含成分股映射）全局复用，避免每次重建

### 历史模式降级

历史看板（`/api/history/dashboard`）读已入库的收盘数据：
- 板块强度：读 `concept_strength` 表
- 成分股排名：读 `daily_kline` 当日涨幅，**降级为纯涨幅排序**（无 1min 历史，无法算涨速/实体）
- 页面标注"历史模式仅涨幅"

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
