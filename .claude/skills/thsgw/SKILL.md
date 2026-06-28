---
name: thsgw
description:
  查询同花顺 A 股数据：自选股信息、同花顺指数成分（A 股指数 / 三级行业指数）。
  优先用 iFinD MCP（自然语言 query 驱动），成分股/成分指数查询走 REST 接口2 更直接。
  MCP token 读 config.IFIND_MCP_TOKEN。涉及 A 股代码一律用 config.is_a_share_code 判定，勿自写正则。
---

# 同花顺查询 (thsgw)

> 🔒 **账号绑定（最高约束，见末「硬约束」第1条）**：本 skill **只能用用户提供的专属 token**
> （即 `config_local.py` 的 `IFIND_MCP_TOKEN`，绑定用户本人的同花顺账号）。
> 原因：自选股数据与该 token 绑定的同花顺账号挂钩，换任何其他 token 都会查到别人的/空的分组。
> **禁止**：用别的 token、把它分享出去、把明文 token 写进任何会被 git 跟踪的文件。

本项目封装了两条查询同花顺数据的通路，本 skill 指引「何时用哪条、怎么调」。
聚焦两个核心场景：**查自选股信息** + **查指数成分（A 股指数 / 三级行业指数）**。

> 所有命令在本项目根目录执行，Python 用 conda 环境 `vibe-trading`（见 AGENTS.md 运行环境表）。

## 何时用本 skill

- 用户问"我的自选股怎么样 / 自选股里哪些强势 / 某分组明细"
- 用户问"某某指数有哪些成分股"（如三级行业指数、A 股指数）
- 用户要查个股的行情 / 基本面 / 财务 / 股东 / 估值等（用 MCP 自然语言最省事）
- 用户要按条件选股（MCP `search_stocks`）

不适用于：盘中分时实时监控（那是 `realtime_engine` 的活）、盘后归因计算（那是 `core_calculator`）。

## 前置：确认 token 已配置

MCP 通路依赖鉴权 JWT，读 `config.IFIND_MCP_TOKEN`：

```python
import config
assert config.IFIND_MCP_TOKEN, "未配置 IFIND_MCP_TOKEN，请在 config_local.py 设置"
```

若为空：在 `config_local.py` 加一行 `IFIND_MCP_TOKEN = "<jwt>"`（该文件已 gitignore，安全）。
REST 通路（成分股）依赖 `ACCESS_TOKEN`（`config_local.py` 的 iFinD 数据接口 token，与 MCP token 不同，别混）。

> ⚠️ **两个 token 是不同的东西，别混淆**：
> - `IFIND_MCP_TOKEN` = MCP 自然语言接口的 JWT（header 含 `kid:mcp-api`），本 skill 主依赖。
> - `ACCESS_TOKEN`/`REFRESH_TOKEN` = iFinD REST 数据接口的 token，任务②成分股查询用。**会过期**（返回 `errorcode:-1302 "Access_Token is expired"`），过期需到同花顺重新获取并更新 `config_local.py`。

## 两条数据通路

| 维度 | MCP（自然语言） | REST（结构化） |
|---|---|---|
| 模块 | `mcp_proxy.py` | `ifind_client.py` |
| 调用 | `call_tool(server, tool, {query})` | `IFindClient().get_concept_members(...)` |
| 驱动 | 自然语言 query 字符串 | 代码 + 日期，精确 |
| 鉴权 | `IFIND_MCP_TOKEN`（JWT） | `ACCESS_TOKEN`（数据 token） |
| **擅长** | 个股摘要/选股/财务/行情/股东、指数指标查询 | **成分股清单（含三级行业指数）** |
| server | `stock`(10 工具) / `index`(3 工具) | 接口1/2/3/5 |

**选型原则**：查"一只/一批股票的某项数据"→ MCP；查"某指数包含哪些股票"→ REST 接口2。

---

## 任务①：查自选股信息

自选股 = 用户从同花顺客户端导出的**自选分组**，存在 `custom_group` 表（PK: group_id+stock_code，53 分组/594 股）。来源：`ths-custom-block-data/同花顺自选分组导出.json`，由 `main.py import-groups` 导入。

### 方式 A：读自选股清单（纯本地，最快）

```python
from database import Database
db = Database()
# 分组名映射 {group_id: group_name}
names = db.get_custom_group_names()
# 全部分组 → 成分股映射 {group_id: [stock_code, ...]}
members_map = db.get_custom_members_map()
# 全部分组去重后的股票代码列表
all_stocks = db.get_custom_all_stock_codes()
```

### 方式 B：自选股分组强弱排名（看板接口，含行情）

```bash
# 实时分组强弱 + 成分股四维排名（盘中/历史均可，复用 realtime_engine 分时切片）
curl "http://127.0.0.1:8000/api/custom/dashboard?snapshot_time=latest&top_n=10"
```

### 方式 C：自选股里挑强势股（MCP 选股 + 取交集）

```python
from mcp_proxy import call_tool
# 自然语言选股 → 返回符合条件的 A 股代码列表
hits = call_tool("stock", "search_stocks",
                 {"query": "近5日涨幅超过10%且市值大于200亿"})
# hits 是 Markdown/文本，需解析出代码；再与自选分组取交集
```

对应现成接口：`GET /api/custom/scan`（自动取交集 + 按自选分组归类统计）。

### 方式 D：查某只自选股的详情（MCP 自然语言）

```python
from mcp_proxy import call_tool
# 估值/财务/行情/股东等，一句话搞定
call_tool("stock", "get_stock_summary", {"query": "同花顺和恒生电子最新估值水平"})
call_tool("stock", "get_stock_financials", {"query": "科大讯飞在2025-12-31的ROE、净利润率"})
call_tool("stock", "get_stock_performance", {"query": "三花智控最近5日的涨跌幅与换手率"})
```

---

## 任务②：查指数成分（A 股指数 / 三级行业指数）

### 三级行业指数 = `881xxx` 体系

同花顺行业分级编码（已收录在 `ths_concept_dict` 表）：

| 前缀 | 含义 | 示例 |
|---|---|---|
| `881xxx` | **行业指数**（含三级行业） | `881101.TI` |
| `700xxx` | A 股行业指数 | `700471.TI` 铜(A股) |
| `884xxx` | 行业概念 | `884055.TI` 铅锌 |
| `885xxx`/`886xxx` | 概念板块（融资融券/苹果概念等） | `885338.TI` |

> 海外行业指数 `861xxx`(美股)/`871xxx`(港股) 已被 `is_a_share_concept` 排除，别碰。

### 查成分股（REST 接口2，最直接）

```python
from ifind_client import IFindClient
client = IFindClient()
# 查三级行业指数成分股，date 用 YYYYMMDD
resp = client.get_concept_members("881101.TI", "20260613")
# A 股指数成分同理
resp = client.get_concept_members("700471.TI", "20260613")
```

> 该接口（`data_pool` p03473）一次只能传**一个**概念代码，多个要循环调。返回结构见 `ifind_client.py:73`。
>
> ⚠️ **401 排错**：若报 `errorcode:-1302 "Access_Token is expired"`，是 `ACCESS_TOKEN` 过期（与 MCP token 无关），需到同花顺重新取数据接口 token 更新 `config_local.py`。在 token 失效期间，可用 MCP 通路降级（见下文「MCP 替代」）。

### MCP 替代（ACCESS_TOKEN 失效时降级）

REST 接口2 需要有效的 `ACCESS_TOKEN`；失效且无法立刻更新时，可用 MCP 通路部分替代：

```python
from mcp_proxy import call_tool
# 查板块基本信息（含成分股个数）
call_tool("index", "sector_data", {"query": "半导体板块的成分股个数"})
# 自然语言选股（注意：对"三级行业"等专业分类命中率低，条件宜宽松）
call_tool("stock", "search_stocks", {"query": "半导体行业的A股股票"})
```

> 限制：MCP 只能拿到板块汇总/部分股票，**无法精确复现接口2 的完整成分股清单**。要完整清单仍需有效的 `ACCESS_TOKEN` 走 REST 接口2。

### 查指数名 → 代码（不知道代码时）

```python
from ifind_client import IFindClient
client = IFindClient()
# 拿指数基本信息（短名/全名/代码）
info = client.get_concept_basic_info(["881101.TI", "881102.TI"])
# 或从已入库字典查（按名称模糊）
from database import Database
db = Database()
# 见 database.py 的 ths_concept_dict 查询方法
```

### 查指数本身的行情指标（MCP index server）

```python
from mcp_proxy import call_tool
call_tool("index", "index_data", {"query": "沪深300近20日涨跌幅"})
call_tool("index", "sector_data", {"query": "半导体板块最新涨跌幅"})  # 板块主体
```

---

## MCP 工具速查表（真实清单，2026-06-28 拉取）

> 工具集会变，调用前可 `MCPClient.instance().list_tools()` 拉最新清单。所有工具入参都是 `{"query": "自然语言"}`（`stock_highfreq_quotes`/`index_highfreq_quotes` 用 symbols 结构化入参）。

### stock server（10 工具，个股）

| 工具 | 用途 | query 示例 |
|---|---|---|
| `get_stock_summary` | 信息摘要（基础/行情/财务/估值/股东/主营） | "同花顺最新估值水平" |
| `search_stocks` | 自然语言选股 → 代码列表 | "汽车零部件市值>1000亿的股票" |
| `get_stock_performance` | 日频行情 + 技术指标/形态 | "三花智控最近5日涨跌幅与换手率" |
| `get_stock_info` | 基本资料（上市信息/行业/主营） | "格力电器上市时间与申万行业" |
| `get_stock_shareholders` | 股本结构 + 股东持股 | "光明乳业流通股占比、前5大股东" |
| `get_stock_financials` | 财务报表 + 财务/估值指标 | "科大讯飞2025-12-31的ROE、净利润率" |
| `get_risk_indicators` | 量价风险指标（alpha/beta/波动率/夏普/VAR） | "航天电子过去1年beta（基准沪深300）" |
| `get_stock_events` | 公司事件（IPO/再融资/并购/增减持/解禁） | "摩尔线程IPO首次发行新股数量" |
| `get_esg_data` | ESG 评级与报告 | "诚意药业中诚信ESG评级" |
| `stock_highfreq_quotes` | 实时快照 + 高频序列（仅当日，无历史） | symbols 结构化入参 |

### index server（3 工具，指数/板块）

| 工具 | 用途 | query 示例 |
|---|---|---|
| `index_data` | 指数指标查询（股票指数/基金/债券/期货/ESG） | "沪深300近20日涨跌幅" |
| `sector_data` | 板块主体指标（行业/概念/市场分类板块） | "半导体板块最新涨跌幅" |
| `index_highfreq_quotes` | 指数实时快照 + 高频序列 | 结构化入参 |

---

## 硬约束（不可违反）

1. **🔒 专属 token 绑定（第1条、最高优先级）**：本 skill **只能用用户提供的那个 token**
   ——`config_local.py` 的 `IFIND_MCP_TOKEN`（绑定用户本人的同花顺账号）。自选股（`custom_group`）与该
   token 绑定的账号挂钩，**换别的 token 查到的是错的**。因此：
   - 不得替换、不得用其他账号的 token；
   - 不得把明文 token 写进 SKILL.md 或任何 git 跟踪文件（`config_local.py` 已 gitignore）；
   - 不得把 token / SKILL 分享给他人。
   - token 过期时：提示用户重新提供，**不要**自作主张用历史/他处的 token 顶替。
2. **A 股范围限定**：个股代码后缀只认 `.SH/.SZ/.BJ`，概念前缀只认 `700/881/883/884/885/886`。
   判定一律用 `config.is_a_share_code(code)` / `config.is_a_share_concept(code)`，**不要自己写正则**。
3. 海外前缀 `861/864/865/871/875` 是美股/港股行业指数，会污染股票池，必须排除。
4. `search_stocks` 选股条件别太宽（工具说明明确"不建议范围过大"，费上下文/超时）。

---

## 附录

### MCP server 端点（URL 非敏感，写死在 `mcp_proxy.py`）

- stock: `https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-stock-mcp`
- index: `https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-index-mcp`

调用约定：POST JSON-RPC 2.0，header `Authorization: <jwt>`，stateless（无需 initialize/session）。

### 配置位置

| 项 | 位置 | 说明 |
|---|---|---|
| `IFIND_MCP_TOKEN` | `config_local.py`（已 gitignore） | MCP 鉴权 JWT，本 skill 主依赖 |
| `ACCESS_TOKEN` / `REFRESH_TOKEN` | `config_local.py` | REST 数据接口 token（成分股查询用） |
| 环境变量兜底 | `IFIND_MCP_TOKEN` | `config.py` 也读同名环境变量 |

### 相关代码

- `mcp_proxy.py` — MCP 客户端单例（`MCPClient.instance()`，list_tools 带 5min 缓存）
- `ifind_client.py` — 5 个 REST 接口封装（`get_concept_members` 在 :73）
- `database.py` — `custom_group` 表读写（`save_custom_groups`/`get_custom_members_map`/`get_custom_group_names`/`get_custom_all_stock_codes`，`:637` 起）
- `api_server.py` — `/api/custom/dashboard`(`:221`)、`/api/custom/scan`(`:244`)
