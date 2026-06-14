# AGENTS.md

> 本文件供 AI 智能体快速了解本项目。读完本文件应能知道：项目是什么、怎么跑、代码在哪改、有哪些易踩的坑。深度内容见文末「深入阅读」指向的文档。

## 一句话

基于同花顺 iFinD API 的 **A股行业归因 + 板块强度检测** 系统。**仅处理沪深北交易所 A 股**（.SH/.SZ/.BJ），全链路过滤海外代码。输出：哪个板块最强、每只股票被哪个概念带涨。

## 运行环境（关键，别猜）

| 项 | 值 |
|---|---|
| 项目根目录 | `/root/projects/2.monitor_940/ifind-sector-attribution` |
| Python | conda 环境 **`vibe-trading`**：`/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python` |
| 工作目录约定 | 所有命令须在本项目根目录执行（`config_local.py`、`data/` 均为相对路径） |
| token | 读 `config_local.py`（`ACCESS_TOKEN` / `REFRESH_TOKEN`，已 gitignore，**勿提交、勿外传**） |
| 数据库 | `data/sector_attribution.db`（SQLite，~92MB） |

跑命令前请用上面那个 conda python，否则缺 `fastapi`/`pandas`/`numpy`/`plotly` 等依赖。

## 入口命令（`main.py`）

| 命令 | 作用 | 备注 |
|---|---|---|
| `init` | 首次部署：拉字典+成分股+映射，补全概念板块全集 | 耗时较长（并发拉取全市场） |
| `daily --date YYYYMMDD` | 每日盘后：同步日K → 板块强度 → 个股归因 | 日期须为交易日；不传 `--codes` 自动反查全市场 |
| `prescreen --date YYYYMMDD` | 盘前筛选：5日涨幅选 top 板块+成分股，写入 `watchlist` | 可 `--top-sector` / `--top-stock` 调数量 |
| `server` | 启动 FastAPI（API + 可视化看板），默认 `0.0.0.0:8000` | 生产用 systemd，调试加 `--reload` |
| `purge [--vacuum]` | 删除海外数据，仅留 A 股 | **破坏性**：执行前备份数据库；幂等可重跑 |
| `test` | 测试 5 个 iFinD 接口连通性 | — |

## 代码地图（改东西先看这里）

| 文件 | 职责 | 改动频率 |
|---|---|---|
| `config.py` | token、概念代码、计算权重（SCORE_WEIGHTS / PERIOD_WEIGHTS）、A股过滤白名单、分时数据源配置（KLINE_API_BASE_URL / INTRADAY_*） | 偶尔 |
| `config_local.py` | 本地 token + KLINE_API_BASE_URL，**已 gitignore** | — |
| `ifind_client.py` | iFinD 5 个接口封装（指数退避重试、批量分片） | 低 |
| `intraday_fetcher.py` | 分时数据批量并发封装（kline-fetcher TrendFetcher，32线程），盘中实时用 | 低 |
| `database.py` | SQLite 封装；所有表的读写方法都在这 | 中 |
| `sync_pipeline.py` | 数据同步与计算管线（init/daily 编排） | 中 |
| `core_calculator.py` | 板块强度 + 多周期融合 + L1 归因算法 | 中（改算法看这） |
| `prescreen.py` | 盘前筛选 | 低 |
| `stock_scorer.py` | 盘中成分股四维评分（涨幅/涨速/开盘至今涨幅/涨停）+ 涨速加速 | 低 |
| `realtime_engine.py` | 盘中实时引擎（分时序列缓存 + 时刻切片 + 内存计算，**不入库**） | 低 |
| `api_server.py` | FastAPI 服务（REST API + 可视化页面） | 中（加接口看这） |
| `templates/index.html` | 可视化看板单页（时间滑块 + 3s 轮询 + 加速列） | 低 |
| `main.py` | 命令入口（argparse 子命令） | 低 |

## 数据库（必读）

完整结构见 **`data/DATABASE_MANIFEST.json`**（机器可读，包含每张表的列定义、行数、日期范围、样本数据、常见查询 SQL、caveats）。智能体查询数据库前应先读这个文件。

8 张表：`ths_concept_dict` / `stock_concept_map` / `concept_members` / `daily_kline` / `min1_kline`（空）/ `concept_strength` / `stock_attribution` / `watchlist`。

**最容易踩的坑**：
1. **日期格式跨表不一致** — `ths_concept_dict`/`stock_concept_map` 用 `YYYY-MM-DD`，其余表用 `YYYYMMDD`。跨表 JOIN 前必须格式归一，否则键对不上。
2. **永久缓存表** — `stock_concept_map`/`concept_members`/`ths_concept_dict` 查询时不传日期默认取 `MAX(date)` 最新快照，与 init/daily 日期解耦。这是设计而非 bug。
3. **`daily_kline.volume`/`amount` 普遍为 NULL**（接口未拉取），不要假设非空。
4. **`stock_attribution.attribution_json` 内含 JSON `NaN`**（非标准 JSON），`json.loads` 能解析，其他解析器需容错。
5. **`concept_strength.score_final`/`rank_1d` 跨日不可直接比**（每日独立 Z-score 标准化），比较强弱只在同一 `calc_date` 内有意义。

代码访问数据库统一走 `database.py` 的 `class Database`，`with self._connect() as conn` 上下文管理（自动 commit/rollback）。

## 不可违反的约束

- **A 股范围限定是硬约束**：个股代码后缀只认 `.SH/.SZ/.BJ`，概念代码前缀只认 `700/881/883/884/885/886`。判定用 `config.is_a_share_code()` / `config.is_a_share_concept()`，不要自己写正则。
- **海外前缀** `861/864/865/871/875` 是美股/港股行业指数，会污染股票池，必须排除。
- **双概念编码体系**：行业码（700xxx/881xxx，来自 `config.ALL_CONCEPT_CODES`）与概念板块码（885xxx/886xxx，来自接口1）**交集为 0**。归因链路靠 `init_concept_universe` 补全后者后才能 JOIN 打通。详见 ARCHITECTURE.md §1。
- **日期须为交易日**：`daily --date` 传非交易日会因当日无数据返回空。

## 两套数据链路

| 维度 | daily（盘后，入库） | realtime（盘中，仅内存） |
|---|---|---|
| 数据源 | 接口3 日K | **kline-fetcher 分时数据**（中焯 API，非 iFinD） |
| 板块强度 | 多周期融合（1d/5d/20d） | 仅 1d 实时强度（切片末点涨幅） |
| 成分股排名 | L1 归因（贡献占比） | 四维加权评分（涨幅/涨速/开盘至今涨幅/涨停） |
| 缓存 | 持久化 `concept_strength` / `stock_attribution` | 分时序列内存缓存（TTL 5s，历史日期全天缓存） |
| 拉取范围 | 全市场 A 股 | **默认 watchlist 聚焦**（~279 只，1.5s） |

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
- **最小成分股数** `MIN_MEMBER_COUNT=6`，低于此的概念不参与排名（样本过小 Z-score 失真）。

## 常见任务 → 怎么做

| 想做的事 | 怎么做 |
|---|---|
| 加一个 API 接口 | 在 `api_server.py` 加路由；数据查询走 `database.py` |
| 改板块强度算法 | `core_calculator.py`（`calc_all_sectors_strength` / `calc_multi_period_score`） |
| 改某个表的字段 | 改 `database.py` 建表 + 读写方法，**同步更新 `data/DATABASE_MANIFEST.json`** |
| 加新概念分类 | `config.ALL_CONCEPT_CODES` 加码 → 重跑 `init` 的 `init_concept_universe` |
| 查数据库结构/样本/查询模板 | 读 `data/DATABASE_MANIFEST.json`，别猜 |

## 深入阅读

| 文档 | 内容 |
|---|---|
| `data/DATABASE_MANIFEST.json` | 数据库结构清单（机器可读，列定义/行数/日期范围/样本/常见 SQL/caveats） |
| `README.md` | 系统总览、快速开始、命令、API、配置项（面向人类） |
| `docs/ARCHITECTURE.md` | 双概念编码体系、永久缓存语义、多周期融合、A股过滤、实时监控、盘前筛选（设计决策） |
| `docs/DEPLOYMENT.md` | systemd 服务、外网访问、运维命令、故障排查 |
| `docs/CHANGELOG.md` | 版本改动记录 |
