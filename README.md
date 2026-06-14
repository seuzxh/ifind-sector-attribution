# iFinD 行业归因与板块强度检测系统

基于同花顺 iFinD API 的量化**行业归因 + 板块强度检测**系统。用同花顺概念板块（行业分类 + 概念板块双体系）作为分类标准，自动判定"哪只股票被哪个概念带涨"以及"当前哪个板块最强"。**仅处理 A 股**（沪深北交易所）。

## 核心功能

- **板块强度检测**：三维评分（涨幅强度 S1 / 上涨广度 S2 / 相对强度 S4）+ Z-score 标准化排名，支持 **1d / 5d / 20d 多周期融合**
- **个股多概念归因**：L1 权重归因法（`weight × concept_return`），分解个股涨幅对各概念的贡献
- **组合归因分析**：按持仓组合的市值暴露，匹配当前强势板块并预警
- **A股范围限定**：全链路过滤海外代码，只处理沪深北交易所股票
- **盘中实时监控**：可视化网站，9:30~9:40 实时刷新板块强度 + 成分股四维评分排名

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
├── config.py              # 配置（token、概念代码、计算权重、A股过滤规则）
├── config_local.py        # 本地 token 配置（已 gitignore，不提交）
├── ifind_client.py        # iFinD API 客户端封装（含指数退避重试、批量分片）
├── database.py            # SQLite 数据库封装
├── sync_pipeline.py       # 数据同步与计算管线
├── core_calculator.py     # 核心计算引擎（板块强度、多周期融合、L1归因）
├── stock_scorer.py        # 成分股四维综合评分（涨幅/涨速/实体/涨停）
├── realtime_engine.py     # 盘中实时引擎（接口4拉取+实时板块强度）
├── prescreen.py           # 盘前筛选（5日涨幅选板块+成分股→watchlist）
├── api_server.py          # FastAPI 服务层（API + 可视化页面）
├── main.py                # 入口脚本
├── requirements.txt       # 依赖
├── templates/
│   └── index.html         # 可视化看板单页（plotly.js CDN）
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
```

### 2. 配置 token

在项目根目录创建 `config_local.py`（已被 `.gitignore` 忽略，不会提交）：

```python
ACCESS_TOKEN = "你的 access token"
REFRESH_TOKEN = "你的 refresh token"
```

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

支持开机自启、崩溃自动重启、外网访问：

```bash
sudo bash install_service.sh    # 一键安装并启动
```

启动后访问 `http://<服务器公网IP>:8000`。详见 [部署手册](docs/DEPLOYMENT.md)。

**方式B：手动前台启动（调试用）**

```bash
python main.py server --host 0.0.0.0 --port 8000
```

服务启动后：
- **可视化看板**：浏览器打开 `http://localhost:8000`
- **REST API**：见下方"API 接口"

#### 可视化看板（盘中实时监控）

页面顶部可切换三种模式：

- **实时模式**（9:30~9:40 盘中）：前端每 15 秒自动轮询，后端实时调接口4 拉全市场 1min K 线，算实时板块强度。Top10 板块的成分股按**四维加权评分**排名（涨幅 0.4 / 涨速 0.2 / 实体涨幅 0.2 / 涨停 0.2），涨停判定按板块（主板 9.8% / 创业科创 19.5% / 北交 29%）。勾选"watchlist聚焦"后只监控盘前筛选出的标的（拉取量从 5500 降到 ~290，响应从 2 分钟 → 约 30 秒）。
- **历史模式**：选日期读已入库的收盘数据，秒级响应。成分股按当日涨幅排序（无 1min 历史，标注"仅涨幅"）。
- **盘前筛选模式**：展示当日 watchlist（20 板块 + 各 30 成分股的 5d 涨幅），开盘前观察用。

页面布局：顶部统计栏（股票数/涨跌/涨停）+ 左侧 Top10 强势板块柱状图 + 右侧 Bottom10 弱势板块 + 下方各板块成分股卡片。点击柱状图可高亮对应成分股卡片。顶部"盘前筛选"按钮可即时触发筛选。

> 实时模式仅在交易时段有数据，非交易时段会显示友好提示。

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

## 命令一览

| 命令 | 说明 |
|---|---|
| `init [--stocks FILE]` | 首次部署：拉取字典+成分股+映射，补全概念板块全集 |
| `daily --date DATE [--codes FILE]` | 每日：同步 K 线 + 板块强度 + 个股归因 |
| `prescreen [--date DATE] [--top-sector N] [--top-stock M]` | 盘前筛选：5日涨幅选板块+成分股，存入 watchlist |
| `server [--host H] [--port P]` | 启动 FastAPI 服务（API + 可视化页面） |
| `test` | 测试 5 个 iFinD 接口连通性 |
| `purge [--vacuum]` | 删除海外数据，仅保留 A 股 |

## API 接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `GET /` | 可视化看板页面（HTML） |
| `GET /api/sector/rankings` | 获取板块强度排名（含多周期融合分） |
| `POST /api/attribution/stock` | 个股多概念归因 |
| `POST /api/attribution/portfolio` | 组合归因 + 强势板块定位 |
| `GET /api/realtime/sector` | 最新板块强度排名 |
| `GET /api/realtime/dashboard` | **实时看板**（top/bottom 板块 + 成分股排名，支持 watchlist_mode） |
| `GET /api/history/dashboard` | **历史看板**（指定日期的板块 + 成分股） |
| `POST /api/prescreen` | **盘前筛选**（5日涨幅选板块+成分股，存入 watchlist） |
| `GET /api/watchlist` | 读取当日 watchlist |
| `GET /api/dates` | 已入库的板块强度日期列表 |
| `GET /api/concept/list` | 全部 A 股概念板块列表 |
| `GET /api/concept/members` | 概念板块成分股（`date` 不传则取最新缓存） |

## 配置项（config.py）

| 配置 | 默认值 | 说明 |
|---|---|---|
| `ACCESS_TOKEN` / `REFRESH_TOKEN` | （空） | iFinD 认证，用 `config_local.py` 覆盖 |
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
