---
title: "设计：板块/概念数据层抽离（ifind-sector-hub）"
parent: "架构与设计"
nav_order: 11
---

# 板块/概念数据层抽离（ifind-sector-hub）

> 状态：需求与设计已确认，待实施 | 日期：2026-09-17 | 版本：v1.0

## 一、背景与动机

monitor 项目经多轮迭代，已沉淀出一套完整的**板块/概念数据管理能力**：ifind 数据接口封装（token 生命周期/重试/分片）、板块字典与成分股的同步与永久缓存（MAX(date) 快照语义）、个股-概念映射构建、smart_pick 板块全集枚举。

该能力目前与监控业务耦合在 `ifind_client.py` / `sync_pipeline.py` / `database.py` 中，且存在重复建设的实证：`/root/Projects/ifind-concept-trend` 已独立实现第二套 IfindClient（token 自动续期、限流、成分股接口），两套代码在 token 管理与接口语义上各自演化。

本次将该能力抽离为公共组件 **ifind-sector-hub**，作为板块/概念数据层供 monitor 及未来其他平台复用，消除重复、统一 token 与数据口径。

## 二、目标与非目标

**目标**

1. 板块/概念数据层（client + 三表存储 + 同步流程）抽离为独立 Python 包，monitor 全量切换为组件调用方，行为等价。
2. 彻底收编绕过封装的裸 SQL 访问点，三表所有读写统一走组件接口。
3. 解决 token 管理遗留隐患：REFRESH_TOKEN 轮换不再正则改写 `config_local.py`；同服务器多消费方共用账号不互踢。
4. 组件自带可选 FastAPI 服务层，为未来跨平台/跨语言供数预留形态（本期只交付、不部署）。

**非目标（本期不做）**

- `ifind-concept-trend` 等其他平台的实际接入（仅保证可接入性）。
- 板块强度、归因、实时引擎、知识图谱等消费侧业务的外移。
- 数据迁移：三表数据留在 `data/sector_attribution.db` 原地不动。
- 服务化部署、中心库建设。

## 三、已拍板决策（review 已确认，2026-09-17）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 复用形态 | **库 + 可选服务层**：核心能力为 Python 包（照 kline-fetcher 先例 `pip install -e`），包内附带可选 FastAPI APIRouter |
| 2 | 能力边界 | **纯数据层**：client + 字典/成分股/映射的同步与缓存。watched 勾选管理、观察池/成员数资格规则等监控平台私有业务**留守 monitor** |
| 3 | 存储架构 | **存储抽象 + 沿用现库**：组件存储层面向接口编程，默认 SQLite 实现指向任意库文件；monitor 指向现有 `sector_attribution.db`，零数据迁移 |
| 4 | 首期范围 | **组件 + monitor 全量切换**（含裸 SQL 收编），行为等价替换；其他平台接入放后续 |
| 5 | 包名/目录 | 包名 `ifind-sector-hub`（Python 导入名 `ifind_sector_hub`），目录 `/root/Projects/ifind-sector-hub`（独立 git 仓库，同 kline-fetcher 惯例） |
| 6 | token 持久化 | `FileTokenStore(data/ifind_sector_hub_token.json)` 取代改写 `config_local.py`；`config_local.py` 与环境变量降级为首次 bootstrap 来源 |

## 四、能力边界

| 收入组件（数据层内在能力） | 留守 monitor（监控平台私有业务） |
|---|---|
| ifind client：接口1/2/3/5/实时行情/smart_pick_boards/stocks，token 生命周期、重试退避、批量分片 | `watched_concepts` 表及勾选存取/种子逻辑 |
| 三表存储：`ths_concept_dict` / `concept_members` / `stock_concept_map`（含 MAX(date) 快照语义） | 观察池前缀（884/885/886）、成员数 10~500 资格规则 |
| 同步流程：字典 init、成分股并发拉取、映射构建、universe 补全、字典全量替换 | `sector_manage.py` 管理页行情组装（实时涨幅/实体/涨跌家数） |
| smart_pick 枚举板块全集（refresh-boards 的数据侧） | 板块强度/归因计算、实时引擎、知识图谱 |
| A股代码过滤（`is_a_share_code` / `is_a_share_concept`，数据有效性约束，随组件走） | `_rest_search` 业务级重试包装（realtime_engine） |

> 边界判据：**"换一个消费平台依然成立"的能力进组件，"只有监控平台需要"的留 monitor。** A股过滤虽是规则，但它是数据域的有效性约束（防止海外指数污染数据源），归组件。

## 五、包结构与部署形态

```
/root/Projects/ifind-sector-hub/          # 独立 git 仓库
├── pyproject.toml                  # 核心依赖仅 requests；[project.optional-dependencies] service = fastapi/pydantic
├── ifind_sector_hub/
│   ├── __init__.py                 # SectorHub 门面 + HubConfig
│   ├── codes.py                    # A股代码/前缀过滤
│   ├── tokens.py                   # TokenStore：内存默认 + FileTokenStore(flock)
│   ├── client.py                   # IfindClient（签名照搬现 ifind_client.py，配置注入化）
│   ├── storage.py                  # 三表 DDL + 快照读写
│   ├── sync.py                     # 同步编排 + replace_concept_dict
│   └── service.py                  # 可选 FastAPI APIRouter（本期只交付不部署）
└── tests/                          # mock HTTP 单测
```

- 安装：`pip install -e /root/Projects/ifind-sector-hub`（vibe-trading 环境）；monitor `requirements.txt` 注明本地依赖（同 kline-fetcher 写法）。
- 门面：`hub = SectorHub(HubConfig(db_path=..., token_store=FileTokenStore(...)))`，下挂 `hub.client` / `hub.store` / `hub.sync`。

## 六、核心接口设计

### 6.1 IfindClient

- 方法签名**照搬**现 `ifind_client.py`（接口1/2/3/5/实时行情/高频、`batch_*` 分片、`smart_pick_boards/stocks`、`get_all_ths_boards`、`search_board_by_name`），降低迁移风险。
- 差异点：构造参数化（base_url/timeout/max_retries/batch_size 注入，默认值同现 config）；**client 自持 headers**，消除 `config.HEADERS` 全局可变引用的历史包袱。
- 401 自动刷新 + 指数退避重试 + 进程锁双检逻辑原样迁移。

### 6.2 TokenStore

- token 解析顺序：**显式传入 > token 文件 > 环境变量**（`IFIND_ACCESS_TOKEN` / `IFIND_REFRESH_TOKEN`）。
- 默认内存态（与现状一致：进程内刷新、重启重新引导）。
- `FileTokenStore`：JSON 落盘 `{access_token, refresh_token, updated_at}` + `fcntl` 文件锁；401 刷新时"锁→读文件→已变则复用→未变则刷新并写回"，REFRESH_TOKEN 轮换自动持久化。
- monitor 接 `FileTokenStore(data/ifind_sector_hub_token.json)`（gitignore）。

### 6.3 Storage（三表）

- 三表 DDL 与全部读方法**原样迁移**：快照语义（日期参数缺省取 `MAX(date)`）、`get_concept_members_map` 单连接批量优化、`refresh_concept_dict_replace` 的级联清理。
- 新增两个访问器（收编现裸 SQL）：`get_concept_names()`（字典 code→name）、`get_latest_member_date()`。
- 顺带清理：`get_concept_stocks`（database.py:701）经确认无调用方，**不迁移、直接删除**（实施时以 grep 复核）。
- `watched_concepts` 建表/种子/读写**不迁移**，留守 monitor 的 `Database._init_db`（建表顺序：先组件三表、后 monitor 私有表，全部 `IF NOT EXISTS` 幂等）。

### 6.4 Sync

- `sync_concept_members(codes, date)`：并发拉取内核（现 `_fetch_concept_members_batch`，8 线程 + 进度日志），kg_sources 的复用点改走此接口。
- `init_concept_dict(codes)` / `sync_stock_concept_map(stock_codes, date)` / `init_concept_universe(...)`：板块池开关等 monitor 语义由调用方以参数传入。
- `replace_concept_dict(boards, migrate_hook)`：字典全量替换 + `concept_members` 级联清理在**同一事务**内执行；watched 名称迁移（白酒Ⅲ→白酒）通过 `migrate_hook(conn)` 回调由 monitor 注入，保持现 `database.py:261-341` 的原子性。
- monitor 的 `refresh_observe_members`（观察池编排）与 refresh-boards 的 CLI 编排留守，内部改调组件。

## 七、monitor 接入改造

### 7.1 Database 门面化

`Database` 类保留，约 18 个板块方法改为一行委托 `hub.store`，调用方（api_server/realtime_engine/sync_pipeline/kg 等）签名不变、基本零改动；watched 三方法及种子逻辑留守。

### 7.2 裸 SQL 收编清单（7 文件 9 点位）

| 位置 | 现状 | 改造 |
|---|---|---|
| `api_server.py:515,528` | 历史看板直查字典/成分股 | `get_concept_names()` / `get_concept_members_map()` |
| `realtime_engine.py:74-86` | 直查字典名+成分股名 | 同上（members_map 已含 stock_name） |
| `sector_manage.py:140-143` | 直查字典名 | `get_concept_names()` |
| `theme_catalyst.py:214-221,702-707` | 只读直连 PROD_DB 查成分/字典 | 组件只读接口（保持只读语义） |
| `auction_engine.py:59-60` | 直查成分股名 | `get_concept_members_map()` |
| `kg_sources.py:74-75` | 直查 `MAX(member_date)` | `get_latest_member_date()` |
| `main.py:174-176` | refresh-boards 直查成分股 | 随重排消灭 |

`kg_analysis.py` 的裸 SQL 目标为 kg 表/daily_kline，非三表，**不在收编范围**。

### 7.3 流程重排

- **refresh-boards**：`main.py:189-202` 与 sync_pipeline 重复的接口2响应解析逻辑合并进组件；watched 迁移以 `migrate_hook` 注入。
- **后台刷新流不动**：`_refresh_state` 状态机（api_server.py:739-801）、save/refresh 后 `realtime_engine.clear_cache()` 调用时机保持原样。
- `sync_pipeline.py` 板块半边（init/refresh/universe）改为薄编排调组件；行情同步与计算半边（sync_daily_kline/calc_*）不动。

## 八、兼容性红线（行为等价原则）

以下行为**必须原样保留**，等价性验证逐项覆盖：

1. 日期格式跨表差异：字典/映射表 `YYYY-MM-DD`，其余 `YYYYMMDD`（组件接口文档明示，不做"顺手统一"）。
2. 永久缓存快照语义：三表不传日期取 `MAX(date)` 最新快照，与 init/daily 日期解耦。
3. `refresh_concept_dict_replace` 的事务原子性（替换+级联清理+watched 迁移同一事务）。
4. watched 种子初始化（空表灌 `config.SECTOR_POOL_CODES`）及触发时机。
5. 401 刷新双检锁防并发重复刷新。
6. `get_concept_members_map` 单连接批量查询的性能特征。
7. 对外 API 响应结构零变化（前端不动）。

## 九、测试与验收

**组件单测**（ifind-sector-hub/tests，mock HTTP）

- client：token 刷新流（401→刷新→重试）、REFRESH_TOKEN 轮换持久化、指数退避、批量分片、smart_pick 解析。
- storage：快照语义、`replace_concept_dict` 级联清理与 migrate_hook 同事务、新增访问器。
- sync：mock client 下并发拉取、失败不中断、进度计数。

**monitor 等价性验证**

1. 固定端点 JSON 快照对比（切换前后）：`/api/concept/list`、`/api/sector_manage/watched`、`/api/kg/stock/{code}/sectors`、`/api/history/dashboard?scope=sector`。
2. 三表行数 + `MAX(date)` 对比（切换前后一致）。
3. `main.py test` 五接口连通冒烟。
4. 既有测试（test_api / test_performance_architecture / test_scan_push）全部保持通过。

**验收标准**：上述 4 项全绿 + 管理页手工冒烟（列表/勾选保存/后台刷新轮询）正常。

## 十、风险与开放问题

| 风险 | 缓解 |
|---|---|
| 事务拆分引入失败窗口 | migrate_hook 同事务回调，不拆事务 |
| theme_catalyst 直连改造影响其回测口径 | 保持只读语义与返回结构不变，等价验证覆盖 |
| 两套 ifind client 并存期 token 互踢（若同账号） | monitor 侧 FileTokenStore 落地后，轮换持久化不再依赖 config_local；concept-trend 接入前各自 token 独立演进，风险与现状持平 |
| 环境依赖（vibe-trading）装包顺序 | install_service.sh / DEPLOYMENT.md 补充 `pip install -e` 步骤 |

**开放问题**：无（实施中发现新问题按流程回补本文档）。

## 十一、文件清单

**新增**：`/root/Projects/ifind-sector-hub/` 全部（pyproject.toml、ifind_sector_hub/ 六模块、tests/）。

**monitor 修改**：`database.py`（门面化+删 get_concept_stocks）、`ifind_client.py`（**删除**，由组件替代）、`sync_pipeline.py`（板块半边薄编排）、`main.py`（refresh-boards 重排）、`api_server.py`（2 处收编+hub 初始化）、`realtime_engine.py`、`sector_manage.py`、`theme_catalyst.py`、`auction_engine.py`、`kg_sources.py`（各 1 处收编）、`scripts/backfill_style_history.py`（直连 ifind_client，import 切换组件）、`scripts/backfill_daily.py`（走 Database 门面，签名不变，仅回归验证）、`config.py`（网络参数/A股过滤迁出后的引用调整）、`requirements.txt`、`install_service.sh`、`AGENTS.md` / `README.md` / `docs/architecture/ARCHITECTURE.md`、`.gitignore`（ifind_sector_hub_token.json）。

**不动**：`core_calculator.py`、`kg_builder.py`、`kg_analysis.py`、`scan_push.py`、`open_scan_engine.py`、`rotation_agent.py`、`frontend/` 全部、数据库文件。

## 十二、实施顺序概览

骨架 → client+tokens（含单测）→ storage（含单测）→ sync（含单测）→ monitor Database 门面化 + 裸 SQL 收编 → refresh-boards 重排 → 等价性验证 → 可选 service 层随包交付 → 文档更新。

> 详细实施计划（任务粒度/顺序/验证点）在本文档审阅通过后以 writing-plans 展开。
