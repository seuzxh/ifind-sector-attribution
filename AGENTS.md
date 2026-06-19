# AGENTS.md

> 本文件供 AI 智能体快速了解本项目。读完本文件应能知道：项目是什么、怎么跑、代码在哪改、有哪些易踩的坑。深度内容见文末「深入阅读」指向的文档。

## 一句话

基于同花顺 iFinD API 的 **A股行业归因 + 板块强度检测** 系统。**仅处理沪深北交易所 A 股**（.SH/.SZ/.BJ），全链路过滤海外代码。输出：哪个板块最强、每只股票被哪个概念带涨。盘中实时监控基于 kline-fetcher 分时数据，**从 9:15 集合竞价即可开始**（ref_price 推算涨跌）。

## 运行环境（关键，别猜）

| 项 | 值 |
|---|---|
| 项目根目录 | `/root/projects/2.monitor_940/ifind-sector-attribution` |
| Python | conda 环境 **`vibe-trading`**：`/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python` |
| 工作目录约定 | 所有命令须在本项目根目录执行（`config_local.py`、`data/` 均为相对路径），跑 main.py 需 `PYTHONPATH=.` |
| iFinD token | `ACCESS_TOKEN` / `REFRESH_TOKEN`：读 `config_local.py` 或环境变量 `IFIND_ACCESS_TOKEN` / `IFIND_REFRESH_TOKEN` |
| **分时数据依赖** | **`kline-fetcher` 本地包（不在 PyPI，须单独装）**：`pip install -e /root/Projects/kline-fetcher`，或 `pip install git+https://github.com/seuzxh/kline-fetcher.git` |
| **kline API 地址** | `KLINE_API_BASE_URL`（中焯行情 API，盘中实时监控用）：配在 `config_local.py` 或环境变量，**不配则实时链路不可用** |
| 数据库 | `data/sector_attribution.db`（SQLite，~92MB，9 张表） |
| 交易日历缓存 | `data/trade_calendar.txt`（`trade_calendar.py` 三级缓存的本地落盘，缺失会自动重建） |
| 服务器 / 部署 | **115.191.14.82:8000**；systemd 服务 `ifind-monitor`，一键装 `sudo bash install_service.sh`（详见 `docs/DEPLOYMENT.md`） |
| **AI 问答 LLM** | `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（火山方舟 Coding Plan）：base_url **必须用 `/api/coding/v3`**（`/api/v3` 不消耗 Plan 额度会产生额外费用）。配在 `config_local.py` 或环境变量，不配则 AI 问答降级为手动选工具模式。文档 `https://www.volcengine.com/docs/82379/1928261` |
| **AI 问答 MCP** | `IFIND_MCP_TOKEN`（iFinD MCP server 的 JWT 鉴权）：配在 `config_local.py` 或环境变量，不配则 AI 问答无法调 iFinD 工具 |
| **可用模型** | Coding Plan 白名单 10 个（`llm_agent._CODING_PLAN_MODELS`）：doubao-seed-2.0-pro/code/lite、doubao-seed-code、minimax-latest、glm-latest、deepseek-v4-flash/pro、kimi-k2.6/k2.7-code。页面下拉框运行时可切 |

> ⚠️ 上述 `config_local.py` 已 gitignore，**勿提交、勿外传**（含真实 token）。跑命令前务必用上面的 conda python，否则缺 `fastapi`/`pandas`/`numpy`/`plotly` 等依赖；盘中实时链路还需 `kline-fetcher`。

## 入口命令（`main.py`）

| 命令 | 作用 | 备注 |
|---|---|---|
| `init` | 首次部署：拉字典+成分股+映射，补全概念板块全集 | 耗时较长（并发拉取全市场） |
| `daily --date YYYYMMDD` | 每日盘后：同步日K → 板块强度 → 个股归因 | 日期须为交易日；不传 `--codes` 自动反查全市场 |
| `prescreen --date YYYYMMDD` | 盘前筛选：5日涨幅选 top 板块+成分股，写入 `watchlist` | 可 `--top-sector` / `--top-stock` 调数量 |
| `server [--host H] [--port P] [--reload]` | 启动 FastAPI（API + 可视化看板），默认 `0.0.0.0:8000` | 生产用 systemd，调试加 `--reload` |
| `import-groups [--json FILE]` | 导入同花顺自选股分组 JSON → `custom_group` 表（幂等覆盖） | 默认读 `ths-custom-block-data/同花顺自选分组导出.json`；自动过滤指数/ETF/可转债等非 A 股标的 |
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
| `trade_calendar.py` | 交易日历模块（`TradeCalendar` 单例，三级缓存：内存→`data/trade_calendar.txt`→网络→DB 兜底；复用 `kline_fetcher.fetch_trade_calendar`） | 低 |
| `probe_auction.py` | 集合竞价数据探针脚本（生产环境验证 `pre_market` 形态用，非业务链路） | 低 |
| `api_server.py` | FastAPI 服务（REST API + 可视化页面 + AI 问答 SSE 路由） | 中（加接口看这） |
| `llm_agent.py` | AI 问答的"大脑"（火山方舟 Coding Plan，OpenAI 兼容；静态 10 模型白名单 `_CODING_PLAN_MODELS`；运行时可切模型） | 低 |
| `mcp_proxy.py` | iFinD MCP 客户端代理（hexin-ifind-ds-stock-mcp / -index-mcp，JWT 鉴权） | 低 |
| `templates/tabs.html` | **顶层 Tab 容器**（[📊板块强度]/[⭐自选分组]/[🎯强势归类]/[💬AI问答] iframe，状态完全隔离） | 低 |
| `templates/index.html` | 看板单页（`?board=sector`/`=custom`/`=scan` 复用同模板；时间滑块 + 播放 + 3s 轮询 + 加速列 + **持仓金色标注** + **强势归类手风琴**） | 低 |
| `templates/chat.html` | AI 问答页面（自然语言查行情，模型下拉框 + SSE 流式） | 低 |
| `install_service.sh` / `ifind-monitor.service` | systemd 一键安装脚本 + 服务配置（绑 0.0.0.0:8000，Restart=always） | 低 |
| `main.py` | 命令入口（argparse 子命令） | 低 |

## 数据库（必读）

完整结构见 **`data/DATABASE_MANIFEST.json`**（机器可读，包含每张表的列定义、行数、日期范围、样本数据、常见查询 SQL、caveats）。智能体查询数据库前应先读这个文件。

9 张表：`ths_concept_dict` / `stock_concept_map` / `concept_members` / `daily_kline` / `min1_kline`（空）/ `concept_strength` / `stock_attribution` / `watchlist` / **`custom_group`**（自选股分组，`import-groups` 导入）。

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

## 盘中实时链路补充（易忽略）

- **集合竞价也能监控（9:15~9:25）**：此阶段 `trading` 为空，但 `pre_market` 有逐点 `ref_price`（3 秒一点，~201 点）。`realtime_engine._build_indicator_df` 按两阶段分支：集合竞价**只用末点 ref_price 算涨幅**，`speed/body/acceleration` 置 0；进度条 `available_times` 含 09:15~09:25 点，**自动从 09:15 起**。
- **trading 切片严格按 snapshot_time 过滤，不兜底回退**（否则 9:20 会误用 9:30 数据）。
- **交易时段由服务端 `session_phase` 决定**（`trade_calendar.py`，7 个 phase：`pre_open`/`auction`/`pre_morning`/`morning`/`lunch`/`afternoon`/`closed`）。前端仅 `<9:15(pre_open)` 和非交易日停 3s 轮询，**收盘后 `closed` 仍轮询**展示全天数据供回看。
- **历史日期回看 ≠ 历史看板**：实时接口传 `trade_date=YYYYMMDD` 走分时链路（拉该日全天分时 + 内存切片）；`/api/history/dashboard` 读已入库的 `concept_strength`（降级为纯涨幅排序）。两条路径别混。
- **自选股分组看板**：`GET /api/custom/dashboard` 用 `custom_group` 表替代概念板块算分组强弱，复用 realtime_engine 的缓存/切片（仅 `members_map` 来源不同）。需先用 `import-groups` 导入分组。
- **双看板 Tab 隔离**：根路由 `/` 返回 `tabs.html`（顶层 Tab 容器），内嵌两个 iframe：`/?board=sector`（板块强度）和 `/?board=custom`（自选分组）。两 iframe 各自独立 JS 环境，状态完全隔离（模式/时间条/播放/autoFollow 互不影响）。`index.html` 据 `?board` 参数切换数据源与标题。
- **时间条播放**：`togglePlay` 用 `setInterval` 逐分钟推进滑块（`stepPlay` → `refresh`），速度 1.5x/2x/4x/8x。切模式/切日期/拖滑块/点"回到最新"自动 `stopPlay`。播放时 `autoFollow=false`（否则 3s 轮询拉回最新）。`refreshSeq` 序号守卫防异步乱序覆盖。

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
  - **请求序号守卫** `refreshSeq`：每次 refresh 前 `++seq`，响应回来若过期则丢弃——防止播放/轮询异步乱序覆盖界面
- **持仓醒目标注**（自选看板专属）：`HOLDING_GROUP_NAME="CC"` 识别持仓分组，其成分股作持仓股。含持仓的分组返回 `holding_in_group`，前端金色高亮（排行表行+卡片描边+持仓个股行+持仓标签）。仅 `isCustomBoard` 生效。
- **最小成分股数** `MIN_MEMBER_COUNT=6`，低于此的概念不参与排名（样本过小 Z-score 失真）。

## REST API（`api_server.py`，默认 `0.0.0.0:8000`）

| 接口 | 方法 | 说明 |
|---|---|---|
| `GET /` | — | **顶层 Tab 容器**（`tabs.html`）；带 `?board=sector`/`=custom` 时返回 iframe 内页（`index.html`） |
| `GET /api/sector/rankings` | — | 板块强度排名（含多周期融合分） |
| `POST /api/attribution/stock` | — | 个股多概念归因 |
| `POST /api/attribution/portfolio` | — | 组合归因 + 强势板块定位 |
| `GET /api/realtime/dashboard` | — | **板块实时看板**（分时切片，`trade_date`/`snapshot_time`/`watchlist_mode`） |
| `GET /api/custom/dashboard` | — | **自选分组看板**（`custom_group` 替代概念板块，复用实时切片，返回 `holding_stocks`/`holding_in_group`） |
| `POST /api/realtime/clear_cache` | — | 清空分时序列缓存（切日/调试用） |
| `GET /api/history/dashboard` | — | **历史看板**（指定日期，读入库 `concept_strength`，降级纯涨幅） |
| `GET /api/trade_calendar` | — | 交易日列表（供前端日期选择器过滤非交易日） |
| `GET /api/session_status` | — | 交易时段状态（盘前/盘中/盘后，前端据此控制轮询） |
| `POST /api/prescreen` | — | 盘前筛选（5日涨幅选板块+成分股 → `watchlist`） |
| `GET /api/watchlist` | — | 读当日 watchlist |
| `GET /api/dates` | — | 已入库的板块强度日期列表 |
| `GET /api/concept/list` | — | 全部 A 股概念板块列表 |
| `GET /api/concept/members` | — | 概念成分股（`date` 不传则取最新缓存） |
| `GET /api/trade_calendar?year=YYYY` | — | 交易日列表（前端日期选择器过滤非交易日用） |
| `GET /api/session_status` | — | 当前交易时段状态（`is_trading_day`/`phase`/`next_open_time`/`next_trade_day`，前端盘前判断用） |

## 常见任务 → 怎么做

| 想做的事 | 怎么做 |
|---|---|
| 加一个 API 接口 | 在 `api_server.py` 加路由；数据查询走 `database.py` |
| 改板块强度算法 | `core_calculator.py`（`calc_all_sectors_strength` / `calc_multi_period_score`） |
| 改某个表的字段 | 改 `database.py` 建表 + 读写方法，**同步更新 `data/DATABASE_MANIFEST.json`** |
| 加新概念分类 | `config.ALL_CONCEPT_CODES` 加码 → 重跑 `init` 的 `init_concept_universe` |
| 查数据库结构/样本/查询模板 | 读 `data/DATABASE_MANIFEST.json`，别猜 |
| 盘中实时拉取失败 / `ImportError: kline_fetcher` | 检查 kline-fetcher 是否 `pip install -e` 装好 + `KLINE_API_BASE_URL` 是否配置 |
| 接入自选股分组监控 | `main.py import-groups` 导入 JSON → 调 `GET /api/custom/dashboard` |
| 改盘前筛选/时段判定 | `prescreen.py`（筛选）/ `trade_calendar.py`（`session_phase`、交易日历） |
| 改持仓分组（自选看板金色标注） | `config.HOLDING_GROUP_NAME` 改分组名（默认 "CC"），无需改代码 |
| 改双看板（Tab 隔离） | `templates/tabs.html`（容器）/ `templates/index.html`（`?board=sector`/`=custom` 复用同模板） |
| 时间条播放异常（时刻跳变） | 检查 `refreshSeq` 请求序号守卫是否被破坏（防异步乱序覆盖） |
| 改 AI 问答模型 / 加模型 | `llm_agent._CODING_PLAN_MODELS` 白名单（静态 10 个）；或 `config.LLM_MODEL` 改默认；页面下拉框运行时切。**base_url 必须用 `/api/coding/v3`** |
| AI 问答报错 / 不调工具 | 检查 `LLM_API_KEY` + `IFIND_MCP_TOKEN` 是否配在 `config_local.py`；看 `/api/llm/models` 是否返回模型列表 |

## 深入阅读

| 文档 | 内容 |
|---|---|
| `data/DATABASE_MANIFEST.json` | 数据库结构清单（机器可读，列定义/行数/日期范围/样本/常见 SQL/caveats） |
| `README.md` | 系统总览、快速开始、命令、API、配置项（面向人类） |
| `docs/ARCHITECTURE.md` | 双概念编码体系、永久缓存语义、多周期融合、A股过滤、实时监控、盘前筛选（设计决策） |
| `docs/DEPLOYMENT.md` | systemd 服务、外网访问、运维命令、故障排查 |
| `docs/CHANGELOG.md` | 版本改动记录 |
