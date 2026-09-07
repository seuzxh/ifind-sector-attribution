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
| 历史与竞价 | 2 | 历史收盘看板、集合竞价看板 |
| 强势归类 | 2 | REST 智能选股 + 归类 |
| 板块管理 | 5 | 勾选保存、后台刷新 |
| 基础设施 | 5 | 日历、时段状态、轮动分析、自选重导 |

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

### GET /api/rotation/analyze

板块轮动分析（**SSE 流式**，`text/event-stream`）。三阶段：数据采集 → 第一性分析 → 对抗审查 → 综合结论；`data:` 事件为 `{"type":"delta","text":"..."}` 增量，结束发 `{"type":"done"}`。依赖 `LLM_API_KEY`。

## 错误约定

- 业务错误统一返回 `{"error": "中文原因"}`（HTTP 200），如选股接口失败、未配置监控板块。
- 参数错误由 FastAPI 校验返回 422。
- iFinD 侧 401 由客户端自动刷新 token 重试，调用方无需处理。
