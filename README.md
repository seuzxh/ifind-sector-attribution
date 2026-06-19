# iFinD 行业归因与板块强度检测系统

基于同花顺 iFinD API 的量化**行业归因 + 板块强度检测**系统。用同花顺概念板块（行业分类 + 概念板块双体系）作为分类标准，自动判定"哪只股票被哪个概念带涨"以及"当前哪个板块最强"。**仅处理 A 股**（沪深北交易所）。

## 核心功能

- **板块强度检测**：三维评分（涨幅强度 S1 / 上涨广度 S2 / 相对强度 S4）+ Z-score 标准化排名，支持 **1d / 5d / 20d 多周期融合**
- **个股多概念归因**：L1 权重归因法（`weight × concept_return`），分解个股涨幅对各概念的贡献
- **组合归因分析**：按持仓组合的市值暴露，匹配当前强势板块并预警
- **A股范围限定**：全链路过滤海外代码，只处理沪深北交易所股票
- **盘中实时监控（多看板 Tab）**：基于分时数据（kline-fetcher）的可视化网站
  - **板块强度看板**：3s 轮询刷新板块强度 + 成分股四维评分排名
  - **自选分组看板**：导入同花顺自选股分组 JSON，监控自定义分组的强弱，含持仓分组（CC）金色醒目标注
  - **强势股归类**：按筛选条件（涨幅/成交额/实体涨幅）扫描自选股池，按分组归类统计命中，发现哪个分组批量冒强势股
  - **全市场强势归类**：自然语言选股（iFinD MCP `search_stocks`，4 组预置 + 自定义条件可存/重命名）→ 按 884 概念板块归类，发现全市场强势股集中在哪些行业
  - **AI 问答**：自然语言查行情（火山方舟 Coding Plan，10 模型可切，SSE 流式 + iFinD MCP 工具自动调用）
  - 顶部 Tab 切换，状态完全隔离；时间条可拖动/播放回看任意时刻

## 5 个 iFinD 接口

| # | 接口 | 用途 | 缓存策略 |
|---|---|---|---|
| 1 | `basic_data_service` (ths_the_ths_concept_index_stock) | 个股所属同花顺概念 | 一次性永久缓存 |
| 2 | `data_pool` (p03473) | 概念板块成分股 | 一次性永久缓存 |
| 3 | `cmd_history_quotation` | 历史行情日K | 按 (code, date) 缓存 |
| 4 | `high_frequency` | 1min K线 | 实时监控用（盘中拉取，不入库） |
| 5 | `basic_data_service` (ths_index_short_name_index) | 概念基本信息字典 | 一次性永久缓存 |

## 项目结构

```
ifind_sector_attribution/
├── config.py              # 配置（token、概念代码、计算权重、A股过滤规则、分时数据源）
├── config_local.py        # 本地 token + KLINE_API_BASE_URL（已 gitignore，不提交）
├── ifind_client.py        # iFinD API 客户端封装（含指数退避重试、批量分片）
├── intraday_fetcher.py    # 分时数据批量并发封装（kline-fetcher TrendFetcher，32线程）
├── database.py            # SQLite 数据库封装
├── sync_pipeline.py       # 数据同步与计算管线
├── core_calculator.py     # 核心计算引擎（板块强度、多周期融合、L1归因）
├── stock_scorer.py        # 成分股四维综合评分（涨幅/涨速/开盘至今涨幅/涨停）+ 涨速加速
├── realtime_engine.py     # 盘中实时引擎（分时序列缓存 + 时刻切片 + 板块强度）
├── prescreen.py           # 盘前筛选（5日涨幅选板块+成分股→watchlist）
├── api_server.py          # FastAPI 服务层（API + 可视化页面）
├── main.py                # 入口脚本
├── requirements.txt       # 依赖
├── templates/
│   ├── tabs.html          # 顶层 Tab 容器（板块强度/自选分组 双看板 iframe 隔离）
│   └── index.html         # 看板单页（?board=sector/custom 复用；时间滑块+播放+3s轮询+加速列+持仓标注）
├── ifind-monitor.service  # systemd 服务配置（开机自启+自动重启）
├── install_service.sh     # 一键安装 systemd 服务脚本
├── data/                  # 数据库文件（已 gitignore）
└── tests/
    └── test_api.py        # 接口测试脚本
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt

# 分时数据依赖（盘中实时监控用，需单独装）
pip install -e /root/Projects/kline-fetcher
# 或从 GitHub 安装：pip install git+https://github.com/seuzxh/kline-fetcher.git
```

### 2. 配置 token

在项目根目录创建 `config_local.py`（已被 `.gitignore` 忽略，不会提交）：

```python
# iFinD token（盘后 daily / 归因 / 盘前筛选用）
ACCESS_TOKEN = "你的 access token"
REFRESH_TOKEN = "你的 refresh token"

# 中焯行情 API 地址（盘中实时监控用，敏感不入库）
KLINE_API_BASE_URL = "http://your-kline-api-host:port"

# AI 问答 LLM（火山方舟 Coding Plan，敏感不入库）
# 注意：base_url 必须用 /api/coding/v3（走 Plan 额度），
#       切勿用 /api/v3（不消耗 Plan 额度会产生额外费用）。
# 文档：https://www.volcengine.com/docs/82379/1928261
LLM_API_KEY = "你的 ark api key"
LLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
LLM_MODEL = "doubao-seed-2.0-pro"   # 可选 10 个模型，页面下拉框运行时也可切

# iFinD MCP server 鉴权 JWT（AI 问答调 iFinD 工具用，敏感不入库）
IFIND_MCP_TOKEN = "你的 mcp jwt token"
```

AI 问答可用模型（`doubao-seed-2.0-pro`/`code`/`lite`、`doubao-seed-code`、`minimax-latest`、`glm-latest`、`deepseek-v4-flash`/`pro`、`kimi-k2.6`、`kimi-k2.7-code`）。`LLM_API_KEY` / `IFIND_MCP_TOKEN` 留空时 AI 问答降级为手动选工具模式（页面可用不报错）。

### 3. 运行接口测试

```bash
python main.py test    # 测试 5 个 iFinD 接口连通性
```

### 4. 首次部署初始化

```bash
python main.py init
```

`init` 会依次执行：
1. **概念字典**（接口5）：拉取 A 股行业分类概念（700xxx/881xxx/884xxx 等），自动过滤海外行业指数
2. **个股-概念映射**（接口1）：默认用 8 只样本股票
3. **行业成分股**（接口2）：对全部行业概念并发拉取成分股（默认 8 线程）
4. **概念板块全集补全**（`init_concept_universe`）：扫描全市场股票，补全 885xxx/886xxx 概念板块码的字典+成分股+映射，打通归因链路

> **为什么需要步骤4**：接口1 返回的个股概念是 `885xxx` 系列（概念板块），而 `config.ALL_CONCEPT_CODES` 默认只有 `700xxx/884xxx`（行业分类），两套编码体系交集为 0。`init_concept_universe` 通过扫描全市场发现并补全概念板块码，让归因的 JOIN 能打通。详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

可选参数：

```bash
python main.py init --stocks stocks.txt    # 指定初始个股列表
```

### 5. 每日同步

```bash
python main.py daily --date 20260612              # 自动用全市场 A 股
python main.py daily --date 20260612 --codes my.txt  # 指定股票列表
```

`daily` 会执行：同步当日 K 线 → 计算多周期板块强度 → 计算个股归因。

> **日期须为交易日**：传入非交易日（如周末）会因当日无数据而返回空结果。

### 6. 启动服务（API + 可视化看板）

**方式A：systemd 服务（推荐，生产用）**

支持开机自启、崩溃自动重启、公网直连（115.191.14.82）+ SSH 隧道双访问方式：

```bash
sudo bash install_service.sh    # 一键安装并启动
```

启动后访问：
- **公网直连**：`http://115.191.14.82:8000`（需云安全组放行 TCP 8000）
- **SSH 隧道**：本地 `ssh -L 8000:127.0.0.1:8000 <用户>@115.191.14.82`，浏览器开 `http://127.0.0.1:8000`

详见 [部署手册](docs/DEPLOYMENT.md)。

**方式B：手动前台启动（调试用）**

```bash
python main.py server --host 0.0.0.0 --port 8000
```

服务启动后：
- **可视化看板**：浏览器打开 `http://localhost:8000`
- **REST API**：见下方"API 接口"

#### 可视化看板（双 Tab，盘中实时监控）

访问 `http://localhost:8000` 进入**顶层 Tab 容器**，两个看板各自独立 iframe，状态完全隔离（模式/时间条/播放互不影响）：

**📊 Tab 1：板块强度监控**（默认）
- 每个分组 = 同花顺概念板块（884 行业分类码）。三种模式可切：
  - **实时模式**（默认）：基于 **kline-fetcher 分时数据**，拉每只股票完整分时序列（集合竞价 + 盘中逐分钟 241 点）。前端每 **3s** 自动轮询，后端分时序列缓存（TTL 5s）挡住网络。Top10 板块成分股按**四维加权评分**排名（涨幅 0.4 / 涨速 0.2 / 开盘至今涨幅 0.2 / 涨停 0.2），另有**涨速加速**指标（▲加速 / ▼减缓）。默认 **watchlist 聚焦**（~279 只，1.5s 拉取）。
  - **历史模式**：选日期读已入库的收盘数据，秒级响应，成分股按当日涨幅排序。
  - **盘前筛选模式**：展示当日 watchlist（20 板块 + 各 30 成分股的 5d 涨幅）。

**⭐ Tab 2：自选分组监控**
- 每个分组 = 你导入的**自选股分组**（同花顺 custom_block 导出）。复用板块看板的全部功能（实时分时 / 时间条 / 播放 / 历史回看 / 四维评分），仅分组来源不同。
- **持仓金色标注**：持仓分组（默认 "CC"）的成分股作为持仓股，凡含持仓股的分组在排行表/卡片/成分股行**金色高亮**（持仓徽章 + 持仓标签），一眼看出哪些主题涉及持仓。持仓分组名可配置（`config.HOLDING_GROUP_NAME`）。
- 导入分组：`python main.py import-groups`（读 `ths-custom-block-data/同花顺自选分组导出.json`，幂等覆盖，自动过滤指数/ETF/可转债等非 A 股）

**两看板通用功能**：
- **可拖动时间条**：拖到任意时刻（如 9:50）回看板块排名，观察轮动；拖动后自动暂停跟随，点"回到最新"恢复
- **▶ 播放按钮**：自动逐分钟推进时间条（速度 1.5x/2x/4x/8x），像动画看板块强度演进；到末尾自动停
- 选历史日期时拉该日全天分时后缓存，拖时间条/切片纯内存（毫秒级）

页面布局：顶部统计栏（股票数/涨跌/涨停）+ 时间条（播放控件）+ Top10 强势板块 + Bottom10 弱势板块 + 下方各板块成分股卡片。点击板块行可高亮对应成分股卡片。顶部"盘前筛选"按钮可即时触发筛选。

> 实时模式在交易时段拉当日数据；非交易时段/盘前会显示最近交易日的全天数据（可拖时间条体验回看）。

### 7. 盘前筛选（生成 watchlist）

盘前（如 9:15~9:25）执行，选出近 5 个交易日强势的板块和成分股，供实时监控聚焦：

```bash
python main.py prescreen --date 20260612               # 默认前20板块，每板块前30成分股
python main.py prescreen --date 20260612 --top-sector 30  # 自定义数量
```

筛选逻辑：
1. 对所有 A 股概念算 5 日累计涨幅（成分股均值），取前 20 板块
2. 对选出的每个板块，取其成分股 5 日累计涨幅前 30
3. 结果存入 `watchlist` 表，供实时监控的 watchlist 模式使用

> **新股过滤**：自动剔除上市不足 5 个交易日的新股（从 K 线历史反推），避免连板新股撑高板块涨幅导致排名失真。

也可通过可视化页面的"盘前筛选"按钮触发，或调用 `POST /api/prescreen`。

### 8. 清理海外数据（维护命令）

```bash
python main.py purge             # 删除所有海外数据，仅保留 A 股
python main.py purge --vacuum    # 删除后执行 VACUUM 回收磁盘空间
```

此命令幂等，可重复执行。建议执行前备份数据库。

### 9. 导入自选股分组（自选看板用）

```bash
python main.py import-groups                      # 默认读 ths-custom-block-data/同花顺自选分组导出.json
python main.py import-groups --json /path/to.json # 指定其他 JSON
```

从同花顺 custom_block 导出的自选分组 JSON 导入到 `custom_group` 表，供"自选分组看板"使用。**幂等**（清表重导，分组更新后重跑即可），自动按 `market_code` 过滤指数/ETF/可转债等非 A 股标的（只保留 17 沪/33 深/151 北交）。

## 命令一览

| 命令 | 说明 |
|---|---|
| `init [--stocks FILE]` | 首次部署：拉取字典+成分股+映射，补全概念板块全集 |
| `daily --date DATE [--codes FILE]` | 每日：同步 K 线 + 板块强度 + 个股归因 |
| `prescreen [--date DATE] [--top-sector N] [--top-stock M]` | 盘前筛选：5日涨幅选板块+成分股，存入 watchlist |
| `import-groups [--json FILE]` | **导入自选股分组 JSON**（幂等覆盖），自选看板用 |
| `server [--host H] [--port P]` | 启动 FastAPI 服务（API + 可视化页面） |
| `test` | 测试 5 个 iFinD 接口连通性 |
| `purge [--vacuum]` | 删除海外数据，仅保留 A 股 |

## API 接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `GET /` | — | **顶层 Tab 容器**（`tabs.html`）；带 `?board=sector`/`=custom` 返回 iframe 内页 |
| `GET /api/sector/rankings` | — | 获取板块强度排名（含多周期融合分） |
| `POST /api/attribution/stock` | — | 个股多概念归因 |
| `POST /api/attribution/portfolio` | — | 组合归因 + 强势板块定位 |
| `GET /api/realtime/sector` | — | 最新板块强度排名 |
| `GET /api/realtime/dashboard` | — | **板块实时看板**（分时切片，`trade_date`/`snapshot_time`/`watchlist_mode`） |
| `GET /api/custom/dashboard` | — | **自选分组看板**（`custom_group` 替代概念板块，复用实时切片，返回持仓标注字段） |
| `GET /api/custom/scan` | — | **自选强势归类**（分时筛选命中 → 按自选分组归类） |
| `GET /api/market/scan` | — | **全市场强势归类**（MCP `search_stocks` 选股 → 按 884 概念板块归类；入参 `query`） |
| `POST /api/realtime/clear_cache` | — | 清空分时序列缓存（切日/调试用） |
| `GET /api/history/dashboard` | — | **历史看板**（指定日期的板块 + 成分股） |
| `GET /api/trade_calendar` | — | 交易日列表（供日期选择器过滤非交易日） |
| `GET /api/session_status` | — | 交易时段状态（盘前/盘中/盘后，前端据此控制轮询） |
| `POST /api/prescreen` | — | **盘前筛选**（5日涨幅选板块+成分股，存入 watchlist） |
| `GET /api/watchlist` | — | 读取当日 watchlist |
| `GET /api/dates` | — | 已入库的板块强度日期列表 |
| `GET /api/concept/list` | — | 全部 A 股概念板块列表 |
| `GET /api/concept/members` | — | 概念板块成分股（`date` 不传则取最新缓存） |

## 配置项（config.py）

| 配置 | 默认值 | 说明 |
|---|---|---|
| `ACCESS_TOKEN` / `REFRESH_TOKEN` | （空） | iFinD 认证，用 `config_local.py` 覆盖 |
| `KLINE_API_BASE_URL` | （空） | 中焯行情 API 地址（分时数据源），用 `config_local.py` 覆盖 |
| `SCORE_WEIGHTS` | s1:0.4 / s2:0.3 / s4:0.3 | 板块强度三维权重 |
| `PERIOD_WEIGHTS` | 1d:0.5 / 5d:0.3 / 20d:0.2 | 多周期融合权重 |
| `MIN_MEMBER_COUNT` | 6 | 命中 K 线的成分股数下限，过滤迷你概念 |
| `CONCEPT_MEMBERS_CONCURRENCY` | 8 | 成分股拉取并发线程数 |
| `CONCEPT_MEMBERS_PROGRESS_EVERY` | 100 | 进度打印间隔 |
| `A_SHARE_CONCEPT_PREFIXES` | 700/881/883/884/885/886 | A 股概念前缀白名单 |
| `A_SHARE_SUFFIXES` | .SH/.SZ/.BJ | A 股个股后缀 |
| `PRESCREEN_PERIOD_DAYS` | 5 | 盘前筛选用的累计涨幅天数 |
| `PRESCREEN_TOP_SECTOR` | 20 | 盘前筛选选出的板块数 |
| `PRESCREEN_TOP_STOCK` | 30 | 每个板块选出的成分股数 |
| `INTRADAY_WORKERS` | 32 | 分时数据多线程拉取并发数 |
| `INTRADAY_CACHE_TTL` | 5 | 分时序列缓存 TTL（秒） |
| `SECTOR_POOL_ENABLED` / `SECTOR_POOL_CODES` | True / 884(259个) | 板块池白名单（限定观察的概念范围，当前 884 行业分类码） |
| `HOLDING_GROUP_NAME` | "CC" | 持仓分组名（自选看板金色标注用，按 block_name 精确匹配） |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | （空）/ `.../api/coding/v3` / `doubao-seed-2.0-pro` | AI 问答 LLM（火山方舟 Coding Plan），用 `config_local.py` 覆盖 |
| `IFIND_MCP_TOKEN` | （空） | iFinD MCP server JWT（AI 问答调工具用），用 `config_local.py` 覆盖 |

## 数据模型

SQLite 数据库（`data/sector_attribution.db`）包含 7 张表：

| 表 | 说明 | A 股过滤 |
|---|---|---|
| `ths_concept_dict` | 概念板块字典（行业码 + 概念码） | 仅 A 股前缀 |
| `stock_concept_map` | 个股-概念映射（全市场） | 仅 A 股代码 |
| `concept_members` | 概念成分股 | 仅 A 股概念 |
| `daily_kline` | 日K线 | 仅 A 股代码 |
| `min1_kline` | 1min K线（当前未启用） | — |
| `concept_strength` | 板块强度评分（含 score_1d/5d/20d/final） | 仅 A 股概念 |
| `stock_attribution` | 个股归因结果（含明细 JSON） | 仅 A 股代码 |
| `watchlist` | 盘前筛选结果（板块+成分股，按日期） | 仅 A 股 |

**永久缓存语义**：`stock_concept_map` / `concept_members` 是一次性缓存，查询时不传日期则取最新一份（`MAX(date)`），与 init 日期解耦。详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 更多文档

- [架构设计](docs/ARCHITECTURE.md) — 双概念编码体系、永久缓存语义、多周期融合算法、A股过滤策略、实时监控、盘前筛选
- [部署手册](docs/DEPLOYMENT.md) — systemd 服务、外网访问、运维命令、故障排查
- [更新日志](docs/CHANGELOG.md) — 版本改动记录
