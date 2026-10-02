---
title: "API 参考"
parent: "使用指南"
nav_order: 2
description: 全部 REST 端点的入参、返回结构与调用示例
---

# API 参考

后端为 FastAPI，默认监听 `0.0.0.0:8000`（`python main.py server` 或 systemd 服务）。所有接口返回 JSON；本文按功能分组列出全部端点。

试 API 最快的方式：服务启动后浏览器打开 `http://localhost:8000/docs`（FastAPI 自带的交互式文档，可直接发起请求）。

## 总览

| 分组 | 端点数 | 用途 |
|---|---|---|
| 盘后数据 | 4 | 板块强度排名、个股/组合归因、概念字典 |
| 实时看板 | 6 | 板块/自选看板切片、成员排序、缓存管理 |
| 开盘题材 | 1 | 冻结归因与盘中分时的只读聚合 |
| 历史与竞价 | 2 | 历史收盘看板、集合竞价看板 |
| 强势归类 | 2 | REST 智能选股 + 归类 |
| 板块管理 | 5 | 勾选保存、后台刷新 |
| 基础设施 | 4 | 日历、时段状态、自选重导 |

## 盘后数据

### GET /api/sector/rankings

板块强度排名（多周期融合分）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `date` | str | 最新入库日 | YYYYMMDD |
| `top_n` | int | 10 | 返回前 N |

```bash
curl "http://localhost:8000/api/sector/rankings?date=20260817&top_n=10"
```

### POST /api/attribution/stock

个股多概念归因（L1 权重法：贡献 = 权重 × 概念收益）。

```json
// 请求体
{ "stock_codes": ["600519.SH", "000001.SZ"], "date": "20260817" }
```

`date` 可省略，默认最近入库日。返回每只股票对各概念的涨幅贡献占比。

### POST /api/attribution/portfolio

组合归因：按持仓暴露匹配强势板块并预警。

```json
// 请求体
{ "holdings": [{"code": "600519.SH", "weight": 0.4}, {"code": "000001.SZ", "weight": 0.6}], "date": "20260817" }
```

### GET /api/realtime/sector

最新板块实时强度排名（轻量版，无成分股明细）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `top_n` | int | 5 | 返回前 N |

## 概念字典

### GET /api/concept/list

全部 A 股概念板块列表（含行业码与概念板块码）。

### GET /api/concept/members

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `concept_code` | str | 必填 | 如 `884001.TI` |
| `date` | str | 最新快照 | YYYYMMDD |

概念成分股（永久缓存语义：不传日期取 `MAX(date)` 最新一份）。

## 实时看板

看板类接口共用两个"时空"参数：

- `trade_date`（YYYYMMDD）：默认今天；传历史日期拉该日全天分时后切片。
- `snapshot_time`（HH:MM）：截止时刻，如 `"09:50"`；缺省或 `latest` 为最新时刻。切片纯内存，毫秒级响应。

### GET /api/realtime/dashboard

板块实时看板（管理页有效板块）。另支持 `top_n`（默认 10）。响应含 Top10 强势板块、Bottom10 弱势板块与各板块成分股（四维评分排名）。

### GET /api/custom/dashboard

自选分组看板。参数同上；分组来自 `custom_group` 表，响应额外含持仓标注字段（`holding_stocks` / `holding_in_group`）。

### GET /api/dashboard/members

单板块/分组全量有效成员排序（看板点击表头时按需调用）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `concept_code` | str | 必填 | 板块码或分组 id |
| `trade_date` / `snapshot_time` | str | 同上 | 同看板 |
| `custom_mode` | bool | false | true=自选分组模式 |
| `sort_key` | str | score | change/speed/acceleration/open_change/score |
| `descending` | bool | true | 排序方向 |

返回重新排序后的前 10 名（排序基于全量成员，避免只排可见 10 只）。

### POST /api/realtime/clear_cache

清空分时序列与结果缓存（切日 / 调试用）。无请求体。

### POST /api/auction/clear_cache

清空集合竞价缓存。

## 开盘题材

### GET /api/opening-strength/dashboard

供第八个 Tab `#/opening-themes` 使用；只读所选日期的 `FROZEN` 归因快照和三个源股池候选分时。Theme 限 `884` 行业和 `885/886` 概念，不读取实时 Membership 或盘中重跑归因，不写排名。自动盘前调度、集合竞价、自定义静态题材、WebSocket 和排名持久化均排除。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `trade_date` | str | 必填 | 八位 `YYYYMMDD`，必须为有效日历日期 |
| `snapshot_time` | str | 最新有效分钟 | `HH:MM`，不早于 09:30；不接受 `latest` 字符串 |

```bash
curl "http://localhost:8000/api/opening-strength/dashboard?trade_date=20261008&snapshot_time=09:45"
```

省略时点取最新有效分钟；位于分钟间或晚于末点时，向下取不晚于请求的最近有效分钟。每股各自取不晚于该分钟的末点；仅 `trading` 的 09:30 起数据有效。09:30 使用自身作为变化基线；其后基线为时间轴上一有效分钟，缺失基线不虚构数值。历史与实时使用同一聚合函数；`mode` 由所选日期是否为中国标准当日决定。

成功响应（200）字段：

| 字段 | 内容 |
|---|---|
| `trade_date`, `run_id`, `mode` | 日期、当前冻结版本、`realtime` / `historical` |
| `snapshot_time`, `latest_time`, `available_times` | 实际切片分钟、末分钟、全部可用分钟轴 |
| `candidate_count`, `theme_count`, `data_health` | 去重冻结候选数、题材数、有效行情候选占比（0～1） |
| `themes` | 完整题材列表，含下列指标、风险标签及 `contributors` |
| `acceleration_theme_codes`, `breadth_theme_codes` | 按对应变化榜排序的题材代码，引用 `themes` |
| `generated_at`, `cache_status` | 中国标准时间生成戳、`fresh` / `hit` / `stale` |

设 `r_s=(last_price_s/pre_close_s-1)*100`、`w_sg` 为冻结归因权重，`V_g` 为有有效行情的归因股（昨收正数、价格有限且不晚于请求分钟），`P_g` 为其中上涨股。公式均在有效集上计算：

```text
contribution_sg = w_sg * r_s
level_g = Σ(V_g, w_sg * r_s) / Σ(V_g, w_sg)
momentum_1m_g = level_g(t) - level_g(previous_minute)
up_ratio_g = |P_g| / |V_g|
breadth_delta_1m_g = up_ratio_g(t) - up_ratio_g(previous_minute)
support_weight_g = Σ(P_g, w_sg) / Σ(V_g, w_sg)
positive_contribution_sg = max(contribution_sg, 0)
top1/top3_concentration_g = 最大前1/3个正贡献之和 / 全部正贡献之和
data_health_g = valid_quote_count / attributed_stock_count
```

`level`、涨幅、贡献和加速以百分比数值/百分点表示（`2.35` 即 2.35%）；上涨占比、支撑权重、集中度和健康度为 0～1 比例。扩散变化为占比差（−1～1），UI 中乘 100 显示百分点。API 四位小数，计算保留全精度。无有效权重时强度为 `null`；无正贡献时集中度为 `null`。`source_pool_diversity` 为上涨股覆盖的源池种类数（0～3）。

主榜按有指标优先、`level` 降序、`momentum_1m` 降序、题材代码升序；加速/扩散榜分别按各自变化值降序、代码升序，仅纳入有效行情股数 ≥2、健康度 ≥0.60 且变化值非空的题材。无行情题材仍保留在末尾。风险标签为单股驱动、低支撑（多股题材但上涨股不足2）、高度集中（首股≥0.70）、数据不足（健康度<0.60）、行情滞后（当天末分钟落后超过2个已开市分钟，休市不累计）。

`contributors` 按贡献降序、股票代码升序；负贡献保留，缺行情在末尾。字段包含代码/名称、冻结 `attribution_weight/confidence/reason_codes/source_pool_ids`、`quote_time/pre_close/last_price/avg_price/turnover`、`return_pct/contribution/positive_contribution/has_quote`；缺行情的行情与贡献数值为 `null`。成交额等原始字段不参与排序。

行情缓存以日期和去重候选集合为键：当天 15 秒过期，历史日期进程内稳定；同键并发只抓一次，其余请求读取旧成功结果或等待。刷新失败有旧缓存时返回 `cache_status=stale`，无缓存返回 503。聚合结果缓存 3 秒，以 `run_id` 与实际时点为键；行情身份/滞后状态变化会立即重算。

本接口错误结构独立于既有 HTTP 200 业务错误约定：

```json
{
  "error": {
    "code": "SNAPSHOT_NOT_FOUND",
    "message": "该日期尚未生成盘前冻结快照",
    "retryable": false
  }
}
```

| HTTP | 错误码 | 可重试 | 场景 |
|---|---|---|---|
| 422 | `INVALID_REQUEST` | false | 日期缺失/无效、时点格式无效或早于09:30 |
| 404 | `SNAPSHOT_NOT_FOUND` | false | 日期无冻结快照，不触发行情或归因 |
| 503 | `QUOTE_DATA_UNAVAILABLE` | true | 快照存在但无可用盘中点/所选时点无数据 |
| 503 | `QUOTE_PROVIDER_FAILED` | true | 行情获取失败且无可用旧缓存 |

无快照须维护人员在项目根目录人工运行 `PYTHONPATH=. python main.py opening-premarket --date YYYYMMDD`；替换已有冻结版本遵守 09:30 后 `--force-replace` 保护。已记录的生产四表截至 2026-10-03 为空，发布时可用预期 404/页面提示验收，不能据此声称真实排名可见；本阶段本地实现等待审查与发布。

## 历史与竞价

### GET /api/history/dashboard

历史看板（读已入库收盘数据）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `date` | str | 必填 | YYYYMMDD |
| `top_n` | int | 10 | 前 N 板块 |
| `force_calc` | bool | false | 无数据时自动拉取计算（约 2 分钟） |
| `scope` | str | sector | `sector`=当前勾选板块；`custom`=自选分组 |

### GET /api/auction/dashboard

集合竞价看板（9:15~9:25，基于逐点 `ref_price` 推算涨幅）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `trade_date` | str | 今天 | 传历史日期回看该日竞价 |
| `snapshot_time` | str | 末点 | 截止时刻 HH:MM |

## 强势归类

两个端点同 schema，仅归类范围不同；选股由 iFinD REST `smart_stock_picking`（ACCESS_TOKEN）执行，与 MCP 配额无关。

### GET /api/custom/scan —— 自选强势归类

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | str | 必填，自然语言条件，如 `涨幅大于7%并且小于12.1%；未涨停；非ST` |

命中股 = REST 选股结果 ∩ 自选分组股票，按自选分组归类。

### GET /api/market/scan —— 全市场强势归类

入参同上。命中股按管理页当前勾选板块归类；未勾选板块时返回错误。

两者响应结构一致：

```json
{
  "query": "涨幅大于7%并且小于12.1%；未涨停；非ST",
  "pool_size": 106,
  "hit_total": 42,
  "group_hit_count": 18,
  "groups": [
    {
      "group_id": "884001.TI", "group_name": "示例板块",
      "hit_count": 3, "member_total": 25, "coverage": 0.12,
      "hit_avg_change": 8.6,
      "hits": [{"code": "000955.SZ", "name": "示例股票", "change_ratio": 8.8}]
    }
  ]
}
```

一股属多组时各组各计，`hit_count` 之和可能大于去重的 `hit_total`。

## 板块管理

### GET /api/sector_manage/list

可管理板块候选列表（最新成分股数 10~500）。`date` 可选。

### GET /api/sector_manage/watched

当前勾选的监控板块。

### POST /api/sector_manage/save

全量覆盖保存勾选。

```json
{ "concept_codes": ["884001.TI", "884002.TI"] }
```

### POST /api/sector_manage/refresh

触发后台刷新 884/885/886 字典与成分股（立即返回，后台线程执行）。

### GET /api/sector_manage/refresh/status

查询刷新进度 / 结果。

## 基础设施

### GET /api/trade_calendar

| 参数 | 类型 | 说明 |
|---|---|---|
| `year` | int | 可选；不传返回近 3 年 |

返回 `{count, trade_days: [...], today}`。`today` 为服务端权威日期，前端据此设默认值避免时区错位。

### GET /api/session_status

交易时段状态：`{is_trading_day, phase, next_open_time, next_trade_day, now}`。`phase` ∈ pre_open / auction / pre_morning / morning / lunch / afternoon / closed。前端据此决定轮询节奏。

### GET /api/dates

已入库的板块强度日期列表。

### POST /api/custom/check_reload

检测自选分组 JSON 是否变更（mtime 比对），变更则自动全量重导。前端切入自选 Tab 时调用。

## 错误约定

- 既有接口业务错误返回 `{"error": "中文原因"}`（HTTP 200），如选股接口失败、未配置监控板块。开盘题材使用上文结构化错误及 404/422/503。
- 参数错误由 FastAPI 校验返回 422。
- iFinD 侧 401 由客户端自动刷新 token 重试，调用方无需处理。
