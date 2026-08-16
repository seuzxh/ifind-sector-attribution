---
title: "设计：知识图谱"
parent: "架构与设计"
nav_order: 7
---

# 设计方案：个股-板块知识图谱（KG）

> 状态：待 Review | 日期：2026-07-14
> 目标：用 iFinD 接口**一次性构造图谱雏形**，**每周定时维护**实现持续沉淀，并预留**多数据源扩展**（其他接口/数据商的行业、概念分类）。

---

## 一、目标与设计原则

1. **iFinD 单源起步**：首次构造只用现有 iFinD 接口（字典 + 成分股 + 个股概念），不引入外部依赖
2. **事实源单一**：图谱数据落 SQLite（现有 `sector_attribution.db`），图引擎只是"计算视图"，不双写、无一致性窗口
3. **持续沉淀 = 双时态**：边带 `valid_from/valid_to`，历史关系永不删除只"关闭"——任何时点的图谱状态可回放
4. **多源可插拔**：边带 `source` 字段，数据源以 Adapter 形式接入，未来加申万行业/问财/东财概念不动核心表结构
5. **静态骨架 + 动态强度**：归属关系（BELONGS_TO）是骨架，滚动相关性（corr）作为边权动态更新

## 二、总体架构（四层）

```
┌─ L0 拉取层  iFinD 接口（可扩展其他源）
│    接口5 板块字典 │ 接口2 板块→成分股 │ 接口1 个股→概念 │ (未来)申万/问财...
│         │  Source Adapter 统一产出 (stock, sector, props) 三元组
▼
┌─ L1 事实层  SQLite（单一真相，现有库内新增表）
│    concept_members(已有快照) ──► kg_snapshot(图谱版本)
│                                 kg_node / kg_edge(双时态) / kg_change
▼
┌─ L2 图计算层  NetworkX（按需构建，<1s 加载 7 万边）
│    社区发现(板块族群) │ 中心性(枢纽板块) │ 联动股(路径/共享板块)
│         │  结果写回 L1 普通表
▼
┌─ L3 消费层  API / 看板 / rotation_agent
     /api/kg/* 查询 │ 族群表喂轮动分析 │ 监控板块管理页展示族群
```

## 三、图谱 Schema（SQLite，4 张新表）

```sql
-- 1. 节点表：股票 + 板块统一建模
CREATE TABLE IF NOT EXISTS kg_node (
    node_id     TEXT PRIMARY KEY,        -- 'STOCK:600519.SH' / 'SECTOR:884091.TI'
    node_type   TEXT NOT NULL,           -- 'stock' | 'sector'
    code        TEXT NOT NULL,           -- 原始代码
    name        TEXT,                    -- 名称（股票名/板块名）
    sector_type TEXT,                    -- 板块节点专用: 'industry'(884) / 'concept'(885/886)；股票为 NULL
    props_json  TEXT,                    -- 扩展属性（如板块 index_code、上市板等）
    first_seen  TEXT NOT NULL,           -- 首次进入图谱日期
    last_seen   TEXT NOT NULL,           -- 最近一次确认存在的日期
    is_active   INTEGER DEFAULT 1        -- 长期消失(连续N周未被任何源确认)置0，不物理删
);
CREATE INDEX IF NOT EXISTS idx_kg_node_type ON kg_node(node_type, is_active);
CREATE INDEX IF NOT EXISTS idx_kg_node_code ON kg_node(code);

-- 2. 边表：双时态归属关系（核心）
CREATE TABLE IF NOT EXISTS kg_edge (
    edge_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id      TEXT NOT NULL,           -- 股票节点 'STOCK:xxx'
    dst_id      TEXT NOT NULL,           -- 板块节点 'SECTOR:xxx'
    edge_type   TEXT NOT NULL DEFAULT 'BELONGS_TO',
    source      TEXT NOT NULL,           -- 数据源: 'ifind_p03473' / 'ifind_concept' / 'sw_industry' / 'custom'
    valid_from  TEXT NOT NULL,           -- 关系生效日（快照日期）
    valid_to    TEXT,                    -- NULL=当前生效；非空=已失效（被剔除/源删除）
    props_json  TEXT,                    -- 边动态属性: {"member_date":"20260714","corr_20d":0.87,...}
    confidence  REAL DEFAULT 1.0         -- 多源交叉验证：单源命中<1，双源命中=1
);
CREATE INDEX IF NOT EXISTS idx_kg_edge_src ON kg_edge(src_id, valid_to);
CREATE INDEX IF NOT EXISTS idx_kg_edge_dst ON kg_edge(dst_id, valid_to);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_edge_open
    ON kg_edge(src_id, dst_id, source) WHERE valid_to IS NULL;  -- 每源每对只有一条生效边

-- 3. 图谱快照（版本管理，"持续沉淀"的锚点）
CREATE TABLE IF NOT EXISTS kg_snapshot (
    snapshot_id  TEXT PRIMARY KEY,       -- '20260714'（周快照日期）
    built_at     TEXT NOT NULL,
    node_count   INTEGER, edge_count     INTEGER,
    added_edges  INTEGER, removed_edges  INTEGER,
    sources      TEXT,                   -- 参与的 source 列表 json
    stats_json   TEXT                    -- 度分布/连通分量等统计
);

-- 4. 变更日志（每周 diff 产物，跟踪查询直接查这张）
CREATE TABLE IF NOT EXISTS kg_change (
    change_date TEXT NOT NULL,
    node_id     TEXT NOT NULL,
    edge_id     INTEGER,
    change_type TEXT NOT NULL,           -- 'edge_added' / 'edge_removed' / 'node_added' / 'node_deactivated'
    detail_json TEXT,
    PRIMARY KEY (change_date, node_id, edge_id)
);
```

**关键设计说明**：
- `(src, dst, source)` 唯一生效约束：同一对股票-板块可同时存在**多个来源的生效边**（iFinD 说属于、申万也说属于 → confidence 提升），这是多源扩展的根基
- `valid_to IS NULL` 表示生效——"当前图谱" = 所有 open 边，任意历史时点 = `valid_from <= d < valid_to`
- 层级关系（884→881→一级行业）未来作为 `edge_type='IS_CHILD_OF'` 的板块-板块边进入同一张边表，不用改结构

## 四、数据源适配层（多源扩展的接缝）

```python
# kg_sources.py —— 每个 Adapter 只需实现一个协议
class SourceAdapter(Protocol):
    source: str                                    # 'ifind_p03473'
    def fetch_pairs(self) -> List[Tuple[str, str, dict]]:
        """返回 [(stock_code, sector_code, props), ...] 全量当前归属"""

# 内置两个 iFinD 实现：
class IfindMembersAdapter:      # 接口2: 板块→成分股（主源，权威）
    source = "ifind_p03473"
    # 复用 SyncPipeline._fetch_concept_members_batch（636 板块 8 并发 ≈1-2分钟）

class IfindStockConceptAdapter: # 接口1: 个股→概念（交叉验证源）
    source = "ifind_concept"
    # 复用 batch_get_stock_concepts（5500 股 55 批 ≈1分钟）

# 未来扩展示例（只写 Adapter，不动核心）：
# class SwIndustryAdapter:   source = "sw_industry"    # 申万行业
# class WencaiConceptAdapter: source = "wencai"        # 问财概念
# class ManualTagAdapter:    source = "custom"          # 人工标注
```

**多源合并规则**：
| 情况 | 处理 |
|---|---|
| 两源都说属于 | 各建一条 open 边，`kg_edge` 查询时按 (src,dst) 去重 + confidence=1.0 |
| 仅主源（接口2）说属于 | 边 source='ifind_p03473'，confidence=0.8 |
| 仅验证源（接口1）说属于 | 边照建，confidence=0.6，标记 `props.only_source` 供审查 |
| 主源说不再属于 | 关闭该边 valid_to；验证源边不受影响 |

## 五、一次性构造（Bootstrap）

新增 `python main.py kg_init`：

```
Step 1  板块节点（~10s）
        接口5 拉字典全集 → SECTOR 节点（884/885/886 全收，含 sector_type）
Step 2  归属边——主源（~1-2min）
        复用现有最新 concept_members 快照（72,440 对，免重拉）；
        若快照超过 7 天则先调接口2 刷新
Step 3  归属边——交叉验证源（~1min）
        接口1 批量拉个股→概念，与 Step2 diff：
        - 双源命中 → confidence=1.0
        - 仅接口1  → 建 confidence=0.6 边（隔离板块：接口1 只返回 885/886 概念，
                     884 行业不在此源覆盖范围，属预期差异而非数据错误）
        - 仅接口2  → 保持 confidence=0.8
Step 4  股票节点（即时）
        从边表反推全部股票 → STOCK 节点（name 从 concept_members 的 stock_name 取）
Step 5  首个 kg_snapshot + 基础统计
        度分布 / 连通分量数 / 孤立板块 / 平均每股归属板块数
```

产出物：`kg_v0` 雏形 ≈ 6,166 节点（5,530 股 + 636 板块）+ ~7.2 万条 open 边 + 1 份快照统计。

## 六、每周维护（定时 diff，沉淀的核心）

新增 `python main.py kg_update`，crontab 参照现有 `run_push.sh` 模式挂载：

```bash
# 每周日 20:00 维护知识图谱（周末无行情波动，快照干净）
0 20 * * 0 /root/projects/2.monitor_940/ifind-sector-attribution/scripts/run_kg.sh update >> .../data/kg_update.log 2>&1
```

```
kg_update 流程（幂等，可重跑）：
1. 各 Adapter fetch_pairs() 拉最新全量（接口2 若当日已拉过则复用）
2. 与当前 open 边按 (src, dst, source) diff：
   new_pairs − open_edges → 逐条建新边(valid_from=今日, confidence 按命中源数)
   open_edges − new_pairs → 关闭边(valid_to=今日)
3. 节点维护：新股票/新板块建节点；连续 4 周未被确认的节点 is_active=0（不物理删）
4. 产出 kg_change 变更日志 + kg_snapshot 周快照
5. （可选开关）触发图分析任务更新族群/中心性缓存表
```

**"持续沉淀"的体现**：任何时点可回答——
- "这只股何时进的半导体材料" → 该边 `valid_from`
- "这个板块上周踢掉了谁" → `kg_change` 查 `edge_removed`
- "3 月 1 日时它属于哪些板块" → `valid_from <= '0301' AND (valid_to IS NULL OR valid_to > '0301')`

## 七、多源扩展路线（未来，按需启用）

| 阶段 | 接入源 | 增量价值 | 改动量 |
|---|---|---|---|
| P3 | 884→881 行业层级（`IS_CHILD_OF` 板块边） | 一/二级行业聚合视图、族群更准 | 1 个静态映射表 + 边类型复用 |
| P4 | 申万/中信行业分类 | 跨体系行业对照、机构口径归因 | 1 个 Adapter（拉源一次性灌入） |
| P5 | 问财/东财概念 | 概念口径互补，发现 iFinD 没打标的主题 | 1 个 Adapter + 爬取/接口适配 |
| P6 | 图谱可视化（Neo4j/Bloom） | 交互式探索 | schema 已按属性图设计，数据导出平移即可 |

## 八、动态边权（关联度，衔接此前的 ρ 方案）

`kg_edge.props_json.corr_20d` 作为边权，**每日盘后任务**增量更新（非每周）：
- 个股收益取 `daily_kline`，板块收益取接口3 的 `.TI` 指数 close 序列
- 只更新 open 边（~7.2 万条），20 日窗口滚动
- 图算法（族群/联动）把 corr 作为权重，弱关联边自动降权

## 九、图分析与消费（L2/L3）

```python
# kg_analysis.py —— NetworkX 按需构建（<1s），结果写回普通表
build_graph(valid_at=None, min_confidence=0.6) -> nx.Graph   # 时间旅行查询
detect_communities()   → kg_community(族群表, 喂 rotation_agent)
hub_sectors()          → 板块中心性（枢纽/细分标记, 喂监控板块管理页）
linked_stocks(code, k) → 联动股（共享板块数 + 平均 corr 排序）
```

API（挂现有 api_server，新区块）：
| 接口 | 用途 |
|---|---|
| `GET /api/kg/stock/{code}/sectors` | 个股归属（含 corr、生效时间、多源） |
| `GET /api/kg/sector/{code}/stocks` | 板块成分（图谱口径，含联动强度） |
| `GET /api/kg/linked/{code}` | 联动股 TopN |
| `GET /api/kg/communities` | 板块族群 |
| `GET /api/kg/changes?date=` | 变更日志（周 diff 结果） |

## 十、工程落地清单

| 项 | 文件 | 量级 |
|---|---|---|
| 建表 | `database.py` DDL 追加 4 表 | 小 |
| 源适配 | 新建 `kg_sources.py`（2 个 iFinD Adapter） | 小 |
| 构建与维护 | 新建 `kg_builder.py`（bootstrap / weekly_update / diff） | 中 |
| 图算法 | 新建 `kg_analysis.py` + NetworkX 依赖 | 中 |
| CLI | `main.py` 加 `kg_init` / `kg_update` / `kg_stats` | 小 |
| 定时 | `scripts/run_kg.sh` + crontab 周日 20:00 | 小 |
| API | `api_server.py` 加 5 个查询路由 | 小 |
| 文档 | AGENTS/README/DB manifest 同步 | 小 |

**iFinD 调用量预算（每周一次）**：接口2 636 次请求（8 并发 ~1.5min）+ 接口1 55 批（~1min）+ 接口5 7 批（~3s）——远低于日频行情调用，配额无压力。

## 十一、验证指标（首个快照 + 每周巡检）

- 结构：节点/边数、孤立板块数、平均每股归属数（预期 ~13，与接口1 历史均值对照）
- 质量：confidence 分布（双源占比应 >60%）、无行情板块清单（如 884253 类，标记待下架）
- 变更：每周 added/removed 数量曲线（异常暴增 → 源数据问题告警）
- 回放：随机抽 10 只股验证"任意历史时点归属"查询与人工核对一致

## 十二、分期实施

| 期 | 内容 | 交付判据 |
|---|---|---|
| **P1 雏形** | 4 表 + 2 Adapter + kg_init + 首个快照 | 7.2 万边入库，统计报告产出 |
| **P2 沉淀** | kg_update + crontab + 变更日志 | 连续 2 周 diff 正常，历史可回放 |
| **P3 消费** | NetworkX 族群/联动 + 5 个 API + ρ 边权 | 族群符合盘面直觉，联动股可查 |
| **P4+ 扩展** | 层级边 / 申万 / 可视化 | 按需 |

---

## 附一：与既有方案的关系

- 上一轮"current/change/corr 三表"方案的目标（当前视图、变更跟踪、动态强度）在本方案中由 `kg_edge(双时态) + kg_change + props.corr` 完整覆盖，且多了多源与版本化能力
- `concept_members` 保持事实快照角色不动；`watched_concepts`（监控勾选）与图谱解耦，互不影响
- `refresh_observe_members`（管理页刷新按钮）与 `kg_update` 共享底层拉取函数，各自独立调度

## 附二：技术选型说明——NetworkX 是什么、为什么选它

**NetworkX 是 Python 的图分析库**（2004 年至今，Python 生态最广用的图工具）：把"节点+边"装进内存跑图算法，纯 Python、`pip install networkx` 即用，无服务、无部署。

**为什么图谱分析不用 SQL/不用图数据库**：

| 想做的事 | SQL | NetworkX | Neo4j |
|---|---|---|---|
| 个股属于哪些板块 | ✅ 简单 | ✅ | ✅ |
| 和某股共享板块最多的股 | 多层 JOIN | 几行代码 | ✅ Cypher |
| 板块族群（社区发现） | ❌ 做不了 | `louvain_communities()` 一个函数 | ✅ GDS 库 |
| 两股几跳连通 | 递归 CTE 痛苦 | `shortest_path()` | ✅ |
| 枢纽板块（中心性） | 自己写 | `degree_centrality()` | ✅ |
| 浏览器交互式可视化 | ❌ | ❌ | ✅（最优） |
| 运维成本 | — | **零**（纯库） | 新服务+备份+监控 |

**选型逻辑**：本项目图谱规模 ~7 万边，NetworkX 全量加载 <1 秒，绰绰有余；项目全 Python 栈零改造；数据事实源始终在 SQLite，NetworkX 只是按需构建的计算视图（时间旅行查询、族群分析时建图，结果写回普通表）。Neo4j 为亿级边设计，在此规模属过度工程，且引入双写一致性问题——**待交互式可视化成为刚需时再平移**，数据模型不变。

```python
# 典型用法：从 SQLite 建图 → 跑算法 → 写回
G = nx.Graph()
for stock, sector, corr in conn.execute("SELECT src,dst,corr FROM kg_edge WHERE valid_to IS NULL"):
    G.add_edge(stock, sector, weight=corr)
groups = louvain_communities(G, weight="weight")   # 板块族群
hubs = nx.degree_centrality(G)                      # 枢纽板块
```
