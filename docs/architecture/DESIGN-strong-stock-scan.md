---
title: "设计：强势归类扫描"
parent: "架构与设计"
nav_order: 4
---

# 强势股归类扫描

> ⚠️ **现状注记（2026-09）**：本文为历史设计。选股已从 MCP `search_stocks` 迁移到 REST `smart_stock_picking`（ACCESS_TOKEN 鉴权）；全市场归类已改为知识图谱富集（全量板块、lift 排序、每股 corr_20d），旧版"按勾选板块逐板块数命中"已下线。现行口径见[页面数据与计算](../guides/data-sources.md)。

> 状态：已实现并在用 | 现役实现核对：2026-07-24；全市场归类规则升级为知识图谱富集归类：2026-08-18

## 一、产品目的

先用 iFinD MCP `search_stocks` 按自然语言条件从全市场选股，再把命中股映射到用户关心的分组。页面回答的是“强势股集中在哪些自选主题或监控板块”，不是个股概念贡献拆解。

顶部有两个独立路由：

| 页面 | 路由 | 后端接口 | 归类范围 |
|---|---|---|---|
| 自选强势归类 | `/scan` | `GET /api/custom/scan` | `custom_group` 全部分组 |
| 全市场强势归类 | `/market_scan` | `GET /api/market/scan` | **知识图谱全量板块**（650 个；2026-08-18 起不再限定勾选集，勾选板块带 `is_watched` 标记高亮） |

两页复用 `frontend/src/views/ScanPage.vue`，由 `route.name` 选择接口。切换两页时会自动重新查询，不能沿用上一页结果。

## 二、数据链路

```text
自然语言 query
  → MCP stock.search_stocks（全市场收盘选股）
  → 解析 Markdown 得到 {code, name, change_ratio}
  → 按页面范围归类（自选=分组交集；全市场=图谱富集）
  → 命中数、富集倍数/覆盖率、平均涨幅、命中股票明细（含每股 ρ）
```

两条链路均不读取分时序列，也没有时间条或 3 秒轮询。

### 自选强势归类

1. MCP 返回全市场命中集合。
2. 与 `get_custom_all_stock_codes()` 取交集。
3. 按 `get_custom_members_map()` 归入全部自选分组。
4. `pool_size` 是 MCP 全市场命中数；`hit_total` 是自选范围内去重命中数。

一只股票属于多个自选分组时，每个分组都会展示，但 `hit_total` 只计算一次。

### 全市场强势归类（KG 富集版，2026-08-18）

1. MCP 返回全市场命中集合。
2. `kg_analysis.classify_hits()` 在知识图谱（650 板块、open 边）上聚合：每股归属的板块各计命中，板块带富集倍数。
3. **默认按富集倍数 lift 降序**（组内命中率 ÷ 板块成员占全市场比例），消除“融资融券/深股通”类大基数枢纽板块的命中噪音，暴露异常聚集的小圈子；`order=hits` 切回命中数（看最大公约数）。
4. 每个板块返回命中股明细，每股带 `corr_20d`（该股与板块的 20 日 ρ）；`watched_concepts` 勾选的板块带 `is_watched=true`。
5. `pool_size` 是 MCP 全市场命中数；`hit_total` 是图谱内可归类去重命中数（`min_hits=2` 过滤散点，`top_n=30` 截断）。

与旧版（按勾选板块逐板块数命中）的关键差异：归类不再依赖「监控板块管理」勾选集——图谱全量口径意味着**没勾选的板块也能被发现**，勾选状态只是高亮标记。成员数口径：优先概念字典快照 `member_count`，14 个字典外板块（如 机器人概念）用图谱度数兜底。

## 三、接口合同

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string（必填） | iFinD MCP 可理解的自然语言选股条件 |
| `order` | `lift`\|`hits` | 仅 `/api/market/scan`：lift=富集倍数降序（默认），hits=命中数降序 |
| `min_hits` | int（默认 2） | 仅 `/api/market/scan`：板块最少命中数 |
| `top_n` | int（默认 30） | 仅 `/api/market/scan`：返回板块数上限 |

返回结构（全市场页，多出的字段加注释）：

```json
{
  "query": "涨幅大于7%并且小于12.1%；未涨停；非ST",
  "pool_size": 62,
  "hit_total": 41,
  "group_hit_count": 30,
  "order": "lift",
  "groups": [
    {
      "group_id": "885906.TI",
      "group_name": "电子纸",
      "sector_code": "885906.TI",        // KG 版：板块代码（同 group_id）
      "sector_type": "concept",          // KG 版：industry/concept
      "is_watched": false,               // KG 版：是否监控板块管理已勾选
      "lift": 10.0,                      // KG 版：富集倍数
      "hit_count": 3,
      "member_total": 27,
      "coverage": 0.048,                 // KG 版口径：hit_count / pool_size（组内占比）
      "hit_avg_change": 8.35,
      "hits": [
        {"code": "000000.SZ", "name": "示例股票", "change_ratio": 9.2, "corr_20d": 0.374}
      ]
    }
  ]
}
```

自选页返回结构相同但无 `order/sector_*/is_watched/lift/corr_20d` 字段，`coverage = hit_count / member_total`。

## 四、前端交互

- 4 组预置自然语言条件，默认自动执行第 4 组。
- 用户可输入条件并保存到浏览器 `localStorage`。
- 自定义条件可重命名、删除；重命名只改显示标签，不改实际 query。
- 结果按分组手风琴展示，可同时展开多个分组。
- 全市场页：排序单选（富集倍数/命中数，切换即重查）；板块头部显示「富集 N×」徽标与「已监控」标记；个股 chip 悬停显示 ρ。
- 页面提示明确说明全市场归类口径为知识图谱富集。

## 五、刷新与缓存

- MCP 选股由 `_mcp_search_with_retry()` 执行，仅在“未找到/无符合”类结果上重试。
- 自选 JSON 变更由 `/api/custom/check_reload` 检测；进入自选归类页前会检查并重导。
- 管理页保存勾选板块后调用 `realtime_engine.clear_cache()`（KG 归类不受影响，只影响 is_watched 之外依赖 members_map 的链路）。
- 扫描结果本身不做持久化。

## 六、关键代码

| 文件 | 职责 |
|---|---|
| `realtime_engine.py` | 两条扫描、MCP 重试、Markdown 解析、分组统计（市场页归类委托 `kg_analysis.classify_hits`） |
| `kg_analysis.py` | `classify_hits`：KG 富集归类核心（lift 计算 + 字典外板块度数兜底） |
| `api_server.py` | `/api/custom/scan`、`/api/market/scan`（透传 order/min_hits/top_n） |
| `frontend/src/views/ScanPage.vue` | 查询表单、预置条件、排序切换、富集徽标/已监控标记、手风琴 |
| `frontend/src/api/scan.ts` | 类型与接口封装 |
| `frontend/src/layouts/AppLayout.vue` | 自选 JSON 更新检查 |

## 七、边界

- 本功能使用 MCP 收盘/日频选股结果，不等于盘中分时强度。
- 一股可归入多组，因此各组 `hit_count` 相加可能大于 `hit_total`。
- 富集倍数偏爱小板块（2/10 命中即 17.9×），看结果时结合命中数一起判断；命中数排序则被大基数板块主导——两个视角互补，页面上可一键切换。
- ρ（`corr_20d`）基于本地 `daily_kline`，同步滞后时 ρ 样本变旧（详见 DESIGN-knowledge-graph.md §8.1）。
- MCP 工具及响应格式可能变化；解析失败时接口返回 `error` 和有限长度的 `raw_preview`。
