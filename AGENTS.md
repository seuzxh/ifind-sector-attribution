# AGENTS.md

> 本文件供 AI 智能体快速了解本项目。读完本文件应能知道：项目是什么、怎么跑、代码在哪改、有哪些易踩的坑。深度内容见文末「深入阅读」指向的文档。

## 一句话

基于同花顺 iFinD API 的 **A股行业归因 + 板块强度检测** 系统。**仅处理沪深北交易所 A 股**（.SH/.SZ/.BJ），全链路过滤海外代码。输出：哪个板块最强、每只股票被哪个概念带涨。盘中实时监控基于 kline-fetcher 分时数据，**从 9:15 集合竞价即可开始**（ref_price 推算涨跌）。

## 当前状态（2026-10-03 核对）

- 观察池已覆盖 884/885/886；管理页通过 `POST /api/sector_manage/refresh` 启动后台刷新，并轮询 `/api/sector_manage/refresh/status`。无定时自动刷新。
- `watched_concepts` 保存管理页的持久化选择；现役有效范围 = 勾选集 ∩ 最新成分股数 10~500 的板块。realtime、daily 和 scan 统一读取该有效范围，不再经过盘前 watchlist。
- 板块监控只接受最新成分股数 **10~500（含边界）** 的概念；管理候选、保存接口及实时引擎均执行过滤，越界板块即使残留在旧 `watched_concepts` 数据中也不生效。
- 本机运行库的 `stock_concept_map` 当前为空（2026-07-24 实测）；实时监控不受影响，但下一次 daily 个股归因前必须重跑 init 映射流程，不能把历史 `stock_attribution` 行误认为映射仍就绪。
- 本机没有为本项目安装 daily crontab，`daily_kline` 最新日期为 20260930（2026-10-03 实测）；盘后数据是否补齐需显式运行 `main.py daily` 并复核，README/DEPLOYMENT 中的 crontab 只是建议配置。
- 板块字典（ths_concept_dict 710 个=881×90+884×230+885×293+886×97）由 `refresh-boards` 命令用 smart_stock_picking 动态枚举维护；881 二级行业仅入字典**不进观察池**（OBSERVE_CONCEPT_PREFIXES=884/885/886）。
- 强势归类选股走 REST `smart_stock_picking`（`ACCESS_TOKEN`，`ifind_client.smart_pick_stocks`），**不走 MCP**（MCP search_stocks 有每日配额且曾反复打满，已于 2026-09-07 彻底移除 MCP 链路：mcp_proxy.py 已删、IFIND_MCP_TOKEN 已清）。
- 开盘题材只读 REST、独立实时聚合与第八个 Vue Tab 已于 2026-10-03 合入并部署到 `115.191.14.82:8000`（当前发布提交 `1d1ddaa`）；休市/当日无快照时实时页回退最近冻结版本。盘中只读冻结归因，不重跑历史特征或归因。
- 本机运行库已完成四张 `opening_*` 表的 schema 迁移；2026-10-03 已为交易日 20260930 生成首个真实 `FROZEN` 快照（158 只候选全部映射、143 个题材、历史特征覆盖率 0.9942），并验证历史分时健康度 1.0。`data/DATABASE_MANIFEST.json` 已同步刷新；运行判断仍以 SQLite 现场结果为准。
- 开盘题材榜按冻结版本 `hub_member_date` 之前最近的全量成分快照过滤：概念与行业成分股 **>300** 排除，300 保留；未知成分数保留并显示“未知”。主榜返回 Top10，加速/扩散榜限于这十个题材；`theme_count` 为过滤后题材总数。每个题材直接显示候选个股池，默认五只，支持展开全部。

## 🔑 运维知识：access_token 过期自动刷新（重要，别再踩）

**问题背景**：iFinD 数据接口的 `ACCESS_TOKEN` **7 天过期**（报 `errorcode:-1302` / HTTP 401），历史上多次卡住数据拉取。

**已落地方案**（组件 `ifind-sector-hub` 的 `tokens.py`，monitor 经 `ifind_hub.py` 单例接入）：
- `TokenStore.refresh_access_token()`：用 `REFRESH_TOKEN` 调 `https://quantapi.51ifind.com/api/v1/get_access_token` 换新 token；**双检锁**防并发重复刷新。
- `IFindClient._post()`：检测到 **HTTP 401 自动刷新并重试**（仅刷一次防死循环）。
- **REFRESH_TOKEN 长期有效**（与账号到期日一致），只要它不过期，ACCESS_TOKEN 就能自动刷新。
- monitor 用 **`FileTokenStore`（`data/ifind_sector_hub_token.json` + flock）**：刷新结果与 REFRESH_TOKEN 轮换自动落盘、跨进程共享，**不再改写 `config_local.py`**；`config_local.py` / 环境变量仅作首次 bootstrap（文件缺失时用其种子引导）。

**手动刷新**（调试用）：`PYTHONPATH=. python -c "from ifind_hub import refresh_token_now; print(refresh_token_now())"`

## 运行环境（关键，别猜）

| 项 | 值 |
|---|---|
| 项目根目录 | `/root/projects/2.monitor_940/ifind-sector-attribution` |
| Python | conda 环境 **`vibe-trading`**：`/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python` |
| 工作目录约定 | 所有命令须在本项目根目录执行（`config_local.py`、`data/` 均为相对路径），跑 main.py 需 `PYTHONPATH=.` |
| iFinD token | `ACCESS_TOKEN` / `REFRESH_TOKEN`：读 `config_local.py` 或环境变量 `IFIND_ACCESS_TOKEN` / `IFIND_REFRESH_TOKEN`（仅首次引导，运行期看 `data/ifind_sector_hub_token.json`） |
| **板块数据层组件** | **`ifind-sector-hub` 本地包（不在 PyPI，须单独装）**：`pip install -e /root/Projects/ifind-sector-hub`（client/三表存储/同步；monitor 经 `ifind_hub.py` 单例接入） |
| **分时数据依赖** | **`kline-fetcher` 本地包（不在 PyPI，须单独装）**：`pip install -e /root/Projects/kline-fetcher`，或 `pip install git+https://github.com/seuzxh/kline-fetcher.git` |
| **kline API 地址** | `KLINE_API_BASE_URL`（中焯行情 API，盘中实时监控用）：配在 `config_local.py` 或环境变量，**不配则实时链路不可用** |
| 数据库 | `data/sector_attribution.db`（SQLite，19 张现役表；含 hub 的 `relation_snapshots`、知识图谱 5 表和盘前快照 4 表） |
| 交易日历缓存 | `data/trade_calendar.txt`（`trade_calendar.py` 三级缓存的本地落盘，缺失会自动重建） |
| 服务器 / 部署 | **115.191.14.82:8000**；systemd 服务 `ifind-monitor`，一键装 `sudo bash install_service.sh`（详见 `docs/ops/DEPLOYMENT.md`） |

> ⚠️ 上述 `config_local.py` 已 gitignore，**勿提交、勿外传**（含真实 token）。跑命令前务必用上面的 conda python，否则缺 `fastapi`/`pandas`/`numpy`/`plotly` 等依赖；盘中实时链路还需 `kline-fetcher`。

## 入口命令（`main.py`）

| 命令 | 作用 | 备注 |
|---|---|---|
| `init` | 首次部署：拉字典+成分股+映射，补全概念板块全集 | 耗时较长（并发拉取全市场） |
| `daily --date YYYYMMDD` | 每日盘后：同步日K → 板块强度 → 个股归因 | 日期须为交易日；不传 `--codes` 自动反查全市场 |
| `opening-premarket --date YYYYMMDD [--force-replace]` | 执行、校验并冻结盘前归因快照 | 日期必填八位数字；当日 09:30 后替换已有冻结版本须显式覆盖 |
| `server [--host H] [--port P] [--reload]` | 启动 FastAPI（API + 可视化看板），默认 `0.0.0.0:8000` | 生产用 systemd，调试加 `--reload` |
| `import-groups [--json FILE]` | 导入同花顺自选股分组 JSON → `custom_group` 表（幂等覆盖） | 默认读 `ths-custom-block-data/同花顺自选分组导出.json`；自动过滤指数/ETF/可转债等非 A 股标的 |
| `push --slot {933,945,1000,1430} [--dry-run]` | 股池归因定时推送：按时间槽选股归类并推飞书（crontab 交易日 4 时段，见 `scan_push.py`）；自选侧=分组归类，全市场侧=**KG 富集归类**（同全市场强势归类页，卡片带富集倍数/已监控标记） | `--dry-run` 只归类打印不推送 |
| `kg_init [--force] [--refetch] [--skip-verify]` | 构建知识图谱（P1）：4 表落库+统计报告+族群初探 | 幂等（已有快照拒绝，`--force` 重建）；当天有成分股快照则接口2 零调用 |
| `kg_update [--force] [--skip-verify]` | 知识图谱周维护（P2）：拉两源→diff→开/关边+升降级+变更日志+快照 | crontab 周日 20:00 自动跑（`scripts/run_kg.sh`）；幂等；状态从边 confidence 恢复（勿按 source 判态） |
| `kg_query CODE [--linked N]` | 查图谱：个股→板块（含 ρ）/ 板块→成分股 / 联动股 TopN | 代码可带/不带后缀；`--linked` 按共享板块数排联动股 |
| `kg_corr [--window 20] [--no-corr-weight]` | 知识图谱分析（P3）：算 20 日 ρ 边权（写 `kg_edge.corr_20d`）→ Louvain 族群（写 `kg_community`） | 窗口锚定 daily_kline 最新交易日；daily 同步恢复后重跑一次即可刷新 |
| `purge [--vacuum]` | 删除海外数据，仅留 A 股 | **破坏性**：执行前备份数据库；幂等可重跑 |
| `test` | 测试 5 个 iFinD 接口连通性 | — |

## 代码地图（改东西先看这里）

| 文件 | 职责 | 改动频率 |
|---|---|---|
| `config.py` | token、概念代码、计算权重（SCORE_WEIGHTS / PERIOD_WEIGHTS）、A股过滤白名单、分时数据源配置（KLINE_API_BASE_URL / INTRADAY_*） | 偶尔 |
| `config_local.py` | 本地 token + KLINE_API_BASE_URL，**已 gitignore** | — |
| `ifind_hub.py` | **板块数据层组件接入点**：`get_hub()` 进程级单例（FileTokenStore 落 token）+ `refresh_token_now()` 手动刷新 | 低 |
| `ifind-sector-hub`（外部包） | 板块/概念数据层公共组件（`/root/Projects/ifind-sector-hub`）：IFindClient、三表存储 SectorStore、同步 SectorSync、可选 service 层 | 低（独立仓库） |
| `intraday_fetcher.py` | 分时数据批量并发封装（kline-fetcher TrendFetcher，32线程），盘中实时用 | 低 |
| `database/` | **SQLite 封装（包）**：`core.py` 连接/DDL/Mixin 组装、`schema.py` 建表 DDL、领域模块 `kline/kg/results/sector_tables/watched/custom_group/maintenance`（方法与拆包前逐一致）。板块三表（字典/成分股/映射）委托 `ifind-sector-hub` 组件 SectorStore（同库文件，watched 勾选留 monitor） | 中（按领域改对应模块） |
| `sync_pipeline.py` | 数据同步与计算管线（init/daily 编排）。板块同步半边委托组件 hub.sync；行情/计算半边不变 | 中 |
| `opening_strength/` | 盘前归因冻结 + `quote_provider` 独立分时缓存 + `realtime_aggregation` 纯聚合 + `dashboard_service` 只读服务；SQL 在 `database/opening_strength.py`，CLI 在 `main.py` 懒装配 | 中 |
| `core_calculator.py` | 板块强度 + 多周期融合 + L1 归因算法 | 中（改算法看这） |
| `stock_scorer.py` | 盘中成分股四维评分（涨幅/涨速/开盘至今涨幅/涨停）+ 涨速加速 | 低 |
| `realtime_engine.py` | 盘中实时引擎（分时序列缓存 + 时刻切片 + 内存计算，**不入库**） | 低 |
| `trade_calendar.py` | 交易日历模块（`TradeCalendar` 单例，三级缓存：内存→`data/trade_calendar.txt`→网络→DB 兜底；复用 `kline_fetcher.fetch_trade_calendar`） | 低 |
| `probe_auction.py` | 集合竞价数据探针脚本（生产环境验证 `pre_market` 形态用，非业务链路） | 低 |
| `api/` | **FastAPI 接口层（包）**：`app.py` 组装+SPA 入口、`deps.py` db 单例、`schemas.py` 请求模型、`routers/` 按域路由（含 opening_strength）、`history_service.py`+`kg_views.py` 编排下沉 | 中（加接口看 routers/） |
| `api_server.py` | 兼容入口（`from api import app`；uvicorn `"api_server:app"` 不变） | 低 |
| `sector_manage.py` | 监控板块管理：多周期涨幅计算（1d/3d/5d）+ 候选板块列表组装 | 中（改管理页看这） |
| `kg_sources.py` | 知识图谱数据源适配层（SourceAdapter 协议 + iFinD 接口2主源/接口1验证源两个 Adapter，未来加申万/问财只写新 Adapter） | 低 |
| `kg_builder.py` | 知识图谱构建：`kg_bootstrap`（幂等）+ 统计报告 + 周维护 `kg_update`（diff 状态机，旧态从边 confidence 恢复） | 中（改图谱构建/周维护看这） |
| `kg_analysis.py` | 知识图谱分析（P3）：`compute_corr_20d`（ρ 边权，先 join 后 tail 对齐）+ `detect_communities`（Louvain）+ `hub_sectors`/`linked_stocks`/`locate_sectors`（组合定位：一批股→板块富集/命中双指标）/`dedup_dashboard_sectors`（看板三层去重，分类快照有缓存） | 中（改图谱分析看这） |
| `frontend/` | **Vue 3 + Vite + TypeScript SPA**。构建 → `static/`，FastAPI 托管。8 个 Hash Tab：板块强度/开盘题材/自选分组/竞价/强势归类×2/管理/知识图谱。结构详见 `docs/architecture/FRONTEND.md` | 中（改前端看这 + FRONTEND.md） |
| `static/` | 前端构建产物（FastAPI `mount('/static')` 托管；已 gitignore，勿手改） | — |
| `install_service.sh` / `ifind-monitor.service` | systemd 一键安装脚本 + 服务配置（绑 0.0.0.0:8000，Restart=always） | 低 |
| `main.py` | 命令入口（argparse 子命令） | 低 |

## 数据库（必读）

schema 权威来源：monitor 私有表看 `database/schema.py`（建表 DDL）与 `database/core.py::_init_db()`（迁移与种子），板块三表（`ths_concept_dict`/`stock_concept_map`/`concept_members`）看组件 `ifind_sector_hub/repositories/storage.py`（同一库文件）。本机若存在 **`data/DATABASE_MANIFEST.json`**，它是 gitignore 的运行库快照（行数、日期范围、样本和常见查询），查询本机数据库前应先读，但不能假设其他 checkout 一定存在或仍是最新。

9 张既有业务表：`ths_concept_dict` / `stock_concept_map` / `concept_members` / `daily_kline` / `min1_kline`（空）/ `concept_strength` / `stock_attribution` / **`custom_group`** / `watched_concepts`。另有 hub 的 `relation_snapshots`、**知识图谱 5 表**和**盘前归因 4 表**，合计 19 张现役表。知识图谱设计见 `docs/architecture/DESIGN-knowledge-graph.md`，盘前表契约见下节。本机已删除退役 `watchlist`；其他未清理的旧数据库仍可能残留该历史表。

**最容易踩的坑**：
1. **日期格式跨表不一致** — `ths_concept_dict`/`stock_concept_map` 用 `YYYY-MM-DD`，其余表用 `YYYYMMDD`。跨表 JOIN 前必须格式归一，否则键对不上。
2. **永久缓存表** — `stock_concept_map`/`concept_members`/`ths_concept_dict` 查询时不传日期默认取 `MAX(date)` 最新快照，与 init/daily 日期解耦。这是设计而非 bug。
3. **`daily_kline.volume`/`amount` 普遍为 NULL**（接口未拉取），不要假设非空。
4. **`stock_attribution.attribution_json` 内含 JSON `NaN`**（非标准 JSON），`json.loads` 能解析，其他解析器需容错。
5. **`concept_strength.score_final`/`rank_1d` 跨日不可直接比**（每日独立 Z-score 标准化），比较强弱只在同一 `calc_date` 内有意义。

代码访问数据库统一走 `database` 包的 `class Database`（`from database import Database` 不变），`with self._connect() as conn` 上下文管理（自动 commit/rollback）。

**分层约束（机器可查）**：`tests/test_layering.py` = import-linter 契约（`.importlinter.ini`，管 api/database 包边界）+ AST 检查（管平铺模块和 `opening_strength` 包：引擎不碰接口层 / config 叶子 / 计算层纯净 / api_server 只组装）。盘前包的任何导入（含函数内懒导入）均禁止依赖 `realtime_engine`、`theme_catalyst`、`api` 或 `api_server`。改完分层相关代码跑 `python -m unittest tests.test_layering`。

## 盘前冻结与开盘题材

- 命令：`PYTHONPATH=. python main.py opening-premarket --date YYYYMMDD [--force-replace]`。`main.py` 只解析、装配和输出；业务规则由 `OpeningPremarketService.run_and_freeze()` 执行。
- 固定源池：高贝塔值 `883926.TI`、近期强势 `883409.TI`、同花顺热股 `883910.TI`；hub `get_concept_members()`，保留来源和池内顺序，复用 A 股校验。
- Theme 只认行业 `884xxx` 和概念 `885xxx/886xxx`，排除 `700xxx/881xxx`、自定义静态板块及其他体系。读取 `get_hub().store` 最新权威成员并批量反转，不读取 `stock_concept_map`，运行副本不写回 hub。
- 四张私有表：`opening_premarket_run`、`opening_candidate_snapshot`、`opening_membership_snapshot`、`opening_attribution_snapshot`；schema/迁移在 `database/schema.py` / `database/core.py`，查询走 `Database`。表契约和证据字段见 README §9。
- 覆盖率门槛 `0.90`，每股最多一个行业和两个概念、权重和为 1；历史仅用目标日前完成日线，缺失允许降级并记录 `history_coverage_ratio`。成功输出运行标识、状态及覆盖指标；领域错误退出 1，不输出上游响应或敏感详情。
- 新运行通过校验后原子冻结，同日旧版本成为 `SUPERSEDED`，失败不破坏旧冻结版本。当日 09:30（Asia/Shanghai）后替换已有冻结版本须 `--force-replace`；历史回放不受此限制。
- 开盘题材：`#/opening-themes` → `GET /api/opening-strength/dashboard?trade_date=YYYYMMDD[&snapshot_time=HH:MM][&fallback_to_previous=true]`，只读 `FROZEN` 快照，不自动归因或写排名。实时页面显式启用回退：当日无快照时读取不晚于请求日的最近冻结版本、展示实际日期并停止轮询；手选历史日期严格查询，不静默回退。仍无快照时返回结构化 `404 SNAPSHOT_NOT_FOUND`。
- 独立行情提供器只用 09:30 起 `trading`，当天序列缓存 15 秒、历史序列进程内稳定；结果缓存 3 秒且包含 `run_id`。不耦合旧实时引擎；指标/错误合同见 `docs/guides/api.md`。
- 自动盘前调度、集合竞价、自定义静态题材、WebSocket、排名持久化均排除。20260930 已有真实冻结和可回放分时；后续交易日仍需人工执行盘前命令。
- 离线测试使用 fixture/临时 SQLite，真实 iFinD smoke 默认跳过。完整命令和开盘专项命令见 README §10，必须保留发现参数 `-t .`，使用上方 `vibe-trading` 解释器。

绑定设计：[盘前快照](docs/superpowers/specs/2026-10-02-opening-strength-premarket-design.md)、[开盘题材看板](docs/superpowers/specs/2026-10-03-opening-theme-dashboard-design.md)。

## 不可违反的约束

- **A 股范围限定是硬约束**：个股代码后缀只认 `.SH/.SZ/.BJ`，概念代码前缀只认 `700/881/883/884/885/886`。判定用 `config.is_a_share_code()` / `config.is_a_share_concept()`，不要自己写正则。
- **海外前缀** `861/864/865/871/875` 是美股/港股行业指数，会污染股票池，必须排除。
- **双概念编码体系**：行业码（700xxx/881xxx，来自 `config.ALL_CONCEPT_CODES`）与概念板块码（885xxx/886xxx，来自接口1）**交集为 0**。归因链路靠 `init_concept_universe` 补全后者后才能 JOIN 打通。详见 ARCHITECTURE.md §1。
- **日期须为交易日**：`daily --date` 传非交易日会因当日无数据返回空。
- **监控板块由持久化选择 + 成员数规则共同决定**：管理页选择存于 `watched_concepts`，读取时只保留最新成分股数 10~500（含边界）的板块。实时看板拉取有效板块的全部去重成分股；有效集为空时看板/scan 提示“未配置”，daily 归因按既有逻辑退回 `config.SECTOR_POOL_CODES`。改选择走管理页，改资格边界才改 `config.MONITORED_CONCEPT_*`。

## 两套数据链路

| 维度 | daily（盘后，入库） | realtime（盘中，仅内存） |
|---|---|---|
| 数据源 | 接口3 日K | **kline-fetcher 分时数据**（中焯 API，非 iFinD） |
| 板块强度 | 多周期融合（1d/5d/20d） | 仅 1d 实时强度（切片末点涨幅） |
| 成分股排名 | L1 归因（贡献占比） | 四维加权评分（涨幅/涨速/开盘至今涨幅/涨停） |
| 缓存 | 持久化 `concept_strength` / `stock_attribution` | 分时序列内存缓存（TTL 15s，历史日期全天缓存） |
| 拉取范围 | 全市场 A 股 | 管理页有效板块（勾选且成员数 10~500）的全部去重成分股 |

## 盘中实时链路补充（易忽略）

- **集合竞价也能监控（9:15~9:25）**：此阶段 `trading` 为空，但 `pre_market` 有逐点 `ref_price`（3 秒一点，~201 点）。`realtime_engine._build_indicator_df` 按两阶段分支：集合竞价**只用末点 ref_price 算涨幅**，`speed/body/acceleration` 置 0；进度条 `available_times` 含 09:15~09:25 点，**自动从 09:15 起**。
- **trading 切片严格按 snapshot_time 过滤，不兜底回退**（否则 9:20 会误用 9:30 数据）。
- **交易时段由服务端 `session_phase` 决定**（`trade_calendar.py`，7 个 phase：`pre_open`/`auction`/`pre_morning`/`morning`/`lunch`/`afternoon`/`closed`）。前端仅 `<9:15(pre_open)` 和非交易日停 3s 轮询，**收盘后 `closed` 仍轮询**展示全天数据供回看。
- **3s 是页面请求周期，不是上游行情粒度**：盘中 `trading` 为分钟点。状态栏“刷新 HH:MM:SS”证明页面轮询仍在运行；行情时刻只在新分钟数据到达时变化。分时序列后台刷新成功后必须失效同日期/模式的 `result_cache`，避免旧结果继续遮住新序列。
- **成分股列排序必须覆盖全部有效成员**：看板主响应只保留 `members_top10`；实时页面点击涨幅/涨速/加速/body/综合分时调用 `GET /api/dashboard/members`，后端在全体有效成员上排序后仅返回前 10。不要退回浏览器只重排原 10 支，也不要把所有成员塞进 3s 主响应。
- **历史日期回看 ≠ 历史看板**：实时接口传 `trade_date=YYYYMMDD` 走分时链路（拉该日全天分时 + 内存切片）；`/api/history/dashboard` 的 `scope=sector` 读取并按当前勾选集过滤 `concept_strength`，`scope=custom` 用 `daily_kline` 按自选分组现场聚合。两条路径别混。
- **自选股分组看板**：`GET /api/custom/dashboard` 用 `custom_group` 表替代概念板块算分组强弱，复用 realtime_engine 的缓存/切片（仅 `members_map` 来源不同）。需先用 `import-groups` 导入分组。
- **Vue SPA 多 Tab**：根路由 `/` 返回 `static/index.html`，8 个 Hash Tab、`<keep-alive>` 保留状态。`DashboardPage` 复用 sector/custom；`ScanPage` 复用 scan/market_scan。`OpeningThemesPage` 停用时停止轮询/播放并失效在途响应，激活时刷新。
- **时间条播放**：`DashboardPage` 已接入 `usePlayTimeline`，按速度 1.5x/2x/4x/8x 逐分钟推进。切模式/切日期/拖滑块/点"回到最新"自动停止；播放时 `autoFollow=false`。`usePolling` 的共享序号守卫防异步乱序覆盖。

## 三套数据源（重要）

| 源 | 用途 | 调用方 |
|---|---|---|
| iFinD 接口1/2/5 | 概念字典、成分股、个股映射（永久缓存） | `sync_pipeline` init |
| iFinD 接口3 | 日K线（daily 同步 + 多周期 + 归因） | `sync_pipeline` daily |
| **kline-fetcher 分时**（中焯 API） | 盘中实时分时序列 | `realtime_engine`（经 `intraday_fetcher`） |

> 分时数据无 OHLC、无 changeRatio，所有指标用 `last_price` 推导：昨收=`pre_market[0].ref_price`，开盘价=`pre_market[-1].ref_price`。详见 ARCHITECTURE.md §7。

## 关键算法速记

- **板块强度三维**：S1 涨幅 / S2 上涨广度 / S4 相对强度，权重 `0.4/0.3/0.3`（`SCORE_WEIGHTS`），各分量先 Z-score 标准化。
- **多周期融合**：`score_final = 0.5×score_1d + 0.3×score_5d + 0.2×score_20d`（`PERIOD_WEIGHTS`）。
- **L1 归因**：`contribution_c = weight_c × concept_return_c`，`weight = 1/该股概念数`（等权），`concept_return = 成分股当日涨幅均值（不含自身）`。
- **成分股四维评分**（盘中）：`0.4×z(涨幅) + 0.2×z(涨速) + 0.2×z(开盘至今涨幅) + 0.2×涨停分`。涨停阈值：主板 9.8% / 创业科创 19.5% / 北交 29%。
  - 涨幅=`(last-昨收)/昨收`；涨速=`(last[-1]-last[-2])/last[-2]`（1min 滚动）；开盘至今=`(last-开盘价)/开盘价`（开盘价=09:25 集合竞价价）
  - 涨速加速 `=speed[-1]-speed[-2]`：>0 加速 / <0 减缓，**仅展示不进综合分**
  - 时间条 `snapshot_time` 切片：截 `trading[:snapshot_time]` 用末点重算，纯内存毫秒级
  - 时间条**播放**：`togglePlay` 定时器逐分钟推进滑块（速度 1.5x/2x/4x/8x），播放时自动暂停"自动跟随最新"
  - **请求序号守卫**：`usePolling` 为定时刷新、手动刷新和历史强制计算提供共享序号，响应回来若过期则丢弃
- **持仓醒目标注**（自选看板专属）：`HOLDING_GROUP_NAME="CC"` 识别持仓分组，其成分股作持仓股。含持仓的分组返回 `holding_in_group`，前端金色高亮（排行表行+卡片描边+持仓个股行+持仓标签）。仅 `isCustomBoard` 生效。
- **两层成员数门槛不要混淆**：静态板块候选要求最新成分股总数 10~500；进入计算后，`MIN_MEMBER_COUNT=6` 要求本次行情实际命中的成员不少于 6，防止数据缺失时样本失真。

## REST API（`api_server.py`，默认 `0.0.0.0:8000`）

| 接口 | 方法 | 说明 |
|---|---|---|
| `GET /` | — | **前端入口**（Vue 3 SPA，`static/index.html`，Hash 路由） |
| `GET /api/sector/rankings` | — | 板块强度排名（含多周期融合分） |
| `POST /api/attribution/stock` | — | 个股多概念归因 |
| `POST /api/attribution/portfolio` | — | 组合归因 + 强势板块定位 |
| `GET /api/realtime/dashboard` | — | **板块实时看板**（管理页有效板块，分时切片；强弱榜带 KG 三层去重：枢纽过滤+马甲折叠+族群限额2席，板块项含 similar/community_id，响应含 kg_dedup） |
| `GET /api/opening-strength/dashboard` | — | **开盘题材**：只读冻结归因 + 分时切片，结构化 404/422/503 错误 |
| `GET /api/custom/dashboard` | — | **自选分组看板**（`custom_group` 替代概念板块，复用实时切片，返回 `holding_stocks`/`holding_in_group`） |
| `GET /api/custom/scan` | — | **自选强势归类**（REST `smart_stock_picking` 自然语言选股 → 取自选交集 → 按自选分组归类） |
| `GET /api/market/scan` | — | **全市场强势归类**（REST `smart_stock_picking` 选股 → **知识图谱富集归类**：全量 650 板块按 lift/命中数排序，每股带 ρ，勾选板块带 is_watched；入参 `query/order/min_hits/top_n`，不碰分时） |
| `POST /api/realtime/clear_cache` | — | 清空分时序列缓存（切日/调试用） |
| `GET /api/history/dashboard` | — | **历史看板**（`scope=sector` 当前勾选板块；`scope=custom` 自选分组；均按收盘涨幅展示） |
| `GET /api/auction/dashboard` | — | 集合竞价看板 |
| `GET /api/sector_manage/list` | — | 可管理板块列表 |
| `GET /api/sector_manage/watched` | — | 当前监控板块 |
| `POST /api/sector_manage/save` | — | 全量保存监控板块 |
| `POST /api/sector_manage/refresh` | — | 后台刷新 884/885/886 字典与成分股 |
| `GET /api/sector_manage/refresh/status` | — | 查询刷新状态 |
| `GET /api/kg/stock/{code}/sectors` | — | 知识图谱：个股归属板块（含 ρ、confidence、生效区间） |
| `GET /api/kg/sector/{code}/stocks?order=` | — | 知识图谱：板块成分股（`order=corr` 按 ρ 排） |
| `GET /api/kg/linked/{code}?top_n=` | — | 知识图谱：联动股 TopN（共享板块 Σ\|ρ\| 评分） |
| `GET /api/kg/communities?only_sector=` | — | 知识图谱：Louvain 板块族群 |
| `GET /api/kg/changes?date=&change_type=&limit=` | — | 知识图谱：周 diff 变更日志 |
| `GET /api/kg/graph/projection?min_jaccard=` | — | 知识图谱供数：板块投影图（cytoscape elements，含族群色组） |
| `GET /api/kg/graph/star?code=&limit=` | — | 知识图谱供数：个股星型子图 |
| `GET /api/kg/graph/sector/{code}?limit=` | — | 知识图谱供数：板块展开子图 |
| `GET /api/kg/locate?codes=&group=&order=lift或hits` | — | **组合定位**：一批股票（或自选分组名）→ 共同指向板块（富集倍数/命中数双指标，分组名 TRIM 容错） |
| `GET /api/kg/locate/groups` | — | 自选分组列表（组合定位下拉用） |
| `GET /api/dates` | — | 已入库的板块强度日期列表 |
| `GET /api/concept/list` | — | 全部 A 股概念板块列表 |
| `GET /api/concept/members` | — | 概念成分股（`date` 不传则取最新缓存） |
| `GET /api/trade_calendar?year=YYYY` | — | 交易日列表（前端日期选择器过滤非交易日用） |
| `GET /api/session_status` | — | 当前交易时段状态（`is_trading_day`/`phase`/`next_open_time`/`next_trade_day`，前端盘前判断用） |

## 常见任务 → 怎么做

| 想做的事 | 怎么做 |
|---|---|
| 加一个 API 接口 | 在 `api/routers/` 对应域文件加路由（重编排逻辑下沉 `api/history_service.py`/`api/kg_views.py` 风格）；数据查询走 `database/` 包 |
| 改板块强度算法 | `core_calculator.py`（`calc_all_sectors_strength` / `calc_multi_period_score`） |
| 改某个表的字段 | 改 `database/schema.py` 建表 + 对应领域模块读写方法；本机存在 `data/DATABASE_MANIFEST.json` 时同步刷新该本地快照 |
| 加新概念分类 | `config.ALL_CONCEPT_CODES` 加码 → 重跑 `init` 的 `init_concept_universe` |
| 查数据库结构/样本/查询模板 | schema 读 `database.py::_init_db()`；本机数据范围优先读 `data/DATABASE_MANIFEST.json` 并用 SQLite 复核 |
| 盘中实时拉取失败 / `ImportError: kline_fetcher` | 检查 kline-fetcher 是否 `pip install -e` 装好 + `KLINE_API_BASE_URL` 是否配置 |
| 接入自选股分组监控 | `main.py import-groups` 导入 JSON → 调 `GET /api/custom/dashboard` |
| 改交易时段判定 | `trade_calendar.py`（`session_phase`、交易日历） |
| 改持仓分组（自选看板金色标注） | `config.HOLDING_GROUP_NAME` 改分组名（默认 "CC"），无需改代码 |
| 改前端（加 Tab / 改看板） | `frontend/src/`（Vue SPA）：`views/` 加页 + `router/index.ts` 加路由 + `AppLayout.vue` 加 Tab；接口在 `api/<域>.ts` 封装。详见 `docs/architecture/FRONTEND.md` |
| 时间条播放异常（时刻跳变） | 检查 `usePolling` 的共享请求序号守卫是否被破坏（防异步乱序覆盖） |
| 改全市场选股预置条件 | `frontend/src/views/ScanPage.vue` 的预置条件数组；自定义条件存浏览器 localStorage（`market_scan_custom_queries`，结构 `{label,query}`），页面可存/重命名/删除 |
| 全市场选股归类慢 / 报错 | `/api/market/scan` 走 REST `smart_stock_picking`（ACCESS_TOKEN，401 自动刷新）；报错看 token 是否过期、query 表述是否清晰（支持'实体涨幅'） |

## 深入阅读

| 文档 | 内容 |
|---|---|
| `data/DATABASE_MANIFEST.json` | 本机运行库快照（gitignore，可能不存在；结构以 `database.py` 为准，数据范围需现场复核） |
| `README.md` | 系统总览、快速开始、命令、API、配置项（面向人类） |
| `docs/architecture/ARCHITECTURE.md` | 双概念编码体系、永久缓存语义、多周期融合、A股过滤、实时监控（含部分历史实现记录） |
| `docs/ops/DEPLOYMENT.md` | systemd 服务、外网访问、运维命令、故障排查 |
| `docs/reference/CHANGELOG.md` | 版本改动记录 |

## 开发与提交约束

- 以当前模块化单体为准：`main.py` 只装配；接口在 `api/routers/`；领域业务留在对应模块或包；SQL 只进 `database/`；前端是 `frontend/src/` 下的 Vue 3，不使用通用 `src/services` 或 React 骨架套改本项目。
- 依赖方向保持表现层 → 领域/业务层 → 数据与外部适配层；领域层不得反向导入 `api`。`opening_strength` 的额外边界由 `tests/test_layering.py` 机械检查。
- 新行为必须有测试，修 bug 必须先有回归测试。全量发现：`PYTHONPATH=. python -m unittest discover -s tests -t . -p 'test_*.py' -v`；文档：`python scripts/lint_docs.py` 和 `git diff --check`；前端：`cd frontend && npm ci && npm test -- --run && npm run type-check && npm run build`。
- 数据、token、日志、`static/` 构建产物和 `config_local.py` 不入 git；不要在输出、测试或提交中暴露密钥和原始上游响应。
- 提交使用 Conventional Commits：英文小写 type、可选英文 scope、中文具体描述；单次任务不超过 4 个逻辑提交，不添加 AI 署名。提交前核对暂存差异和测试证据。
- 不保留 `*_v2.py`、`*_new.py`、调试脚本或注释掉的旧实现。一次性验证放 gitignore 的临时目录，任务结束前清理；破坏性数据操作、停服、推送或发布按用户授权执行。
