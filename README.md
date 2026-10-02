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
  - **自选强势归类**：iFinD REST 智能选股（smart_stock_picking）后取自选股交集，再按自选分组统计命中
  - **全市场强势归类**：自然语言选股（iFinD REST `smart_stock_picking`，4 组预置 + 自定义条件可存/重命名）→ 知识图谱富集归类（全量板块按富集倍数/命中数排序，详见 docs/architecture/DESIGN-strong-stock-scan.md）
  - **开盘题材看板**：消费盘前冻结归因，自 09:30 起展示题材强弱、加速、扩散和贡献个股，支持历史日期与时点回放
  - 顶部 Tab 切换，状态完全隔离；时间条可拖动/播放回看任意时刻

## 5 个 iFinD 接口

| # | 接口 | 用途 | 缓存策略 |
|---|---|---|---|
| 1 | `basic_data_service` (ths_the_ths_concept_index_stock) | 个股所属同花顺概念 | 一次性永久缓存 |
| 2 | `data_pool` (p03473) | 概念板块成分股 | 一次性永久缓存 |
| 3 | `cmd_history_quotation` | 历史行情日K | 按 (code, date) 缓存 |
| 4 | `high_frequency` | 1min K线 | 客户端保留接口；当前实时看板改用 kline-fetcher |
| 5 | `basic_data_service` (ths_index_short_name_index) | 概念基本信息字典 | 一次性永久缓存 |

## 项目结构

```
ifind_sector_attribution/
├── config.py              # 配置（token、概念代码、计算权重、A股过滤规则、分时数据源）
├── config_local.py        # 本地 token + KLINE_API_BASE_URL（已 gitignore，不提交）
├── ifind_hub.py           # ifind-sector-hub 组件接入点（进程级单例 + token 落盘）
├── intraday_fetcher.py    # 分时数据批量并发封装（kline-fetcher TrendFetcher，32线程）
├── database/              # SQLite 数据库封装（按领域拆分：core 连接/DDL + kline/kg/results/
│                          #   sector_tables/watched/custom_group/maintenance Mixin；schema.py 建表 DDL）
├── sync_pipeline.py       # 数据同步与计算管线
├── opening_strength/      # 盘前归因冻结 + 独立开盘行情缓存、纯聚合与只读看板服务
├── core_calculator.py     # 核心计算引擎（板块强度、多周期融合、L1归因；纯计算无 I/O）
├── stock_scorer.py        # 成分股四维综合评分（涨幅/涨速/开盘至今涨幅/涨停）+ 涨速加速
├── realtime_engine.py     # 盘中实时引擎（分时序列缓存 + 时刻切片 + 板块强度）
├── api/                   # FastAPI 接口层（app 组装 / deps 单例 / schemas 模型 /
│                          #   routers 按域路由 + history_service/kg_views 编排下沉）
├── api_server.py          # 兼容入口（from api import app；uvicorn "api_server:app" 不变）
├── main.py                # 入口脚本
├── requirements.txt       # 依赖
├── .importlinter.ini      # 分层依赖约束（import-linter；配合 tests/test_layering.py）
├── frontend/              # Vue 3 + Vite + TypeScript 前端源码（构建产物输出到 static/）
├── static/                # 前端构建产物（FastAPI 托管；已 gitignore）
├── ifind-monitor.service  # systemd 服务配置（开机自启+自动重启）
├── install_service.sh     # 一键安装 systemd 服务脚本
├── data/                  # 数据库文件（已 gitignore）
└── tests/                 # unittest 套件（接口冒烟/性能架构/推送/分层约束）
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt

# 分时数据依赖（盘中实时监控用，需单独装）
pip install -e /root/Projects/kline-fetcher
   # 板块/概念数据层公共组件（本地包）：
   pip install -e /root/Projects/ifind-sector-hub
# 或从 GitHub 安装：pip install git+https://github.com/seuzxh/kline-fetcher.git
```

### 2. 配置 token

在项目根目录创建 `config_local.py`（已被 `.gitignore` 忽略，不会提交）：

```python
# iFinD token（盘后 daily / 归因用）
ACCESS_TOKEN = "你的 access token"
REFRESH_TOKEN = "你的 refresh token"

# 中焯行情 API 地址（盘中实时监控用，敏感不入库）
KLINE_API_BASE_URL = "http://your-kline-api-host:port"

# 股池归因定时推送飞书 webhook（scan_push 用，敏感不入库）
PUSH_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
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

> **为什么需要步骤4**：接口1 返回的个股概念是 `885xxx` 系列（概念板块），而 `config.ALL_CONCEPT_CODES` 默认只有 `700xxx/884xxx`（行业分类），两套编码体系交集为 0。`init_concept_universe` 通过扫描全市场发现并补全概念板块码，让归因的 JOIN 能打通。详见 [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md)。

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

详见 [部署手册](docs/ops/DEPLOYMENT.md)。

**方式B：手动前台启动（调试用）**

```bash
python main.py server --host 0.0.0.0 --port 8000
```

服务启动后：
- **可视化看板**：浏览器打开 `http://localhost:8000`
- **REST API**：见下方"API 接口"

#### 可视化看板（Vue SPA，盘中实时监控）

访问 `http://localhost:8000` 进入 **Vue 3 SPA**，顶部共 8 个 Tab（板块强度/开盘题材/自选分组/集合竞价/强势归类×2/监控板块管理/知识图谱），各页状态由 `<keep-alive>` 保留：

**📊 Tab 1：板块强度监控**（默认）
- 每个分组 = “监控板块管理”中勾选且成分股数为 **10~500（含边界）** 的同花顺概念板块。两种模式可切：
  - **实时模式**（默认）：基于 **kline-fetcher 分时数据**，拉取有效板块（已勾选且成员数 10~500）的去重成分股完整分时序列。前端每 **3s** 自动轮询，状态栏用“刷新 HH:MM:SS”显示最近一次响应；后端结果缓存（TTL 10s）和分时序列缓存（TTL 15s）挡住重复计算与网络。新分时序列写入后会立即淘汰对应旧结果，下一轮轮询即可消费新行情。Top10 板块成分股按**四维加权评分**排名（涨幅 0.4 / 涨速 0.2 / 开盘至今涨幅 0.2 / 涨停 0.2），另有**涨速加速**指标（▲加速 / ▼减缓）。
  - **历史模式**：选日期读已入库的收盘数据，秒级响应，成分股按当日涨幅排序。

**⭐ 自选分组监控**
- 每个分组 = 你导入的**自选股分组**（同花顺 custom_block 导出）。复用板块看板的全部功能（实时分时 / 时间条 / 播放 / 历史回看 / 四维评分），仅分组来源不同。
- **持仓金色标注**：持仓分组（默认 "CC"）的成分股作为持仓股，凡含持仓股的分组在排行表/卡片/成分股行**金色高亮**（持仓徽章 + 持仓标签），一眼看出哪些主题涉及持仓。持仓分组名可配置（`config.HOLDING_GROUP_NAME`）。
- 导入分组：`python main.py import-groups`（读 `ths-custom-block-data/同花顺自选分组导出.json`，幂等覆盖，自动过滤指数/ETF/可转债等非 A 股）

**两看板通用功能**：
- **可拖动时间条**：拖到任意时刻（如 9:50）回看板块排名，观察轮动；拖动后自动暂停跟随，点"回到最新"恢复
- **▶ 播放按钮**：自动逐分钟推进时间条（速度 1.5x/2x/4x/8x），像动画看板块强度演进；到末尾自动停
- **全量成分股字段排序**：初始卡片由全部有效成员计算综合分后只展示 10 支；点击涨幅/涨速/加速/开盘至今/综合分时，后端对该分组全部有效成员重新排序，再返回前 10，避免把全量成员塞进每 3 秒看板响应
- 选历史日期时拉该日全天分时后缓存，拖时间条/切片纯内存（毫秒级）

页面布局：顶部统计栏（股票数/涨跌/涨停）+ 时间条（播放控件）+ Top10 强势板块 + Bottom10 弱势板块 + 下方各板块成分股卡片。点击板块行可高亮对应成分股卡片。

> 实时模式在交易时段拉当日数据；非交易时段/盘前会显示最近交易日的全天数据（可拖时间条体验回看）。

**🌅 开盘题材**（新增第八个 Tab，导航位于板块强度之后，`#/opening-themes`）：

- 左侧题材强弱榜、右侧加速/扩散榜、下方贡献个股联动；风险标签、冻结版本和数据健康度帮助判断覆盖与可信度。
- 实时模式固定中国标准日期，自动跟随每 3 秒请求；历史模式使用所选日的冻结归因与分时。拖动/播放停止自动跟随，切出 Tab 停止轮询和播放，重新激活后刷新。
- 只覆盖三个源股池候选及其冻结的 `884/885/886` 归因；起点为 09:30，不展示集合竞价。行情序列缓存当天 15 秒、历史日期在进程内稳定；聚合结果缓存 3 秒，按冻结 `run_id` 区分版本。
- 对应日期必须已有 `FROZEN` 快照；无快照返回 `404 SNAPSHOT_NOT_FOUND`，页面显示维护人员可复制的 `python main.py opening-premarket --date YYYYMMDD`。页面请求只读，不会自动冻结或重跑归因。

截至 2026-10-03，已记录的生产开盘四表为空，尚未执行首个真实冻结；新看板发布后预期显示无快照提示，真实排名仍需人工冻结与可用分时。本功能当前等待整分支审查、集成和发布。自动盘前调度、自定义静态题材、集合竞价排名、WebSocket 和排名持久化均不在本阶段范围。指标公式与 API 错误契约见 [API 参考](docs/guides/api.md#开盘题材)。

### 7. 清理海外数据（维护命令）

```bash
python main.py purge             # 删除所有海外数据，仅保留 A 股
python main.py purge --vacuum    # 删除后执行 VACUUM 回收磁盘空间
```

此命令幂等，可重复执行。建议执行前备份数据库。

### 8. 导入自选股分组（自选看板用）

```bash
python main.py import-groups                      # 默认读 ths-custom-block-data/同花顺自选分组导出.json
python main.py import-groups --json /path/to.json # 指定其他 JSON
```

从同花顺 custom_block 导出的自选分组 JSON 导入到 `custom_group` 表，供"自选分组看板"使用。**幂等**（清表重导，分组更新后重跑即可），自动按 `market_code` 过滤指数/ETF/可转债等非 A 股标的（只保留 17 沪/33 深/151 北交）。

### 9. 盘前归因冻结快照

在项目根目录执行，`--date` 必填且格式为八位数字 `YYYYMMDD`，应使用目标交易日：

```bash
PYTHONPATH=. python main.py opening-premarket --date 20261008
PYTHONPATH=. python main.py opening-premarket --date 20261008 --force-replace
```

命令合并三个 `data_pool p03473` 源股池：高贝塔值 `883926.TI`、近期强势 `883409.TI`、同花顺热股 `883910.TI`，保留全部来源及池内顺序。候选股票仅限沪深北 A 股；Theme 仅接受行业 `884xxx` 和概念 `885xxx/886xxx`，排除 `700xxx/881xxx`、自定义静态板块和其他代码体系。Membership 来自 sector-hub 已发布的权威成分快照，经批量反转后复制到本次运行，不依赖 `stock_concept_map`。

归因仅使用目标日前已完成日线，每股最多一个行业和两个概念，权重和为 1。Membership 覆盖率至少为 `0.90` 才能冻结；历史缺失允许降级并记录证据与历史覆盖率。成功输出 `run_id/status/candidate_count/mapped_count/coverage_ratio/history_coverage_ratio`；领域错误退出码为 1，仅输出运行标识和失败码。

每次运行创建新版本，正常盘前重跑成功后原子替换同日冻结版本；失败保留旧版本。当日 09:30（Asia/Shanghai）以后替换已有冻结版本须显式传 `--force-replace`，覆盖原因会留存。历史日期回放不受该时间限制。

`Database` 初始化时迁移以下四张 monitor 私有表，运行查询也通过 `Database` 仓储方法完成：

| 表 | 内容 |
|---|---|
| `opening_premarket_run` | 运行状态、版本、覆盖率、时间、失败码和覆盖原因 |
| `opening_candidate_snapshot` | 候选股票及各源池来源、池内顺序 |
| `opening_membership_snapshot` | 本次实际使用的股票—Theme 权威关系副本 |
| `opening_attribution_snapshot` | 归因排名、分数、权重、置信度、原因码和特征证据 |

第一阶段交付 CLI 和后端冻结链路；开盘题材看板现已实现独立实时聚合、只读 REST 和 Vue 页面，等待审查与发布。详细契约见[盘前归因快照设计](docs/superpowers/specs/2026-10-02-opening-strength-premarket-design.md)和[开盘题材看板设计](docs/superpowers/specs/2026-10-03-opening-theme-dashboard-design.md)。截至 2026-10-03，已记录的本机运行库四表仍无真实冻结记录；`data/DATABASE_MANIFEST.json` 仍是旧数据快照，应在首次真实冻结并现场复核后完整刷新。

### 10. 离线测试

完整后端测试使用确定性 fixture 与临时 SQLite 数据库，默认不访问实时 iFinD；只有显式启用 `IFIND_SMOKE=1` 的连通性冒烟测试需要真实 token/网络。`main.py test` 会启用该冒烟开关。

```bash
PYTHONPATH=. python -m unittest tests.opening_strength.test_realtime_aggregation tests.opening_strength.test_quote_provider tests.opening_strength.test_dashboard_service tests.test_opening_strength_api tests.test_layering -v
PYTHONPATH=. python -m unittest discover -s tests -t . -p 'test_*.py' -v
python scripts/lint_docs.py
git diff --check
cd frontend
npm ci && npm test -- --run && npm run type-check && npm run build
```

完整发现必须保留 `-t .`，避免 `tests/database` 和 `tests/opening_strength` 的镜像包遮蔽生产包。本机解释器见 `AGENTS.md` 的运行环境表。

## 命令一览

| 命令 | 说明 |
|---|---|
| `init [--stocks FILE]` | 首次部署：拉取字典+成分股+映射，补全概念板块全集 |
| `daily --date DATE [--codes FILE]` | 每日：同步 K 线 + 板块强度 + 个股归因 |
| `opening-premarket --date YYYYMMDD [--force-replace]` | 盘前归因：执行、校验并冻结快照；开盘后当日替换须显式授权 |
| `import-groups [--json FILE]` | **导入自选股分组 JSON**（幂等覆盖），自选看板用 |
| `refresh-boards [--skip-members]` | **smart_stock_picking 刷新板块字典**（710 全集+清理遗留+勾选迁移+新板块成分股） |
| `server [--host H] [--port P]` | 启动 FastAPI 服务（API + 可视化页面） |
| `test` | 测试 5 个 iFinD 接口连通性 |
| `purge [--vacuum]` | 删除海外数据，仅保留 A 股 |

## API 接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `GET /` | — | **前端入口**（Vue 3 SPA，`static/index.html`，Hash 路由） |
| `GET /api/sector/rankings` | — | 获取板块强度排名（含多周期融合分） |
| `POST /api/attribution/stock` | — | 个股多概念归因 |
| `POST /api/attribution/portfolio` | — | 组合归因 + 强势板块定位 |
| `GET /api/realtime/sector` | — | 最新板块强度排名 |
| `GET /api/realtime/dashboard` | — | **板块实时看板**（管理页有效板块，分时切片） |
| `GET /api/opening-strength/dashboard` | — | **开盘题材**（必填 `trade_date`、可选 `snapshot_time`；只读冻结快照 + 分时聚合） |
| `GET /api/custom/dashboard` | — | **自选分组看板**（`custom_group` 替代概念板块，复用实时切片，返回持仓标注字段） |
| `GET /api/dashboard/members` | — | 单板块/分组全部有效成员按字段排序，仅返回前 10（实时看板点击成分股表头时按需调用） |
| `GET /api/custom/scan` | — | **自选强势归类**（REST 智能选股 → 取自选交集 → 按自选分组归类） |
| `GET /api/theme/attribution` | — | **题材催化反向归因**（`date`/`pool` 入参；八因子评分+重叠压缩+主线提取，v1 无资讯维度） |
| `GET /api/market/scan` | — | **全市场强势归类**（REST 智能选股 → 知识图谱富集归类：全量板块按富集倍数/命中数排序，每股带 ρ；入参 `query/order/min_hits/top_n`） |
| `POST /api/realtime/clear_cache` | — | 清空分时序列缓存（切日/调试用） |
| `GET /api/history/dashboard` | — | **历史看板**（`scope=sector` 按当前勾选板块；`scope=custom` 按自选分组） |
| `GET /api/trade_calendar` | — | 交易日列表（供日期选择器过滤非交易日） |
| `GET /api/session_status` | — | 交易时段状态（盘前/盘中/盘后，前端据此控制轮询） |
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
| `MONITORED_CONCEPT_MIN_MEMBERS` | 10 | 监控候选的最新成分股总数下限（含边界） |
| `MONITORED_CONCEPT_MAX_MEMBERS` | 500 | 监控候选的最新成分股总数上限（含边界） |
| `CONCEPT_MEMBERS_CONCURRENCY` | 8 | 成分股拉取并发线程数 |
| `CONCEPT_MEMBERS_PROGRESS_EVERY` | 100 | 进度打印间隔 |
| `A_SHARE_CONCEPT_PREFIXES` | 700/881/883/884/885/886 | A 股概念前缀白名单 |
| `A_SHARE_SUFFIXES` | .SH/.SZ/.BJ | A 股个股后缀 |
| `INTRADAY_WORKERS` | 32 | 分时数据多线程拉取并发数 |
| `INTRADAY_CACHE_TTL` | 15 | 分时序列缓存 TTL（秒）；过期时先返回旧序列并后台刷新 |
| `SECTOR_POOL_ENABLED` / `SECTOR_POOL_CODES` | True / 884(259个) | `watched_concepts` 为空时的归因种子与兜底 |
| `HOLDING_GROUP_NAME` | "CC" | 持仓分组名（自选看板金色标注用，按 block_name 精确匹配） |

## 数据模型

SQLite 包含以下 9 张既有业务表，另有 hub 的 `relation_snapshots`、知识图谱 5 表和上文盘前快照 4 表，共 19 张现役表；本机运行库已于 2026-07-24 删除退役 `watchlist`，其他旧数据库仍可能残留该历史表：

| 表 | 说明 | A 股过滤 |
|---|---|---|
| `ths_concept_dict` | 概念板块字典（行业码 + 概念码） | 仅 A 股前缀 |
| `stock_concept_map` | 个股-概念映射（全市场） | 仅 A 股代码 |
| `concept_members` | 概念成分股 | 仅 A 股概念 |
| `daily_kline` | 日K线 | 仅 A 股代码 |
| `min1_kline` | 1min K线（当前未启用） | — |
| `concept_strength` | 板块强度评分（含 score_1d/5d/20d/final） | 仅 A 股概念 |
| `stock_attribution` | 个股归因结果（含明细 JSON） | 仅 A 股代码 |
| `custom_group` | 导入的同花顺自选分组 | 导入时仅留 A 股 |
| `watched_concepts` | 管理页持久化选择（读取时再按最新成分股数 10~500 过滤） | 物理行数不等于有效监控数 |

**永久缓存语义**：`stock_concept_map` / `concept_members` 是一次性缓存，查询时不传日期则取最新一份（`MAX(date)`），与 init 日期解耦。详见 [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md)。

## 更多文档

**在线文档站（GitHub Pages）**：<https://seuzxh.github.io/ifind-sector-attribution/>

- [快速开始](docs/getting-started.md) — 从零部署：依赖、token、初始化、启动、定时任务
- [交互指南](docs/guides/interaction.md) — 既有看板交互；新增开盘题材见上文与前端架构
- [API 参考](docs/guides/api.md) — 全部 REST 端点
- [架构设计](docs/architecture/ARCHITECTURE.md) — 双概念编码体系、永久缓存语义、多周期融合算法、A股过滤策略、实时监控
- [部署手册](docs/ops/DEPLOYMENT.md) — systemd 服务、外网访问、运维命令、故障排查
- [更新日志](docs/reference/CHANGELOG.md) — 版本改动记录

文档规范：`python scripts/lint_docs.py` 校验结构 / 链接 / 导航。
