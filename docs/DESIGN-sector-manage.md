# 监控板块管理

> 状态：已实现 | 日期：2026-07-12

## 一、需求与设计

让用户可视化勾选要监控的概念板块。`watched_concepts` 保存持久化选择，再与最新成分股数 10~500 的资格规则取交集，形成三条链路共同使用的有效范围：板块强度监控、全市场强势归类、daily 归因。

### 展示与交互
- Tab `🛠️ 监控板块管理`（路由 `/sector_manage`）
- 表格列：`☑ | 代码 | 名称 | 涨幅 | 实体 | 涨 | 跌 | 涨停 | 3日涨幅 | 5日涨幅 | 层级`
- 层级筛选（按 `concept_code[:3]`）：全部 / 884 三级行业 / 885 概念 / 886 概念
- 搜索框（代码/名称模糊匹配）+ 全选/取消筛选 + 保存勾选
- 当日涨幅、实体、涨跌家数和涨停家数优先走 iFinD 概念指数实时行情；3日/5日累计涨幅走 iFinD 历史行情，实时缺项时用历史行情末日回退

### 关键决策（review 已确认）
- **存储**：新表 `watched_concepts(concept_code PK, added_at)`
- **空集行为**：表空时看板/scan 显示"未配置监控板块"；daily 归因退回 config 兜底（避免漏算）
- **scan 纳入范围**：去掉 `startswith("884")` 硬编码，统一按勾选集归类
- **层级**：884→三级行业、885/886→概念板块（无 parent/level 字段，靠前缀）
- **成员数边界**：仅允许最新成分股数 10~500（含边界）的板块；管理列表、保存接口和实时引擎均过滤
- **保留 `config.SECTOR_POOL_CODES`**：作首次初始化种子 + daily 兜底

## 二、数据模型

```sql
CREATE TABLE watched_concepts (
    concept_code TEXT PRIMARY KEY,
    added_at     TEXT NOT NULL
);
```
首次建表若空，灌入 `config.SECTOR_POOL_CODES`（884×259）作种子，保证上线即有默认值。

## 三、三链路联动

| 链路 | 数据来源 | 空集行为 |
|---|---|---|
| 板块强度监控 | `get_watched_concept_codes()`（`_ensure_maps`） | "未配置监控板块"提示 |
| 全市场强势归类 | `self._members_map`（已由 watched 限定） | "未配置监控板块"提示 |
| daily 归因 | `get_a_share_concept_codes()` → 读 watched | 退回 config 兜底（避免漏算） |

## 四、API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/sector_manage/list` | 成分股数 10~500 的候选板块 + 实时/多周期指标 + 勾选状态 |
| GET | `/api/sector_manage/watched` | 当前有效勾选代码列表 |
| POST | `/api/sector_manage/save` | 过滤越界代码后全量覆盖勾选清单并清看板缓存 `{concept_codes:[...]}` |

## 五、文件清单
- 新增：`sector_manage.py`（多周期涨幅计算）、`frontend/src/api/sectorManage.ts`、`frontend/src/views/SectorManagePage.vue`
- 改：`database.py`（建表+get/save+`get_a_share_concept_codes` 改读 watched）、`realtime_engine.py`（`_ensure_maps`+`scan_market_groups`+空集提示）、`api_server.py`（3 路由）、`router/index.ts`、`AppLayout.vue`

## 六、复用点
- `calc_period_return_df(db, date, days)`（core_calculator.py:188）—— 传 `days=1/3/5` 算多周期，3日是新增用法（算子已参数化）
- `get_concept_members_map(concept_codes)`（database.py）—— 批量读成分股
- 板块涨幅 = 成分股均值（与 `calc_all_sectors_strength` 的 `s1_return` 口径一致）
