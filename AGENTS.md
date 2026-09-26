# AGENTS.md

> 本文件供 AI 智能体快速了解本项目。读完本文件应能知道：项目是什么、怎么跑、代码在哪改、有哪些易踩的坑。深度内容见文末「深入阅读」指向的文档。

## 一句话

基于同花顺 iFinD API 的 **A股行业归因 + 板块强度检测** 系统。**仅处理沪深北交易所 A 股**（.SH/.SZ/.BJ），全链路过滤海外代码。输出：哪个板块最强、每只股票被哪个概念带涨。盘中实时监控基于 kline-fetcher 分时数据，**从 9:15 集合竞价即可开始**（ref_price 推算涨跌）。

## 当前状态（2026-07-24 核对）

- 观察池已覆盖 884/885/886；管理页通过 `POST /api/sector_manage/refresh` 启动后台刷新，并轮询 `/api/sector_manage/refresh/status`。无定时自动刷新。
- `watched_concepts` 保存管理页的持久化选择；现役有效范围 = 勾选集 ∩ 最新成分股数 10~500 的板块。realtime、daily 和 scan 统一读取该有效范围，不再经过盘前 watchlist。
- 板块监控只接受最新成分股数 **10~500（含边界）** 的概念；管理候选、保存接口及实时引擎均执行过滤，越界板块即使残留在旧 `watched_concepts` 数据中也不生效。
- 本机运行库的 `stock_concept_map` 当前为空（2026-07-24 实测）；实时监控不受影响，但下一次 daily 个股归因前必须重跑 init 映射流程，不能把历史 `stock_attribution` 行误认为映射仍就绪。
- 本机没有为本项目安装 daily crontab，`daily_kline` 最新日期为 20260717（2026-07-24 实测）；盘后数据是否补齐需显式运行 `main.py daily` 并复核，README/DEPLOYMENT 中的 crontab 只是建议配置。
- 板块字典（ths_concept_dict 710 个=881×90+884×230+885×293+886×97）由 `refresh-boards` 命令用 smart_stock_picking 动态枚举维护；881 二级行业仅入字典**不进观察池**（OBSERVE_CONCEPT_PREFIXES=884/885/886）。
- 强势归类选股走 REST `smart_stock_picking`（`ACCESS_TOKEN`，`ifind_client.smart_pick_stocks`），**不走 MCP**（MCP search_stocks 有每日配额且曾反复打满，已于 2026-09-07 彻底移除 MCP 链路：mcp_proxy.py 已删、IFIND_MCP_TOKEN 已清）。

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
| 数据库 | `data/sector_attribution.db`（SQLite，14 张现役表；本机退役 `watchlist` 已于 2026-07-24 删除） |
| 交易日历缓存 | `data/trade_calendar.txt`（`trade_calendar.py` 三级缓存的本地落盘，缺失会自动重建） |
| 服务器 / 部署 | **115.191.14.82:8000**；systemd 服务 `ifind-monitor`，一键装 `sudo bash install_service.sh`（详见 `docs/ops/DEPLOYMENT.md`） |

> ⚠️ 上述 `config_local.py` 已 gitignore，**勿提交、勿外传**（含真实 token）。跑命令前务必用上面的 conda python，否则缺 `fastapi`/`pandas`/`numpy`/`plotly` 等依赖；盘中实时链路还需 `kline-fetcher`。

## 入口命令（`main.py`）

| 命令 | 作用 | 备注 |
|---|---|---|
| `init` | 首次部署：拉字典+成分股+映射，补全概念板块全集 | 耗时较长（并发拉取全市场） |
| `daily --date YYYYMMDD` | 每日盘后：同步日K → 板块强度 → 个股归因 | 日期须为交易日；不传 `--codes` 自动反查全市场 |
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
| `core_calculator.py` | 板块强度 + 多周期融合 + L1 归因算法 | 中（改算法看这） |
| `stock_scorer.py` | 盘中成分股四维评分（涨幅/涨速/开盘至今涨幅/涨停）+ 涨速加速 | 低 |
| `realtime_engine.py` | 盘中实时引擎（分时序列缓存 + 时刻切片 + 内存计算，**不入库**） | 低 |
| `trade_calendar.py` | 交易日历模块（`TradeCalendar` 单例，三级缓存：内存→`data/trade_calendar.txt`→网络→DB 兜底；复用 `kline_fetcher.fetch_trade_calendar`） | 低 |
| `probe_auction.py` | 集合竞价数据探针脚本（生产环境验证 `pre_market` 形态用，非业务链路） | 低 |
| `api/` | **FastAPI 接口层（包）**：`app.py` 组装+SPA 入口、`deps.py` db 单例、`schemas.py` 请求模型、`routers/` 按域路由（overview/realtime/history/sector_manage/kg）、`history_service.py`+`kg_views.py` 编排下沉 | 中（加接口看 routers/） |
| `api_server.py` | 兼容入口（`from api import app`；uvicorn `"api_server:app"` 不变） | 低 |
| `sector_manage.py` | 监控板块管理：多周期涨幅计算（1d/3d/5d）+ 候选板块列表组装 | 中（改管理页看这） |
| `kg_sources.py` | 知识图谱数据源适配层（SourceAdapter 协议 + iFinD 接口2主源/接口1验证源两个 Adapter，未来加申万/问财只写新 Adapter） | 低 |
| `kg_builder.py` | 知识图谱构建：`kg_bootstrap`（幂等）+ 统计报告 + 周维护 `kg_update`（diff 状态机，旧态从边 confidence 恢复） | 中（改图谱构建/周维护看这） |
| `kg_analysis.py` | 知识图谱分析（P3）：`compute_corr_20d`（ρ 边权，先 join 后 tail 对齐）+ `detect_communities`（Louvain）+ `hub_sectors`/`linked_stocks`/`locate_sectors`（组合定位：一批股→板块富集/命中双指标）/`dedup_dashboard_sectors`（看板三层去重，分类快照有缓存） | 中（改图谱分析看这） |
| `frontend/` | **Vue 3 + Vite + TypeScript SPA**（源码）。`npm run build` → `static/`，FastAPI 托管。7 个 Tab（Hash 路由）：板块强度/自选分组/集合竞价/强势归类×2/监控板块管理/知识图谱（cytoscape 四视图：族群投影/个股星型/板块成分/组合定位）。结构详见 `docs/architecture/FRONTEND.md` | 中（改前端看这 + FRONTEND.md） |
| `static/` | 前端构建产物（FastAPI `mount('/static')` 托管；已 gitignore，勿手改） | — |
| `install_service.sh` / `ifind-monitor.service` | systemd 一键安装脚本 + 服务配置（绑 0.0.0.0:8000，Restart=always） | 低 |
| `main.py` | 命令入口（argparse 子命令） | 低 |

## 数据库（必读）

schema 权威来源：monitor 私有表看 `database/schema.py`（建表 DDL）与 `database/core.py::_init_db()`（迁移与种子），板块三表（`ths_concept_dict`/`stock_concept_map`/`concept_members`）看组件 `ifind_sector_hub/repositories/storage.py`（同一库文件）。本机若存在 **`data/DATABASE_MANIFEST.json`**，它是 gitignore 的运行库快照（行数、日期范围、样本和常见查询），查询本机数据库前应先读，但不能假设其他 checkout 一定存在或仍是最新。

9 张业务表：`ths_concept_dict` / `stock_concept_map` / `concept_members` / `daily_kline` / `min1_kline`（空）/ `concept_strength` / `stock_attribution` / **`custom_group`** / `watched_concepts`。另有 **知识图谱 5 表**：`kg_node`（股票+板块节点）/ `kg_edge`（双时态归属边，多源并存+confidence 分级+`corr_20d` ρ 边权列）/ `kg_snapshot`（版本快照）/ `kg_change`（变更日志）/ `kg_community`（Louvain 族群，P3）——设计见 `docs/architecture/DESIGN-knowledge-graph.md`。本机已删除退役 `watchlist`；其他未清理的旧数据库仍可能残留该历史表。

**最容易踩的坑**：
1. **日期格式跨表不一致** — `ths_concept_dict`/`stock_concept_map` 用 `YYYY-MM-DD`，其余表用 `YYYYMMDD`。跨表 JOIN 前必须格式归一，否则键对不上。
2. **永久缓存表** — `stock_concept_map`/`concept_members`/`ths_concept_dict` 查询时不传日期默认取 `MAX(date)` 最新快照，与 init/daily 日期解耦。这是设计而非 bug。
3. **`daily_kline.volume`/`amount` 普遍为 NULL**（接口未拉取），不要假设非空。
4. **`stock_attribution.attribution_json` 内含 JSON `NaN`**（非标准 JSON），`json.loads` 能解析，其他解析器需容错。
5. **`concept_strength.score_final`/`rank_1d` 跨日不可直接比**（每日独立 Z-score 标准化），比较强弱只在同一 `calc_date` 内有意义。

代码访问数据库统一走 `database` 包的 `class Database`（`from database import Database` 不变），`with self._connect() as conn` 上下文管理（自动 commit/rollback）。

**分层约束（机器可查）**：`tests/test_layering.py` = import-linter 契约（`.importlinter.ini`，管 api/database 包边界）+ AST 检查（管平铺模块：引擎不碰接口层 / config 叶子 / 计算层纯净 / api_server 只组装）。改完分层相关代码跑 `python -m unittest tests.test_layering`。

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
- **Vue SPA 多 Tab**：根路由 `/` 返回 Vue SPA（`static/index.html`，Hash 路由），7 个 Tab（板块强度/自选分组/集合竞价/强势归类×2/监控板块管理/知识图谱）在前端切换，`<keep-alive>` 保留各页状态。`DashboardPage` 按 `route.name` 复用（sector/custom）；`ScanPage` 同理（scan/market_scan）。
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

<!-- agent-config:architecture v1.1 begin -->
## 架构与目录规范（强制遵守）

> 本规范适用于本项目所有代码生成、修改与重构任务，优先级高于默认习惯。
> 核心思想：目录树就是架构说明——先看目录，再写代码。

### 〇、任务开始前

- 涉及新建文件/模块的任务：动手前先用 2-3 行说明每个新文件将放在哪里（依据第四节决策表），再继续
- 发现存量代码违反本规范：本次任务内只修自己触碰的文件，不顺手大重构；大范围重构先提方案
- 用户指令与本规范冲突时：指出冲突点请用户决策，默认遵守本规范

### 一、五条铁律

1. **入口只装配**：`main.py` 只做配置加载、路由注册、应用启动，一行业务不写
2. **依赖单向**：表现层 → 业务层 → 数据层；反向、跨层、循环依赖一律禁止
3. **配置单一出口**：`core/config.py` 是唯一读 `.env` 的模块，其他模块只从 config 导入
4. **测试镜像源码**：`tests/` 目录结构与 `src/` 一一对应
5. **高内聚**：一个需求只改一个目录；若改动必然散落 3 个以上目录，停下向用户确认架构

### 二、目录结构（标准骨架）

后端（Python 模块化单体）：

```text
src/<app>/
├── main.py                # 入口：只装配
├── core/                  # 配置、日志、异常、安全
├── api/v1/                # 表现层：路由 + 参数校验
├── schemas/               # 请求/响应 DTO（与 ORM 解耦）
├── services/              # 业务层：规则与事务编排（核心资产）
├── repositories/          # 数据层：ORM + 查询（SQL 只出现在这）
├── integrations/          # 外部系统适配（行情源、iFinD、消息队列）
└── utils/                 # 真正通用的纯函数（保持小）
tests/                     # 镜像 src/
```

前端（React，按功能域组织）：

```text
web/src/
├── app/                   # 装配：路由表、Provider、布局
├── features/<域>/         # 域私有组件/api/types 都在该域目录内
├── components/            # 跨域通用组件（≥2 域使用才升级为通用）
├── lib/                   # axios 实例、通用 hooks、工具
└── styles/
```

前后端同仓（monorepo）：

```text
├── apps/api/              # 后端
├── apps/web/              # 前端
└── packages/shared/       # 前后端共享的类型契约、常量、枚举（单点维护）
```

### 三、依赖方向（红线）

允许的依赖（→ 表示"可以 import"）：

```text
api/v1 → services → repositories
services → integrations
api、services → schemas、utils、core
utils、core →（不依赖任何业务模块，是叶子）
app/ → features/ → components/、lib/
apps/* → packages/shared
```

禁止的依赖（出现即架构已坏，停止写码并报告，不许打补丁绕过）：

- `repositories` / `schemas` / `integrations` 反向 import `services` 或 `api`
- `integrations` import 业务模块（适配层不知道业务）
- `utils` import 任何业务层
- 任何模块 import `api`
- `features/<域A>` import `features/<域B>`（需要共享时下沉到 components/lib，或提出来）
- `apps/api` 与 `apps/web` 互相引用
- 任何形式的循环依赖

### 四、新代码放置决策表

| 要创建的内容 | 放置位置 | 准入/说明 |
|---|---|---|
| 新接口/路由 | `api/v1/<域>.py` | 只做参数校验与调用 service；不写业务、不写 SQL |
| 业务规则/流程编排 | `services/<域>.py` | 事务边界在这层 |
| SQL/ORM 查询 | `repositories/<域>.py` | SQL 只许出现在这 |
| 请求/响应模型 | `schemas/` | 禁止 ORM 模型直接当响应 |
| 外部系统封装 | `integrations/<系统>/` | 全仓唯一一份，禁止复制到业务目录 |
| 通用纯函数 | `utils/` | 无状态、无业务语义、≥2 处使用；否则放域内 |
| 前端新页面/功能 | `features/<域>/` | 域内组件/接口/类型不出域 |
| 跨域通用组件 | `components/` | ≥2 个域使用才放这 |
| 一次性验证脚本 | `scratch/`（gitignore） | 任务结束删除，禁止入库 |
| 数据文件/中间产物 | `data/`（gitignore） | 永不入 git |
| 多步流程/检查清单 | `.claude/skills/<name>/SKILL.md` | 不要塞进本文件 |
| 常驻事实类新规则 | `AGENTS.md`（走 PR） | 不要散落在代码注释里 |

### 五、版本与演进（git tag 管理法）

核心心智：**git 历史是博物馆，工作区是车间；版本号活在参数组、spec 文档和 tag 里，不活在文件名里**。

1. **结构性演进前先 tag**：删除或重构任何"曾经是生产口径"的代码前，先打 `<ver>-frozen` tag（如 `v3-frozen`、`prod-v43`）并推送——旧版本永远可 `git checkout <tag>` 复现，工作区留副本不增加任何可复现性，只增加混乱
2. **新版本 = 参数组 + 文档 + runner，不是新代码文件**：算法调优优先通过 config 的版本化参数组（如 `V41_*` / `V43_*`）实现；禁止为版本差异新建引擎文件（`v2.py`/`v5.py`/`xxx_new.py`）。引擎不带版本名，参数组带版本名
3. **三生命周期判定**：
   - 冻结的验证轨（预注册、评价期禁改参）：**不许动**，即使含有已退役标的
   - 已完成使命的实验（结论已冻结在 docs/ 或 docstring）：**tag 后删除** runner，删除须用户确认并在提交说明里注明 tag 名与结论文档链接
   - 现行生产：**去版本化重构后长住工作区**，入口 docstring 标明现行口径与 spec 链接
4. **死文件的真实代价**：死代码进 code graph 索引和 grep 结果，AI 可能用退役口径作答；每留一份旧文件，未来复制漂移的概率就多一分
5. **周期清理**：验证轨裁决结束后，清理对应冻结 runner 与退役参数组

### 六、禁止事项（反模式，出现即返工）

1. **utils/common 黑洞**：什么都往里塞。准入见决策表
2. **胖控制器**：业务写在路由里
3. **ORM 模型直接当响应**：内部结构泄漏，改表就炸接口
4. **平行实现**：复制已有函数微调改名。新增前必须先全仓搜索是否已有实现
5. **过早微服务**：默认模块化单体；拆服务是规模倒逼的结果
6. **临时残留**：debug 输出（print/console.log）、注释掉的旧代码、无 issue 的 TODO
7. **测试后补/自证**：修 bug 先写失败测试；不要在同一轮生成里同时写测试和实现再让测试迁就实现
8. **版本化文件名**：`xxx_v2.py`、`xxx_new.py`、`xxx_final.py`——版本语义应落在 tag/参数组/文档，见第五节

### 七、配置、数据与环境

- `.env` 不入 git；环境差异全部走环境变量，由 `core/config.py` 统一读取
- `data/`、`scratch/`、构建产物不入 git
- 密钥不硬编码；新依赖先征得用户同意

### 八、测试要求

- 新增行为必须有对应测试；bug 修复附带回归测试
- `tests/` 结构镜像 `src/`：找到被测文件就能找到测试
- 提交前测试套件全绿，并附运行输出作为证据

### 九、完成定义（每次任务收尾自查）

- [ ] 新文件位置符合决策表，依赖方向无反向/跨层
- [ ] 无临时脚本、调试输出、注释掉的旧代码残留
- [ ] 新增行为有测试；测试全绿（附输出）
- [ ] `scratch/` 已清理，`data/` 未入库
- [ ] 提交遵循 Conventional Commits，一次提交只做一件事

<!-- agent-config:architecture v1.1 end -->

<!-- agent-config:commit v1.1 begin -->
## Git Commit 规范（强制遵守）

### 〇、生成 commit 前自查（提交前三问）

1. **哪里坏了 / 缺什么？**（why——这是 body 的主要内容）
2. **最小正确改动是什么？**（what——决定暂存区该装什么）
3. **这个改动值得独立成 commit，还是该并入已有暂存？**（粒度——防碎片化）

**粒度规则**：
- 一次任务产生的 commit 数 ≤4；超过说明粒度错了，合并
- 一个 commit = 一个可独立理解的逻辑改动；"改实现 + 补测试"是**一个** commit，不拆
- 禁止"修个 typo 再来一个 commit"式尾巴提交——并入相关提交
- `wip:` 前缀只允许在个人分支；合入主干前必须 squash（PR 用 Squash and merge）

### 一、格式规范（Conventional Commits）

```text
<type>(<scope>): <中文描述，≤50 字，不加句号>          ← 首行，总长 ≤72 字符

[空行]
<body：解释 why 而非 what；每行 ≤72 字符；可省略但 feat/fix/refactor 强烈建议写]

[空行]
[footer：BREAKING CHANGE: <描述> | Refs: #issue | 关联 tag：v3-frozen]
```

规则要点：
- **type 英文小写，scope 英文小写，description 中文**（工具可解析 + 人易读写）
- type 与描述之间：英文半角冒号 + 一个空格，缺一不可（工具会报错）
- 描述用祈使句口吻、陈述事实：写"添加 X"不写"我添加了 X"、"修复了 Y"（自检句式："如果应用此提交，它将……"）
- 破坏性变更两种标记等价：`feat(api)!: ...` 或 footer 写 `BREAKING CHANGE: ...`（后者必须全大写）
- 同一改动涉及多类型时，取**最主要**的类型；宁可 body 里补充说明，不拆碎片提交

### 二、type 速查表

| type | 用途 | 示例 |
|---|---|---|
| `feat` | 新功能 | `feat(nlq): 添加链内排行查询` |
| `fix` | 修 bug（含实验结论修正） | `fix(minute): 修正 5min 重排的时区偏移` |
| `perf` | 性能优化（行为不变） | `perf(etl): 批量读取替代逐行查询` |
| `refactor` | 结构重构（行为不变） | `refactor: v3.py 拆分为 signals/engine` |
| `docs` | 文档、注释 | `docs: 冻结 v4.1 结论文档` |
| `test` | 测试（补测试、修测试） | `test: 补 signal_daily 幂等性测试` |
| `build` | 构建/依赖 | `build: requirements 增加 tenacity` |
| `ci` | CI 配置 | `ci: 增加 commitlint 校验工作流` |
| `chore` | 杂务（日志、配置清理） | `chore: 清理临时产物` |
| `revert` | 回滚（footer 注明被回滚的 hash） | `revert: 回滚动态半衰期（Refs: 676104e）` |
| `style` | 纯格式（不改语义） | `style: ruff format 全量格式化` |

scope 惯例：用模块名；跨模块省略 scope。

### 三、AI 专属规则（硬性）

1. **禁止任何 AI 署名**：不得添加 `Co-Authored-By: Claude`、`Generated with [...]` 等标识
2. **禁止黑盒动词**：`update code`、`fix stuff`、`修改了一些问题`、`wip`（个人分支除外）——描述必须具体到可检索
3. **message 依据事实生成**：以 `git diff --staged` 实际内容 + 测试输出为准，**不描述"打算做的"**
4. **生成后先展示再执行**：AI 生成 message 后展示给用户确认，不直接 commit（除非用户明确放行）
5. **提交前测试**：`feat`/`fix` 提交前测试套件必须全绿，message body 附测试结果摘要

### 四、研究型项目扩展规则

实验类项目的 commit 语义分层（与「版本与演进」节衔接）：

| 场景 | 写法 | 说明 |
|---|---|---|
| 实验调参试错 | **不进 git**（本地 runner / 个人分支 `wip:`） | 试错留在实验层，历史只记结论 |
| 参数组定稿 | `feat(config): 新增 V44_ANCHOR_POOL 参数组` | 参数组带版本号，进主干 |
| 实验结论冻结 | `docs: 冻结 v4.1 OOS 结论（样本外 −2.3%）` + **打 tag `v41-frozen`** | 结论与代码状态用 tag 绑定 |
| 数据快照更新 | `chore(data): 同步 2026-09 快照` | 大数据不入 git，快照靠缓存层 |
| 删除退役 runner | `chore: 移除 backtest_v3（tag v3-frozen 可复现）` | footer 或 body 注明 tag 名 |

<!-- agent-config:commit v1.1 end -->
