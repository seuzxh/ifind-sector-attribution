# 强势股归类扫描

> 状态：已实现并在用 | 现役实现核对：2026-07-24

## 一、产品目的

先用 iFinD MCP `search_stocks` 按自然语言条件从全市场选股，再把命中股映射到用户关心的分组。页面回答的是“强势股集中在哪些自选主题或监控板块”，不是个股概念贡献拆解。

顶部有两个独立路由：

| 页面 | 路由 | 后端接口 | 归类范围 |
|---|---|---|---|
| 自选强势归类 | `/scan` | `GET /api/custom/scan` | `custom_group` 全部分组 |
| 全市场强势归类 | `/market_scan` | `GET /api/market/scan` | “监控板块管理”当前勾选板块 |

两页复用 `frontend/src/views/ScanPage.vue`，由 `route.name` 选择接口。切换两页时会自动重新查询，不能沿用上一页结果。

## 二、数据链路

```text
自然语言 query
  → MCP stock.search_stocks（全市场收盘选股）
  → 解析 Markdown 得到 {code, name, change_ratio}
  → 按页面范围取交集并分组
  → 命中数、覆盖率、平均涨幅、命中股票明细
```

两条链路均不读取分时序列，也没有时间条或 3 秒轮询。

### 自选强势归类

1. MCP 返回全市场命中集合。
2. 与 `get_custom_all_stock_codes()` 取交集。
3. 按 `get_custom_members_map()` 归入全部自选分组。
4. `pool_size` 是 MCP 全市场命中数；`hit_total` 是自选范围内去重命中数。

一只股票属于多个自选分组时，每个分组都会展示，但 `hit_total` 只计算一次。

### 全市场强势归类

1. MCP 返回全市场命中集合。
2. `RealtimeEngine._ensure_maps()` 从 `watched_concepts` 加载当前勾选板块及成分股。
3. 仅输出至少命中一只股票的勾选板块。
4. `pool_size` 是 MCP 全市场命中数；`hit_total` 是可归入当前勾选板块的去重命中数。

未勾选任何监控板块时返回明确错误，不回退到 884 全集或旧 watchlist。

## 三、接口合同

两个接口只有一个必填查询参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string | iFinD MCP 可理解的自然语言选股条件 |

返回结构：

```json
{
  "query": "涨幅大于7%并且小于12.1%；未涨停；非ST",
  "pool_size": 106,
  "hit_total": 42,
  "group_hit_count": 18,
  "groups": [
    {
      "group_id": "884001.TI",
      "group_name": "示例板块",
      "hit_count": 3,
      "member_total": 25,
      "coverage": 0.12,
      "hit_avg_change": 8.6,
      "hits": [
        {"code": "000001.SZ", "name": "示例股票", "change_ratio": 8.8}
      ]
    }
  ]
}
```

`groups` 按命中数、命中股平均涨幅降序排列。`coverage = hit_count / member_total`。

## 四、前端交互

- 4 组预置自然语言条件，默认自动执行第 4 组。
- 用户可输入条件并保存到浏览器 `localStorage`。
- 自定义条件可重命名、删除；重命名只改显示标签，不改实际 query。
- 结果按分组手风琴展示，可同时展开多个分组。
- 全市场页同时显示“全市场筛出数量”和“勾选板块内归类数量”，避免统计口径混淆。
- 页面提示明确说明全市场归类范围来自“监控板块管理”。

## 五、刷新与缓存

- MCP 选股由 `_mcp_search_with_retry()` 执行，仅在“未找到/无符合”类结果上重试。
- 自选 JSON 变更由 `/api/custom/check_reload` 检测；进入自选归类页前会检查并重导。
- 管理页保存勾选板块后调用 `realtime_engine.clear_cache()`，下一次全市场归类重新加载板块映射。
- 扫描结果本身不做持久化。

## 六、关键代码

| 文件 | 职责 |
|---|---|
| `realtime_engine.py` | 两条扫描、MCP 重试、Markdown 解析、分组统计 |
| `api_server.py` | `/api/custom/scan`、`/api/market/scan` |
| `frontend/src/views/ScanPage.vue` | 查询表单、预置条件、范围提示、手风琴 |
| `frontend/src/api/scan.ts` | 类型与接口封装 |
| `frontend/src/layouts/AppLayout.vue` | 自选 JSON 更新检查 |

## 七、边界

- 本功能使用 MCP 收盘/日频选股结果，不等于盘中分时强度。
- 一股可归入多组，因此各组 `hit_count` 相加可能大于 `hit_total`。
- 全市场页不会展示无法归入当前勾选板块的股票，但会在 `pool_size` 中保留全市场命中总数。
- MCP 工具及响应格式可能变化；解析失败时接口返回 `error` 和有限长度的 `raw_preview`。
