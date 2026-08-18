---
title: "设计：知识图谱"
parent: "架构与设计"
nav_order: 7
---

# 设计方案：个股-板块知识图谱（KG）

> 状态：**P1 已实施**（2026-08-17 实测落地，见 §五/§十一）｜设计定稿 2026-07-14
> 目标：用 iFinD 接口**一次性构造图谱雏形**，**每周定时维护**实现持续沉淀，并预留**多数据源扩展**（其他接口/数据商的行业、概念分类）。

**一句话理解**：把"5,554 只股票 ↔ 636 个板块"的归属关系存成一张**带时间、带来源、带可信度**的关系网——任何一天、任何一只股，都能查它当时挂在哪些板块下、是谁说的、有多可信。

**它不是什么**：不是图数据库产品（Neo4j），数据就是现有 SQLite 里的 4 张普通表；NetworkX/cytoscape 只是按需取用的"计算/展示工具"。

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

**真实数据长这样**（P1 实测，2026-08-17 快照）：

`kg_node` 两类节点各一行：
| node_id | node_type | code | name | sector_type | props_json |
|---|---|---|---|---|---|
| `SECTOR:884091.TI` | sector | 884091.TI | 半导体材料 | industry | `{"member_count":29,"monitorable":true}` |
| `STOCK:600519.SH` | stock | 600519.SH | 贵州茅台 | NULL | `{}` |

`kg_edge`（600519 的 3 条边示例）：
| src_id | dst_id | source | valid_from | valid_to | confidence |
|---|---|---|---|---|---|
| STOCK:600519.SH | SECTOR:885520.TI（沪股通） | ifind_p03473 | 20260817 | NULL | **1.0**（接口1也确认） |
| STOCK:600519.SH | SECTOR:885338.TI（融资融券） | ifind_p03473 | 20260817 | NULL | **0.8**（仅主源） |
| STOCK:某ST股 | SECTOR:885xxx.TI | ifind_concept | 20260817 | NULL | **0.6**（仅验证源） |

**双时态怎么用——一个时间线例子**（P2 周维护生效后的效果）：

```
假设 600519 的"白酒Ⅲ"归属发生过变化：

  edge_id=101  valid_from=20260817  valid_to=NULL       ← 当前生效
  edge_id=98   valid_from=20260601  valid_to=20260810   ← 8/10 被剔除（历史保留）

三问三答：
  Q「它现在属于白酒Ⅲ吗」   → 查 valid_to IS NULL              → 是
  Q「它何时进的白酒Ⅲ」     → 看 valid_from                    → 2026-08-17（本周）
  Q「8/15 时它属于哪些板块」→ valid_from<=0815 AND (valid_to IS NULL OR valid_to>0815)
                             → 按当日生效快照回放，不受之后变化影响
```

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

**可信度（confidence）的定义**：衡量一条"股票→板块"归属关系被**几个相互独立的数据源**确认，是证据强度而非关联强度：

| 值 | 含义 | 判定条件 | 实测占比 |
|---|---|---|---|
| **1.0** | 双源确认 | 接口2（板块成分）和接口1（个股概念）都返回该归属 | 88.2%（67,360 条） |
| **0.8** | 仅主源 | 只有接口2 说属于。**注意结构性原因**：接口1 只覆盖 885/886 概念，所以全部 259 个 884 行业板块的边封顶 0.8——不代表数据差 | 7.9%（6,049 条） |
| **0.6** | 仅验证源 | 只有接口1 说属于，边带 `props.only_source` 标记。**典型含义**：较新的概念板块（如"机器人概念 885517"在 ths_concept_dict 字典里还没有），接口1 已打标而接口2 的成分股列表未收录 | 3.8%（2,926 条） |

两个独立来源（一个从板块拉成分、一个从个股拉概念）交叉验证是数据质量的基本手段：单源可能有口径偏差或更新滞后，两源一致则归属几乎无争议。

**它不是什么**：不是"个股与板块走势的关联强度"（那是 P3 挂在边上的 corr_20d 滚动相关性）。confidence 管"**是否属于**"（静态证据），corr 管"**当下跟谁走**"（动态强度），两者互补。

**踩坑记录（2026-08-17 实发）**：0.6 边的概念码若不在字典里，bootstrap 只按字典建板块节点会漏建 → 边指向不存在节点（JOIN 查不到、统计丢失）。已修复：`kg_builder` 步骤 5.5 扫描边涉及的板块码，缺失的用接口1 返回的概念名补建节点（`props.in_dict=false` 标记，共 14 个），供后续字典清洗时参考。

### 4.1 iFinD 接口清单与作用（均已实测）

端点前缀：`quantapi` = quantapi.51ifind.com/api/v1，`ft` = ft.10jqka.com.cn/api/v1。

| # | 接口 | HTTP 端点 | ifind_client 封装 | KG 中的作用 | 使用阶段 | 单次调用量 |
|---|---|---|---|---|---|---|
| 5 | 概念字典 | `quantapi/basic_data_service`（ths_index_short_name_index 等 5 个 indicator） | `batch_get_concept_basic_info` | 建 SECTOR 节点（代码/名称） | P1 起 | 7 批（100/批）≈3s |
| 2 | 板块→成分股 | `quantapi/data_pool`（reportname=p03473） | `_fetch_concept_members_batch`（8 并发） | 归属边**主源**；股票节点来源 | P1/P2 | 636 次 ≈1.5min |
| 1 | 个股→概念 | `quantapi/basic_data_service`（ths_the_ths_concept_index_stock） | `batch_get_stock_concepts` | 归属边**交叉验证源**；只覆盖 885/886（不含 884，预期行为） | P1/P2 | 55 批 ≈15s |
| 3 | 历史日K | `ft/cmd_history_quotation` | `get_history_quotation` | 动态边权（板块指数与个股 close 序列算 20 日 ρ） | P3 | 周期性批量 |
| 6 | 实时快照 | `quantapi/real_time_quotation` | `batch_get_realtime_quotation` | 盘中抽样校验（可选） | 按需 | 按需 |

**请求/响应速览**（P1 实跑真实形态）：

- **接口2**（单概念，不支持批量，靠 8 线程并发）：
  ```json
  请求: {"reportname":"p03473","functionpara":{"iv_date":"20260817","iv_zsdm":"884091.TI"},
         "outputpara":"p03473_f001,p03473_f002,p03473_f003"}
  响应: tables[0].table = {"p03473_f002":["688432.SH","688584.SH",...],   ← 股票代码
                            "p03473_f003":["炬光科技","柏楚电子",...]}     ← 股票名
  ```
- **接口1**（批量 100 股/批，返回逗号分隔串需拆分）：
  ```json
  请求: {"codes":"600519.SH,000858.SZ,...","indipara":[
           {"indicator":"ths_the_ths_concept_index_stock","indiparams":["2026-08-17"]}]}
  响应: tables[0].table = {"ths_the_ths_concept_index_stock":["融资融券,白酒,沪股通,国企改革,..."]}
  ```
- **接口3**（板块指数与个股同一端点通用）：
  ```json
  请求: {"codes":"884091.TI","indicators":"close","startdate":"2026-08-01","enddate":"2026-08-17",
         "functionpara":{"Fill":"Previous","Interval":"D","CPS":"6"}}
  响应: tables[0].table = {"close":[12963.2, 12801.5, ...]}   ← 日收盘序列，算 ρ 用
  ```

**凭据注意**：access_token 7 天有效，401 自动续刷；refresh_token 失效会报 **-1301** 且不可自愈（2026-07-14 实发过一次，导致行情接口 500）。`refresh_access_token` 现已支持**轮换式 refresh_token 自动写回 config_local.py**（`_persist_refresh_token`），防再次复发。

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

### 5.1 实测记录（P1，2026-08-17）

`python main.py kg_init` 实跑 **16.9 秒**完成（当天成分股快照已存在 → 接口2 零调用，仅接口1 拉 5,553 只股）：

```
$ python main.py kg_init
[KG] SECTOR 节点 636 个入库
[KG-SOURCE] 主源复用当天快照 20260817（零接口调用）
[KG-SOURCE] 主源产出 73409 条归属对
[KG-SOURCE] 验证源拉取 5553 只个股的概念归属（接口1）...
[KG-SOURCE] 验证源产出 70286 条归属对
[KG] 归属对合并：双源 67360 | 仅主源 6049 | 仅验证源 2926
[KG] 边 76335 条入库
[KG] STOCK 节点 5554 个入库
==============================================================
知识图谱 P1 构建报告
节点：SECTOR 636 + STOCK 5554
边：76335 条（双源 67360 / 仅主源 6049 / 仅验证源 2926）
平均每股归属板块数：13.74（预期 ~13）
孤立板块：2 个  例：['884253.TI', '884259.TI']
双源交叉验证率（双源/主源）：91.8%（设计假设 >60%）
枢纽板块 Top10：融资融券 3852 / 深股通 1875 / 沪股通 1639 / 国企改革 1468
              / 专精特新 1230 / 人工智能 1083 / 华为概念 1003 / 芯片概念 915 ...
==============================================================
[KG] 族群初探：6202 节点 / 76335 边，连通分量 1 个，Louvain 社区数：7
  #1 板块129 股票1327：改性塑料、消费电子、第三代半导体、AI手机 ...   ← 泛电子/科技系
  #2 板块121 股票798：华为盘古、东数西算(算力)、在线教育、垂直软件 ...  ← 软件算力系
  #3 板块110 股票938：细胞免疫治疗、动物保健、智能医疗 ...            ← 医药系
  #4 板块105 股票1046：充电桩、换电概念、光伏概念、高铁 ...            ← 新能源基建系
  #5 板块91  股票741：煤化工、调味发酵品、白酒Ⅲ、证券Ⅲ ...           ← 传统消费金融系
```

**结论**：族群划分**符合盘面直觉**（科技/算力/医药/新能源/传统五分天下），枢纽板块符合金融常识（融资融券这类宽口径概念天然是枢纽）——图谱数据质量足以支撑 P2/P3 继续。

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

### 6.1 P2 实施与实测记录（2026-08-17）

已落地：`kg_update` 命令（`--force` / `--skip-verify`）+ `scripts/run_kg.sh` + crontab 周日 20:00。

**diff 引擎要点**（与设计的差异说明）：一个 (股,板块) 对只有**一条 open 边**（bootstrap 聚合存储、update 关旧开新均保持），因此旧状态从**边自身 confidence 恢复**（1.0=双源 / 0.8=仅主源 / 0.6=仅验证源），而非按 source 字段——双源边的 source 是主源，按 source 判态会把 76,335 条边全部误判为"升级"（实施首跑实测踩过此坑，已修复）。

状态机：`none→X` 建边 / `X→none` 关边 / `单源→both` 升级(conf→1.0) / `both→单源` 降级 / `主↔验证` 源切换——所有迁移都是"关旧开新"，历史永不改写。

**实测三连**（当天数据未变 + 人为漂移）：
```
① 数据一致时：    diff：新增 0 | 移除 0 | 升级 0 | 降级 0 | 源切换 0   ← 引擎不乱动
② 删一条真实边：  diff：新增 1（茅台→白酒Ⅲ 被正确重建自愈）
③ 插一条假归属：  diff：移除 1（假边被关闭 valid_to=今日，记 edge_removed）
   回放验证：     「20260801 时该归属存在吗」SQL 返回 1（valid_from≤0801<valid_to）✓
```

**每周日的 crontab**：
```bash
0 20 * * 0 .../scripts/run_kg.sh >> .../data/kg_update.log 2>&1
```

## 七、多源扩展路线（未来，按需启用）

| 阶段 | 接入源 | 增量价值 | 改动量 |
|---|---|---|---|
| P3 | 884→881 行业层级（`IS_CHILD_OF` 板块边） | 一/二级行业聚合视图、族群更准 | 1 个静态映射表 + 边类型复用 |
| P4 | 申万/中信行业分类 | 跨体系行业对照、机构口径归因 | 1 个 Adapter（拉源一次性灌入） |
| P5 | 问财/东财概念 | 概念口径互补，发现 iFinD 没打标的主题 | 1 个 Adapter + 爬取/接口适配 |
| P6 | 图谱可视化（Neo4j/Bloom） | 交互式探索 | schema 已按属性图设计，数据导出平移即可 |

## 八、动态边权（关联度，衔接此前的 ρ 方案）

`kg_edge.corr_20d` 作为边权（P3 落地时从 props 提升为**独立列**，便于批量 UPDATE）：
- 个股收益取 `daily_kline`，板块收益取接口3 的 `.TI` 指数 close 序列
- 只更新 open 边（~7.2 万条），20 日窗口滚动；窗口锚定本地 `daily_kline` 最新交易日
- 图算法（族群/联动）把 corr 作为权重，弱关联边自动降权

命令：`python main.py kg_corr [--window 20] [--no-corr-weight]`（先算 ρ 再跑族群；`--no-corr-weight` 可退化为无权族群）

### 8.1 P3 实测记录（2026-08-17）

| 指标 | 实测 |
|---|---|
| ρ 覆盖 | 72,635 / 76,335 条边（**95.2%**，未覆盖=无行情板块/新股样本不足） |
| 平均 \|ρ\| | 0.4163 |
| 族群数（Louvain，\|corr\| 权重） | 6 个（板块侧规模 143/128/113/108/90/66） |

**正确性抽验（贵州茅台 600519）**：ρ 最高 = 白酒Ⅲ（**0.6177**），最低 = 沪股通（**0.2861**）——主营一致性决定联动强度，说明 ρ 能有效区分"同板块但弱关联"的边（如茅台同时属于 13 个板块，ρ 排序后一眼看出哪个才是"真主线"）。

**两个实现坑（已修复，留给后来人）**：
1. **窗口对齐顺序**：必须"先按交易日 inner join 去掉 NaN，再 `tail(window)`"——顺序反了会因两条序列长度不同（个股停牌缺天）导致窗口起点错位，覆盖率直接归零。
2. **窗口锚点**：板块指数每次拉到今天，但本地 `daily_kline` 可能滞后——窗口必须锚定 `daily_kline` 的 MAX(trade_date)，否则两条序列无交集、结果全空。当前 daily_kline 停在 2026-07-24，实际 ρ 窗口仅 10 个交易日（`min_obs` 已放宽到 8）；**恢复 daily 同步后重跑 `kg_corr` 即自动升级为完整 20 日窗口**。

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
| `GET /api/kg/locate?codes=\|group=` | **组合定位**：一批股票 → 共同指向的板块（富集/命中双指标，见 §9.3） |
| `GET /api/kg/locate/groups` | 自选分组列表（组合定位下拉用） |

### 9.1 图谱可视化：cytoscape.js 集成进看板（已选型）

**选型结论**：采用 **cytoscape.js** 在现有 Vue SPA 内新增「图谱」Tab（路由 `/kg`，`KgGraphPage.vue`），与看板同源同部署，零新增服务。pyvis 仅作为 P1 开发期的临时验证手段（命令行导出 HTML），不作为交付物；Neo4j 保留为未来大规模交互探索的备选（见附二）。

**选型依据**：MIT 开源、万级节点渲染性能好、Vue 3 集成成熟（`npm i cytoscape`）、支持自定义布局与样式映射——满足"集成进现有看板"的要求，且与 FastAPI+Vue 的现有栈零冲突。

**三种视图模式（子图优先，永不渲染全图）**：

| 视图 | 内容 | 规模 | 交互 |
|---|---|---|---|
| ① 板块投影图（默认） | 节点=636 板块（按族群着色），边=成分重叠度（Jaccard≥阈值） | ~636 节点/数千边 | 拖拽/缩放/悬停显成分重叠数；点板块→侧栏看简介与联动板块 |
| ② 个股星型图 | 输入股票代码 → 该股 + 其所属板块 + 联动股 TopN（按 corr） | <100 节点 | 双击板块→局部展开成分股（corr TopN 截断） |
| ③ 板块展开图 | 点某板块 → 板块 + 成分股（corr 排序 TopN） | <N+1 节点 | 点成分股→切换到该股星型图（视图②③互通） |
| ④ 组合定位（表格，非图） | 一批股票（自选分组或粘贴代码）→ 板块富集表 | ≤top_n 行 | 点板块名→跳视图③；排序切换（富集倍数/命中数） |

**工程拆分**：
- 前端：`frontend/src/views/KgGraphPage.vue` + `src/api/kg.ts` + cytoscape 依赖；族群色板与板块类型（行业/概念）形状区分（圆/矩形）；边权滑杆（按 corr 过滤弱关联）
- 后端供数（返回 cytoscape elements 格式 `{nodes:[{data:{id,label,...}}], edges:[{data:{source,target,weight}}]}`）：
  - `GET /api/kg/graph/projection` — 板块投影图（含族群 id/颜色分组）
  - `GET /api/kg/graph/star?code=600519.SH&limit=20` — 个股星型子图
  - `GET /api/kg/graph/sector/{code}?limit=30` — 板块展开子图
- 性能约束：单视图节点上限可配（默认 500），超限提示收窄条件；投影图边按 Jaccard 阈值（默认 0.3）过滤

**交付期**：P3（与 §九 查询 API、ρ 边权同期——边权就绪后投影图/星型图才有强弱层次）。**已于 2026-08-17 交付**，见 §9.2。

### 9.2 P3 交付实测（2026-08-17）

- **后端**：8 个 `/api/kg/*` 路由全部经 TestClient 实测通过（5 查询 + 3 供数，比原设计的 5 个多出 graph 三兄弟）
- **前端**：Playwright 内置浏览器 8 项用例全过、0 控制台错误——
  - 板块族群（默认视图）：650 板块节点 · 84 条重叠边（Jaccard 阈值 0.3），6 个族群色标图例与拖拽/缩放正常
  - 个股关联：600519 → 30 节点星型图（该股 + 所属板块 + 联动股）
  - 板块成分：884091 → 29/29 只成分股全部渲染
  - 交互：单击节点出侧栏信息、双击板块↔板块成分/个股↔个股关联视图互切
- **截图**：`/tmp/kg_projection.png`（族群投影）、`/tmp/kg_star.png`（星型）、`/tmp/kg_sector.png`（成分展开）

### 9.3 组合定位（2026-08-18 增补）

**一句话**：给一批股票（自选分组 / MCP 条件选股结果 / 手动粘贴），回答"它们共同指向哪些板块、哪里异常聚集"。

**入口**：图谱 Tab 第 ④ 视图「🎯 组合定位」，或 `GET /api/kg/locate?codes=600519,000858` / `?group=CPO`（代码可不带后缀；同花顺导出的分组名带尾随空格，后端已 TRIM 容错）。

**双指标**（互补，页面可一键切换排序）：

| 指标 | 公式 | 回答的问题 | 典型噪音 |
|---|---|---|---|
| 命中数 hits | 组内属于该板块的股票数 | 这组股的"最大公约数"题材是什么 | 融资融券/深股通类大基数枢纽板块永远靠前 |
| 富集倍数 lift | (hits/组内总数) ÷ (板块成员数/全市场股票数) | 哪些**小圈子**异常聚集（隐性生活圈） | 成员数缺失（字典外板块）时为 NULL，排最后 |

**实测一（自选分组 CPO，22 只）**：富集榜首 通信网络设备及器件（10 命中/40 成员，**63×**）、F5G概念 49×、光学元件 32×——比"共封装光学(CPO) 21/22 命中但仅 26×"更能暴露这组股的真实生活圈；切命中数排序则榜首是融资融券(22)，验证了噪音过滤的必要性。

**实测二（MCP 选股："涨幅大于7%小于12%，未涨停"，62 只，2026-08-18）**：富集榜无主线高度聚集（最高仅 10×），但 农业种植(5 命中)/粮食概念/玉米 密集成一小簇 + MicroLED/PET铜箔 小圈子异动；命中数视角主基调为 芯片17/机器人16/新能源车16——两视角结合即"今日准涨停股群体画像"。

**实现**：`kg_analysis.locate_sectors()`（一条 GROUP BY SQL + Python 算 lift），API 层只做代码后缀解析（复用 `_kg_resolve`）。

**消费方（2026-08-18 接入）**：**全市场强势归类页已改用本归类规则**——`kg_analysis.classify_hits()`（locate 的明细版，含每股 ρ 与板块命中清单）替代旧"按勾选板块数命中"，默认 lift 排序、`order=hits` 可切。实测（"涨幅7%~12% 未涨停" 62 只）：lift 榜首 其他食品 17.9×/电子纸 10×（小圈子聚集），hits 榜首 融资融券 48（大基数噪音，验证双指标必要性）；归类范围由勾选集 100 板块扩到全量 650 板块。详见 DESIGN-strong-stock-scan.md。

## 十、工程落地清单

| 项 | 文件 | 量级 |
|---|---|---|
| 建表 | `database.py` DDL 追加 4 表 | 小 |
| 源适配 | 新建 `kg_sources.py`（2 个 iFinD Adapter） | 小 |
| 构建与维护 | 新建 `kg_builder.py`（bootstrap / weekly_update / diff） | 中 |
| 图算法 | 新建 `kg_analysis.py` + NetworkX 依赖 | 中 |
| CLI | `main.py` 加 `kg_init` / `kg_update` / `kg_query` / `kg_corr` | 小 |
| 定时 | `scripts/run_kg.sh` + crontab 周日 20:00 | 小 |
| API | `api_server.py` 加 5 个查询路由 + 3 个 graph 供数路由 | 小 |
| 可视化 | `frontend/src/views/KgGraphPage.vue` + `src/api/kg.ts` + cytoscape 依赖 + 路由/Tab | 中 |
| 文档 | AGENTS/README/DB manifest 同步 | 小 |

**iFinD 调用量预算（每周一次）**：接口2 636 次请求（8 并发 ~1.5min）+ 接口1 55 批（~1min）+ 接口5 7 批（~3s）——远低于日频行情调用，配额无压力。

## 十一、验证指标（首个快照 + 每周巡检）

- 结构：节点/边数、孤立板块数、平均每股归属数（预期 ~13，与接口1 历史均值对照）
- 质量：confidence 分布（双源占比应 >60%）、无行情板块清单（如 884253 类，标记待下架）
- 变更：每周 added/removed 数量曲线（异常暴增 → 源数据问题告警）
- 回放：随机抽 10 只股验证"任意历史时点归属"查询与人工核对一致

**P1 实测结果（2026-08-17）**：
| 指标 | 目标 | 实测 | 判定 |
|---|---|---|---|
| 节点/边数 | ~6.2k / ~7.2万 | 6,190 / 76,335 | ✓ |
| 平均每股归属数 | ~13 | **13.74** | ✓ |
| 双源交叉验证率 | >60% | **91.8%** | ✓✓ 远超预期 |
| 孤立板块 | 少量 | 2 个（884253/884259，即两个无行情指数） | ✓ 符合已知 |
| 族群直觉 | 人工判断 | 5 大族群清晰（科技/算力/医药/新能源/传统） | ✓ |
| 幂等 | 重复执行拒绝 | 重复 kg_init 被拒 ✓ | ✓ |
| 服务无回归 | import 正常 | api_server 31 路由正常 | ✓ |

## 十二、分期实施

| 期 | 内容 | 交付判据 | 状态 |
|---|---|---|---|
| **P1 雏形** | 4 表 + 2 Adapter + kg_init + 首个快照 + 族群初探 | 7.6 万边入库，统计报告产出 | ✅ **已完成**（2026-08-17） |
| **P2 沉淀** | kg_update + crontab + 变更日志 | 连续 2 周 diff 正常，历史可回放 | ✅ **已实施**（2026-08-17，引擎零漂移/自愈/回放已验证；"连续 2 周"待时间沉淀） |
| **P3 消费** | NetworkX 族群/联动 + 8 个查询 API + ρ 边权 + **cytoscape 图谱 Tab（投影/星型/展开三视图）** | 族群符合盘面直觉，看板内可交互探索图谱 | ✅ **已完成**（2026-08-17，ρ 覆盖 95.2%、8 API + 浏览器 8/8 用例通过，见 §8.1/§9.2） |
| **P4+ 扩展** | 层级边 / 申万 / Neo4j 大规模探索 | 按需 | 待实施 |

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

**选型逻辑**：本项目图谱规模 ~7 万边，NetworkX 全量加载 <1 秒，绰绰有余；项目全 Python 栈零改造；数据事实源始终在 SQLite，NetworkX 只是按需构建的计算视图（时间旅行查询、族群分析时建图，结果写回普通表）。Neo4j 为亿级边设计，在此规模属过度工程，且引入双写一致性问题——保留为 P4+ 大规模交互探索的备选（届时数据模型不变，平移即可）。

**可视化补充**（2026-07-14 定稿）：看板内交互可视化已选 **cytoscape.js** 方案（见 §9.1），不依赖 Neo4j——上表"浏览器交互式可视化"一列在本项目的落地路径是"FastAPI 供数 + cytoscape.js 渲染"，而非引入图数据库。

```python
# 典型用法：从 SQLite 建图 → 跑算法 → 写回
G = nx.Graph()
for stock, sector, corr in conn.execute("SELECT src,dst,corr FROM kg_edge WHERE valid_to IS NULL"):
    G.add_edge(stock, sector, weight=corr)
groups = louvain_communities(G, weight="weight")   # 板块族群
hubs = nx.degree_centrality(G)                      # 枢纽板块
```

## 附三：如何查看当前 KG（实用指南）

P3 起已有图形界面（看板「🕸️ 知识图谱」Tab），命令行/SQL 是补充手段。查看方式按易用度排序：

### 方式负一：浏览器图谱 Tab（P3 起可用，最直观）

打开看板（`http://<host>:8000/#/kg`）→「🕸️ 知识图谱」，三视图：

| 视图 | 怎么用 | 典型问题 |
|---|---|---|
| 板块族群（默认） | 650 板块按族群着色，拖「重叠阈值」滑杆收放 | "哪些板块其实是一伙的？" |
| 个股关联 | 输入股票代码（如 600519）→ 星型图 | "这只股和谁联动最强？" |
| 板块成分 | 输入板块代码（如 884091）→ 成分按 ρ 排序 | "这个板块里谁是主线？" |

交互：单击节点 → 侧栏详情；双击板块节点 → 跳板块成分视图，双击个股 → 跳个股关联视图。

### 方式零：kg_query / kg_corr 命令（命令行一行搞定）

```bash
PY=/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python

# ① 个股 → 它属于哪些板块（行业/概念自动分组，P3 起含 ρ 排序）
PYTHONPATH=. $PY main.py kg_query 600519

# ② 个股 + 联动股（共享板块最多的股票，图谱最实用的查询）
PYTHONPATH=. $PY main.py kg_query 600519 --linked 5
# → 贵州茅台的联动股：五粮液/泸州老窖（共享7板块）/山西汾酒/伊力特（共享6）
#   并列出具体共享了哪些板块

# ③ 板块 → 成分股清单
PYTHONPATH=. $PY main.py kg_query 884091

# ④ 重算 ρ 边权 + 族群（daily 同步恢复后执行一次即可）
PYTHONPATH=. $PY main.py kg_corr
```

代码/板块码带不带后缀（600519 / 600519.SH；884091 / 884091.TI）都能识别，输出直接可读，无需 SQL。

### 方式一：sqlite3 命令行（需要灵活定制查询时）

```bash
sqlite3 -header -column data/sector_attribution.db
```

三个最常用查询（已实测，可直接粘贴）：

```sql
-- ① 某只股票属于哪些板块（换 code 即查任意股）
SELECT n2.name AS 板块, e.confidence AS 可信度
FROM kg_edge e
JOIN kg_node n1 ON e.src_id = n1.node_id
JOIN kg_node n2 ON e.dst_id = n2.node_id
WHERE n1.code = '600519.SH' AND e.valid_to IS NULL
ORDER BY e.confidence DESC;
-- → 沪股通1.0 / 同花顺漂亮100 1.0 / 国企改革 1.0 / 超级品牌 1.0 / 白酒Ⅲ 0.8 ...

-- ② 某个板块有哪些成分股（含可信度）
SELECT n1.code, n1.name, e.confidence
FROM kg_edge e
JOIN kg_node n1 ON e.src_id = n1.node_id
JOIN kg_node n2 ON e.dst_id = n2.node_id
WHERE n2.code = '884091.TI' AND e.valid_to IS NULL
ORDER BY e.confidence DESC;

-- ③ 枢纽板块排名（去重后归属股票数 TopN）
SELECT n2.name, COUNT(*) AS 股票数
FROM kg_edge e JOIN kg_node n2 ON e.dst_id = n2.node_id
WHERE e.valid_to IS NULL GROUP BY e.dst_id ORDER BY 2 DESC LIMIT 10;
-- → 融资融券3852 / 深股通1875 / 沪股通1639 / 国企改革1468 ...
```

### 方式二：Python（用 database.py 现成方法）

```bash
/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python
```
```python
from database import Database
db = Database()
db.get_kg_nodes("sector")        # 全部板块节点（含 props 里的 member_count）
edges = db.get_kg_open_edges()   # 全部生效边（76,335 条）
# 简易"查一只股"
[e for e in edges if e["src_id"] == "STOCK:600519.SH"]
```

### 方式三：重跑族群初探（看图谱结构）

```bash
PYTHONPATH=. /root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python -c "
from database import Database
from kg_builder import _explore_communities, _load_concept_names
db = Database()
_explore_communities(db, _load_concept_names(db))
"
```
→ 打印连通分量数、Louvain 族群 Top5（科技/算力/医药/新能源/传统消费金融系）。

**想看图？** 打开看板「🕸️ 知识图谱」Tab 即可（P3 已交付，见 §9.1/§9.2 三视图）——方式三这类命令行族群输出适合写脚本做告警/巡检，不适合人眼看图。
