---
title: "页面数据与计算"
parent: "使用指南"
nav_order: 3
description: 7 个看板页面各自的数据来源、推导口径与计算公式
---

# 页面数据与计算

本文按页面梳理：每个 Tab 的数据从哪来（接口 / 表 / 缓存）、指标怎么算（公式与权重）、有哪些阈值。所有公式均与当前代码一致（`core_calculator.py` / `stock_scorer.py` / `realtime_engine.py` / `auction_engine.py` / `sector_manage.py`）。

## 总览：页面 × 数据源

| 页面 | 后端接口 | 主数据源 | 核心计算 |
|---|---|---|---|
| 📊 板块强度监控 | `GET /api/realtime/dashboard` | kline-fetcher 分时（中焯 API） | 板块三维强度 + 个股四维评分 |
| ⭐ 自选分组监控 | `GET /api/custom/dashboard` | 同上（分组换成 custom_group 表） | 同上 + 持仓标注 |
| ⚡ 集合竞价 | `GET /api/auction/dashboard` | 分时 pre_market 竞价序列 | 竞价四因子 + 分组聚合 |
| 🎯 自选强势归类 | `GET /api/custom/scan` | iFinD REST `smart_stock_picking` | 选股 ∩ 自选 → 分组统计 |
| 🌐 全市场强势归类 | `GET /api/market/scan` | 同上 + 知识图谱（kg_node/kg_edge） | 选股 → 图谱富集归类（lift） |
| 🔮 板块轮动分析 | `GET /api/rotation/analyze`（SSE） | kline-fetcher + DB + LLM | 四阶段智能体分析 |
| 🛠️ 监控板块管理 | `GET /api/sector_manage/list` | iFinD 实时行情 + 接口3 日K | 指数级指标直读 + 多日涨幅 |

## 📊 板块强度监控

**数据来源**：`kline-fetcher` 分时序列（中焯行情 API，地址配在 `KLINE_API_BASE_URL`）。拉取当前有效监控板块（勾选且成分股数 10~500）的**全部去重成分股**当日分时；历史日期拉该日全天后缓存。分时序列缓存 TTL 15 秒，看板结果缓存 TTL 10 秒，页面每 3 秒轮询。

**基础价推导**（分时数据无 OHLC，全部用 `last_price` / `ref_price` 推导）：

| 量 | 推导 |
|---|---|
| 昨收 `pre_close` | 分时记录自带 |
| 开盘价 | `pre_market` 末点 `ref_price`（9:25 集合竞价定盘价） |
| 涨幅 | `(last − pre_close) / pre_close × 100` |
| 开盘至今涨幅 body | `(last − 开盘价) / 开盘价 × 100` |
| 涨速 | `(last[-1] − last[-2]) / last[-2] × 100`（1 分钟滚动） |
| 涨速加速 | `speed[-1] − speed[-2]`（仅展示，不进综合分） |

时间条切片：截取 `trading` 中 `<= snapshot_time` 的点用末点重算，纯内存毫秒级；严格按时刻过滤不回退（防止 9:20 误用 9:30 数据）。

**板块强度**（三维，Z-score 标准化后加权，`calc_all_sectors_strength`）：

| 分量 | 定义 | 权重 |
|---|---|---|
| S1 涨幅强度 | 成分股涨幅均值 | 0.4 |
| S2 上涨广度 | 上涨家数 / 命中成员数 | 0.3 |
| S4 相对强度 | S1 − 全池平均涨幅 | 0.3 |

- `score = 0.4·z(S1) + 0.3·z(S2) + 0.3·z(S4)`，标准化在同批板块内进行，跨日不可直接比较。
- 本次行情命中成分股 `< 6`（`MIN_MEMBER_COUNT`）的板块被过滤。
- 板块级 `s_body`（成分股 body 均值）为展示指标。

**成分股四维评分**（板块内 Z-score，`score_members`）：

`score = 0.4·z(涨幅) + 0.2·z(涨速) + 0.2·z(开盘至今) + 0.2·涨停分`

涨停分为 0/1 二值（不标准化），判定阈值：沪深主板 ≥9.8%、创业板/科创板 ≥19.5%、北交所 ≥29%。

**顶部统计栏**（`_market_stats`）：全池股票数、平均涨幅、涨/跌/平家数、涨停数（涨停判定阈值同上）。

## ⭐ 自选分组监控

**与板块看板的差异只有一处**：`members_map` 从 `watched_concepts` 概念成分换成 `custom_group` 表（同花顺自选分组导入），其余推导、强度、评分、缓存完全复用同一引擎。

**持仓金色标注**：名为 `CC` 的分组（`config.HOLDING_GROUP_NAME` 可改）视为持仓分组，其成分股即持仓股；返回 `holding_stocks` 与各分组 `holding_in_group`，前端金色高亮。

**ZT 涨停分组**（仅自选模式）：分组名以 `ZT` 开头的单独成区展示，不参与 Top/Bottom 排序；且不受 `MIN_MEMBER_COUNT=6` 过滤（手工分组成分天然少）。

## ⚡ 集合竞价

**数据来源**：同 kline-fetcher 分时的 `pre_market` 竞价序列（9:15~9:25，约 3 秒一点，含 `ref_price` / `matched_vol` / 未撮合买卖量）。缓存 TTL 30 秒（9:25 后竞价数据不再变化）。

**个股四因子**（跨全观察池 Z-score 后加权，`_calc_factors` / `_build_factor_df`）：

| 因子 | 公式 | 权重 | 含义 |
|---|---|---|---|
| 高开幅度 | `末点 ref / 昨收 − 1` | 0.35 | 做多意愿 |
| 竞价量比 | `竞价成交量 / 昨日成交量` | 0.30 | 爆量介入（阈值 >2 视为爆量） |
| 挂单失衡度 | `(未撮合买 − 未撮合卖) / (买 + 卖)` | 0.20 | 抢筹方向，范围 [-1, 1] |
| 价格趋势 | `末点 ref / 9:20点 ref − 1` | 0.15 | 9:20→9:25 抬升度（不可撤单窗口，真实意图） |

无昨日成交量的新股量比记 0；9:20 前无基准点时趋势记 0。

**分组聚合**（`_aggregate_groups`）：高开 = 成分 gap 均值；量比 = **中位数**（抗极端值）；失衡 = 均值；联动度 = 高开 ≥2%（`AUCTION_GAP_MIN`）的成分占比；分组综合分 = 成分股 score 均值。展示 Top 30 个股 / Top 20 分组。

## 🎯 自选强势归类 / 🌐 全市场强势归类

**选股数据来源**：iFinD REST `smart_stock_picking` 自然语言选股接口（走 `ACCESS_TOKEN` 鉴权，与 MCP 配额无关；`_rest_search` 带重试）。返回 `代码 / 名称 / 涨跌幅`，收盘数据口径（盘中实时表现以接口实际返回为准）。

**自选页归类**：命中股 = 选股结果 ∩ 自选分组股票全集，按全部自选分组归类；`hit_count` 为该组命中数（一股属多组各组各计），`coverage = hit_count / member_total`，`hit_avg_change` 为命中股平均涨幅。

**全市场页归类（2026-08-18 起知识图谱版，`kg_analysis.classify_hits`）**：

- 范围：**全量图谱板块（约 650 个）**，不再限定「监控板块管理」勾选集；勾选板块带 `is_watched` 标记，前端高亮。
- 富集倍数 `lift = 组内命中率 ÷ 该板块成员占全市场比例`——消除"融资融券"类大基数板块的命中噪音；默认按 lift 降序，可切回命中数（`order=hits`）。
- 每股带 `corr_20d`：该股与板块的 20 日相关系数 ρ（来自图谱边 `kg_edge.corr_20d`）。
- 成员数口径：概念字典快照 `member_count` 优先，图谱开放边度数兜底；`coverage = hit_count / 选股池大小`。
- 展示阈值：板块命中 ≥2（`min_hits`）、最多 30 个板块（`top_n`）。

历史版本（按勾选板块逐板块数命中）已下线，演进细节见[设计：强势归类扫描](../architecture/DESIGN-strong-stock-scan.md)。

## 🔮 板块轮动分析

**数据来源**：智能体自选工具，不走 MCP——

| 工具 | 来源 |
|---|---|
| `kline__day_kline` | kline-fetcher 个股日K |
| `kline__history_trend` | kline-fetcher 历史分时 |
| `custom__list_groups` / `custom__group_members` | 数据库自选分组（剔除 ZT / CC） |

**四阶段流程**：① 后端批量采集行情 → ② 分批 LLM 情绪周期分析（批次用轻量模型 `LLM_MODEL_BATCH`，流式输出）→ ③ 对抗审查（质疑阶段2 结论漏洞）→ ④ 综合结论（最终排名）。

## 🛠️ 监控板块管理

**数据来源**（`compute_sector_quotes_from_ifind`，概念**指数**级行情，非本地成分股聚合）：

| 页面字段 | 来源与算法 |
|---|---|
| 涨幅 | iFinD 实时行情接口 `real_time_quotation` 的 `changeRatio`（概念指数当日涨跌幅） |
| 涨跌家数 / 涨停家数 | 同接口 `riseCount` / `fallCount` / `upLimitCount`（服务端按成分股统计） |
| 实体 | 本地现算 `(latest − open) / open × 100`（盘中 latest 为最新价，收盘即 close） |
| 3日 / 5日涨幅 | iFinD 接口3（`cmd_history_quotation`）拉近 15 天概念指数日K `close`，本地算 `期末 / d日前 − 1`；日内不变 |

**fallback**：某指数实时接口未返回时，用接口3 当日日K 补涨跌幅（`changeRatio`）与实体（`(close−open)/open`）。

**候选与勾选**：候选 = 观察池 ∩ 最新成分股数 10~500（`MONITORED_CONCEPT_MIN/MAX_MEMBERS`）；勾选存 `watched_concepts` 表，保存与读取均执行数量边界校验。「刷新」按钮后台重拉 884/885/886 字典与成分股。

## 两套链路的关键区别（防混淆）

| | 盘中实时（看板 3s 轮询） | 盘后 daily（入库） |
|---|---|---|
| 数据源 | kline-fetcher 分时 | iFinD 接口3 日K |
| 板块强度 | 三维（当日切片） | 三维 + 1d/5d/20d 多周期融合（0.5/0.3/0.2） |
| 成分股排名 | 四维评分（涨幅/涨速/开盘至今/涨停） | L1 归因（贡献占比） |
| 存储 | 仅内存缓存，不入库 | `concept_strength` / `stock_attribution` 表 |

历史看板（`GET /api/history/dashboard`）读的是**盘后链路**的入库结果：`scope=sector` 按当前勾选过滤 `concept_strength`，`scope=custom` 用 `daily_kline` 按自选分组现场聚合——与实时看板的分时链路是两条路，别混用口径。
