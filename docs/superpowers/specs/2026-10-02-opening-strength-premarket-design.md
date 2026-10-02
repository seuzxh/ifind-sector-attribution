---
title: "设计：开盘板块强弱盘前归因快照"
parent: "架构与设计"
nav_order: 21
---

# 开盘板块强弱：盘前归因快照设计

- 日期：2026-10-02
- 状态：设计已确认
- 范围：第一阶段后端闭环
- 上游组件：`/root/Projects/ifind-sector-hub`

## 1. 目标

第一阶段建立一条可独立验收的盘前归因链路：从高贝塔值、近期强势、同花顺热股三个源股池取得候选股票，解析候选股票所属的同花顺行业和概念，计算每只股票当天主要代表的 Theme，并在开盘前生成冻结快照。

冻结结果供后续实时引擎读取。第一阶段不接入实时聚合、前端看板、自定义静态板块、新闻归因或自动交易。

成功标准如下：

1. 给定交易日可以生成候选股票、Membership 和 Attribution 的完整运行版本。
2. 每只已归因股票最多对应一个行业和两个概念，合计不超过三个 Theme。
3. 冻结快照可查询、可解释、不可被失败重跑破坏。
4. 相同输入和配置产生完全相同的归因顺序、分数和权重。
5. 实时模块无需导入历史特征或归因计算代码。

## 2. 已冻结的业务口径

### 2.1 源股池

| pool_id | 名称 | 同花顺指数代码 | 成分接口 |
|---|---|---|---|
| `high_beta` | 高贝塔值 | `883926.TI` | `data_pool p03473` |
| `recent_strong` | 近期强势 | `883409.TI` | `data_pool p03473` |
| `hot_stock` | 同花顺热股 | `883910.TI` | `data_pool p03473` |

三个源池的当日结果先合并去重。候选股票仍保留全部来源、各池返回顺序和源池数量，不设置主来源。

### 2.2 Theme 范围

- 行业：`884xxx`
- 概念：`885xxx`、`886xxx`
- 不纳入：`700xxx`、`881xxx`、自定义静态板块及其他代码体系

Theme 类型只由代码前缀判定：`884` 为 `INDUSTRY`，`885/886` 为 `CONCEPT`。股票和 Theme 均沿用项目现有 A 股代码校验函数，禁止在新模块复制正则或另建白名单。

## 3. 架构与模块边界

在现有模块化单体内新增 `opening_strength/` 领域包，不扩展 `theme_catalyst.py` 或 `realtime_engine.py`：

| 模块 | 职责 |
|---|---|
| `opening_strength/models.py` | 候选股票、Membership、评分证据、归因项及运行结果模型 |
| `opening_strength/source_pools.py` | 解析三个源池并保留来源信息 |
| `opening_strength/memberships.py` | 从 sector-hub 权威成分快照构造候选股票 Membership |
| `opening_strength/features.py` | 读取和规范化近期 Theme 表现与股票—Theme 同步度 |
| `opening_strength/attribution.py` | 纯函数评分、筛选、权重归一和原因码生成 |
| `opening_strength/snapshot_service.py` | 编排运行、校验、持久化和冻结 |

持久化实现放在现有 `database/` 包中，SQL 不进入领域模块。命令入口只负责解析参数并调用 `snapshot_service`，不承载业务规则。

第一阶段不修改旧实时看板的行为。后续实时阶段只能加载 `FROZEN` 快照和股票倒排索引，不得调用 `features.py` 或 `attribution.py`。

## 4. sector-hub 使用契约

sector-hub 是 ThemeDefinition 和 ThemeMembership 的事实来源，monitor 是运行版本、模型归因和冻结结果的事实来源。

盘前任务使用以下现有能力：

1. `hub.client.get_concept_members(index_code, trade_date)` 获取三个特色指数成分。
2. `hub.store.get_concept_names()` 读取 Theme 字典并筛选 `884/885/886`。
3. `hub.store.get_concept_members_map(theme_codes)` 一次读取各 Theme 最新已发布的权威成分快照。
4. `hub.store.get_latest_member_date()` 记录本次运行可见的上游最新日期。

归因链路不读取 `stock_concept_map`。Membership Resolver 将批量 Theme→股票结果在内存中反转为股票→Theme，并把本次实际使用的关系复制到 monitor 的运行快照。sector-hub 后续刷新不改变已冻结运行的内容。

sector-hub 的“最新快照”按各 Theme 自己的最近一次完整发布解析；monitor 不直接查询或解释 sector-hub 的内部 `relation_snapshots` 表。

## 5. 数据流

一次盘前运行按以下顺序执行：

1. 创建 `RUNNING` 运行记录并固定模型版本、配置版本和交易日。
2. 分别解析三个源池；过滤非 A 股代码，保存原始来源和池内顺序。
3. 合并为候选集合，同时保留多池命中关系。
4. 批量加载 `884/885/886` Membership，反转并只截取候选股票的关系。
5. 保存本次运行实际使用的候选和 Membership 快照。
6. 加载截至前一交易日的历史特征，计算每个候选股票—Theme 的评分证据。
7. 选择每股 Top Theme、归一化权重并保存归因草稿。
8. 执行覆盖率、代码范围、数量、权重和版本校验。
9. 将运行置为 `VALIDATED`，然后在一个数据库事务中冻结为 `FROZEN`。
10. 若同交易日已有冻结版本，新版本成功冻结后将旧版本置为 `SUPERSEDED`。

任何失败均把本次运行置为 `FAILED`，保留失败码和摘要；已存在的冻结版本不变。

## 6. 持久化模型

第一阶段增加四类数据。表名不带版本号，算法版本存入字段。

### 6.1 `opening_premarket_run`

| 字段 | 语义 |
|---|---|
| `run_id` | UUID，主键 |
| `trade_date` | `YYYYMMDD` |
| `status` | `RUNNING/VALIDATED/FROZEN/SUPERSEDED/FAILED` |
| `model_version` | 归因模型版本 |
| `config_version` | 配置版本 |
| `hub_member_date` | sector-hub 可见的最新成分快照日期 |
| `candidate_count` | 去重候选数 |
| `mapped_count` | 至少有一个有效 Membership 的候选数 |
| `coverage_ratio` | `mapped_count / candidate_count` |
| `started_at/validated_at/frozen_at` | ISO 时间，Asia/Shanghai |
| `failure_code/failure_detail` | 失败时填写；detail 限制长度且不含密钥或原始响应 |

同一交易日最多存在一条状态为 `FROZEN` 的记录，以 SQLite 部分唯一索引保证。

### 6.2 `opening_candidate_snapshot`

主键为 `(run_id, stock_code, source_pool_id)`。字段包括股票代码、股票名称、源池、池内顺序和可选源分数。多池命中保存为多行，不压成不可查询的 JSON。

### 6.3 `opening_membership_snapshot`

主键为 `(run_id, stock_code, theme_code)`。字段包括 Theme 名称、`INDUSTRY/CONCEPT` 类型和来源 `sector_hub_authoritative`。这是本次运行使用关系的不可变副本，不写回 sector-hub。

### 6.4 `opening_attribution_snapshot`

主键为 `(run_id, stock_code, theme_code)`。字段包括 Theme 类型、名次、原始分数、归因权重、置信度、原因码 JSON 和评分证据 JSON。证据保存归一化后的四项特征、特征覆盖率和缺失标记，足以解释冻结结果。

## 7. 第一版归因算法

股票只在其真实 Membership 内评分，模型不得生成不存在的股票—Theme 关系。

### 7.1 特征

所有特征转换到 `[0, 1]`：

1. `theme_recent_strength`：Theme 的 1、3、5、20 日收益分别做同截面百分位排名，再按 `0.4/0.3/0.2/0.1` 合成。
2. `peer_support`：同一 Theme 中除当前股票外的候选数，以 5 只为饱和值：`min(peer_count / 5, 1)`。
3. `source_pool_diversity`：该 Theme 支持股票覆盖的源池数除以 3。
4. `stock_theme_sync`：股票和 Theme 最近 20 个共同交易日收益相关系数，从 `[-1, 1]` 映射到 `[0, 1]`；少于 10 个共同有效交易日时记为缺失。

基础权重如下：

| 特征 | 权重 |
|---|---:|
| `theme_recent_strength` | 0.30 |
| `peer_support` | 0.35 |
| `source_pool_diversity` | 0.20 |
| `stock_theme_sync` | 0.15 |

缺失特征不按零分处理。算法仅在可用特征之间重新归一权重，并把基础权重的可用比例记录为 `feature_coverage`。

### 7.2 置信度与选择

置信度计算为：

```text
confidence = 0.45 * feature_coverage
           + 0.35 * peer_support
           + 0.20 * source_pool_diversity
```

默认最低置信度为 `0.35`。选择过程为：

1. 按原始分数降序排列。
2. 同分时依次按支持股票数、源池覆盖数降序，最后按 Theme 代码升序。
3. 保留置信度达标的最多一个行业和两个概念。
4. 若没有任何 Theme 达标，仍保留全体候选 Theme 中得分最高的一项并标记 `LOW_CONFIDENCE`。
5. 入选项按原始分数归一化，权重和为 1；若所有入选原始分数均为零，则等权分配。

原因码至少包括：`STRONG_RECENT_THEME`、`PEER_CONFIRMATION`、`MULTI_POOL_SUPPORT`、`HIGH_SYNCHRONY`、`LOW_SUPPORT`、`MISSING_HISTORY`、`LOW_CONFIDENCE`。

模型权重、历史窗口、支持饱和值、最低置信度和最大 Theme 数量均属于版本化配置。代码只提供默认配置，不散落魔法数字。

## 8. 历史特征边界

历史特征只使用目标交易日前已经完成的日线。当前交易日数据不得进入盘前评分。

`HistoricalFeatureProvider` 通过现有 `get_hub().client.get_history_quotation(...)` 接口分批补齐所需股票和 Theme 的日线，并复用 `daily_kline` 存储。它只请求或使用 `preClose/close/changeRatio`，不依赖当前缺失的 `volume/amount`。当目标窗口没有足够数据时返回显式缺失，不得用陈旧日期冒充目标日期的近期数据。

历史特征缺失只降低置信度，不阻止冻结；但运行记录必须汇总历史覆盖率，便于运维识别全量降级。

## 9. 失败与冻结策略

### 9.1 阻止冻结

以下情况令本次运行失败：

- 任一源池请求失败、响应结构无效或返回合法空快照。实现必须用不同失败码区分接口失败、无效响应和空快照。
- 去重候选集合为空。
- Membership 覆盖率低于默认阈值 `0.90`。
- 出现非 A 股代码或范围外 Theme 代码。
- 单股超过一个行业、两个概念或三个 Theme。
- 单股归因权重和与 1 的误差超过 `1e-6`。
- 模型版本、配置版本、交易日或运行标识缺失。

### 9.2 允许降级

- 个别候选无 Membership：保存为未映射候选，不参与归因。
- 近期 Theme 行情缺失：重算可用特征权重并记录 `MISSING_HISTORY`。
- 股票—Theme 同步度样本不足：同步度缺失并降低特征覆盖率。
- 支持股票过少：保留结果但记录 `LOW_SUPPORT`。

### 9.3 同日重跑

每次重跑产生新 `run_id`，不覆盖旧运行明细。新运行只有在全部校验通过后才能原子替换同日冻结版本。替换完成前，读取方始终看到旧的完整冻结版本。

当天进入连续竞价后，自动任务不得替换冻结版本；需要人工显式覆盖参数才能执行，且运行记录标记覆盖原因。历史日期回放不受该时间限制。

## 10. 第一阶段入口与查询

增加命令：

```text
python main.py opening-premarket --date YYYYMMDD
```

命令默认执行、校验并冻结。对同日已有冻结版本的正常盘前重跑，成功后原子替换；连续竞价后的当日覆盖必须显式传入 `--force-replace`。

第一阶段提供仓储查询方法读取：

- 某交易日当前冻结运行；
- 某运行的候选股票和源池来源；
- 某运行的股票 Membership；
- 某运行或某股票的 Attribution。

REST API 和前端页面属于第三阶段，不作为第一阶段验收条件。

## 11. 测试策略

所有新增行为遵循测试先行，并使用临时 SQLite 数据库和确定性 fixture，不依赖实时网络完成单元测试。

### 11.1 单元测试

- 三个源池合并去重且保留全部来源和池内顺序。
- 非 A 股候选被过滤，空池和无效响应被区分。
- Membership 只接受 `884/885/886`，并正确反转 Theme→股票关系。
- 四项特征计算、缺失特征权重重归一和置信度计算符合固定样例。
- 最多一个行业、两个概念，低置信度兜底只留一项。
- 同分排序稳定，零分归因等权，权重和满足容差。

### 11.2 仓储与事务测试

- 草稿、校验、冻结状态转换合法，非法转换被拒绝。
- 低覆盖率和权重错误不能产生冻结记录。
- 新版本冻结失败时旧版本保持可读。
- 新版本成功冻结后旧版本成为 `SUPERSEDED`，且任意时刻只存在一个 `FROZEN`。
- sector-hub 刷新后，已复制的运行 Membership 不发生变化。

### 11.3 集成与回放测试

- 使用伪造的 sector-hub 适配器完成全流程冻结。
- 相同输入、模型版本和配置版本重复运行得到一致结果。
- 历史特征全缺失时仍可在覆盖率达标的前提下生成降级快照。
- 新领域模块不导入 `realtime_engine`、`theme_catalyst` 或 API 层。
- 项目现有完整 Python 测试套件继续通过。

真实 iFinD 连通性只作为可跳过的 smoke test，不作为离线测试套件通过的条件。

## 12. 后续阶段边界

第二阶段加载冻结快照，构建 `stock→1~3 Theme` 倒排索引，接入实时行情、增量聚合和 Data Health。第二阶段不得改变第一阶段已冻结的 Membership 或 Attribution。

第三阶段增加 REST/WebSocket 和看板，展示题材排名、贡献股票、支持度、集中度和数据健康。自定义静态板块、新闻题材识别和盘中重新归因仍不在范围内。
