# 股池归因定时推送

> 状态：已实现并部署 | 实现核对：2026-07-31

## 一、产品目的

在交易日 9:33 / 9:45 / 10:00 / 14:30 四个时刻，按各自条件调 iFinD MCP 选股，
对**自选分组**和**全市场**分别做强势归类（**完全复用**【自选股强势归类】页面的
`scan_custom_groups` / `scan_market_groups`），把结果格式化成飞书卡片推送到 webhook。

归类逻辑与 `docs/DESIGN-strong-stock-scan.md` 描述的页面一致，本功能只是在指定时间
触发并把结果推到飞书，不改归类算法。

## 二、触发条件（slot → 选股 query）

| slot | 时间 | query |
|---|---|---|
| `933`  | 09:33 | `实体涨幅大于3%或最大涨幅大于3%;成交额大于6亿;上市时间大于5天` |
| `945`  | 09:45 | `实体涨幅大于3%或最大涨幅大于3%;成交金额大于10亿` |
| `1000` | 10:00 | `成交金额大于20亿;且实体涨幅大于4%或最大涨幅大于4%` |
| `1430` | 14:30 | `涨幅大于7%并且小于12.1%;未涨停；非ST` |

**query 原样传给 MCP，不做任何翻译**（含「实体涨幅」「最大涨幅」「或」OR 逻辑）。
盘中 MCP 反映实时数据；盘后调用因收盘会返回当日收盘结果（部分 query 的涨跌幅可能为 0）。
slot→query 映射集中在 `scan_push.py::PUSH_SLOTS`。

## 三、触发机制

crontab + CLI 子命令（沿用兄弟项目 `ifind-concept-trend` 的 `run_sync.sh`+crontab 模式）：

```
33 9  * * 1-5  .../scripts/run_push.sh 933  >> .../data/push_933.log 2>&1
45 9  * * 1-5  .../scripts/run_push.sh 945  >> .../data/push_945.log 2>&1
0  10 * * 1-5  .../scripts/run_push.sh 1000 >> .../data/push_1000.log 2>&1
30 14 * * 1-5  .../scripts/run_push.sh 1430 >> .../data/push_1430.log 2>&1
```

- `scripts/run_push.sh <slot>`：激活 conda `vibe-trading` python、cd 项目根、`PYTHONPATH=.`、
  `source .env`（若有）、exec `python main.py push --slot <slot>`。
- `scripts/install_push_cron.sh`：**幂等**安装 4 条 crontab（已存在 `run_push.sh` 标记则跳过），
  保留其他已有条目。卸载：`crontab -l | grep -v 'run_push.sh' | crontab -`。
- cron 用 `1-5` 只做工作日粗筛；**交易日（节假日）校验在程序内**用 `TradeCalendar.is_trading_day()`
  完成，非交易日自动跳过不推送。

## 四、数据链路

```text
crontab → run_push.sh → main.py push --slot <slot>
  → scan_push.run_push(slot)
      ├─ is_trading_day? 否 → return（不推送）
      ├─ run_classification(slot)
      │     ├─ scan_custom_groups(query)   # 复用 realtime_engine，自选分组归类
      │     └─ scan_market_groups(query)   # 复用 realtime_engine，全市场归类
      │     （两侧互相隔离，一个失败不影响另一个）
      ├─ build_scope_message(slot,"custom",...)  → push_to_feishu  # 自选蓝头卡片
      └─ build_scope_message(slot,"market",...)  → push_to_feishu  # 全市场紫头卡片
          （两条独立推送，先自选后全市场，各自重试，一条失败不影响另一条）
```

**自选与全市场拆成两条独立推送**：自选蓝头标题"自选分组归类"、全市场紫头标题"全市场归类"，便于在飞书里分别查看与转发。两条共享同一次选股归类结果（query 相同），只是归类维度不同。

## 五、配置

- `config.PUSH_WEBHOOK_URL`（环境变量 `PUSH_WEBHOOK_URL`，默认空）：飞书 webhook 地址。
  实际值放在 `config_local.py`（已 gitignore）。
- 飞书卡片为 `interactive`，已实测 `<font color>` 与 `column_set` 在本 webhook 可用。
  **自选与全市场各发一条独立卡片**（自选蓝头、全市场紫头），每条版面：
  - 头部 → 条件行 → 分割线 → 单侧内容（**标题** + 三列灰底统计卡片 + 分组明细）→ 备注页脚。
  - **头部三列统计**用 `column_set`：选股池(蓝) / 命中或可归类(红) / 涉及分组(绿)，替代旧的单行文本。
  - **涨幅按幅度上色**（`_change_color`）：≥9.8% 深红(涨停级) / ≥5% 红 / >0 橙 / <0 绿 / 0 灰。分组"均涨"与个股涨幅均上色。
  - 每组明细：`▸ 板块名　命中 n/总数　均涨 <色>` + `代码　名称　涨幅<色>`。空结果也推送（提示"无符合条件股票"）。

## 六、命令

| 命令 | 作用 |
|---|---|
| `python main.py push --slot 1430` | 交易日校验+选股归类+推送飞书 |
| `python main.py push --slot 1430 --dry-run` | 同上但不推送，打印消息 JSON（调试用） |
| `python tests/test_scan_push.py` | 离线单测（不打真实网络/MCP/webhook） |

## 七、关键代码

| 文件 | 职责 |
|---|---|
| `scan_push.py` | `PUSH_SLOTS` 配置、`run_classification`（复用 scan_*_groups）、`build_scope_message`（单侧卡片）、`build_feishu_message`（合并卡片，兼容用）、`push_to_feishu`、`run_push`（交易日校验+自选/全市场分别推送） |
| `main.py` | `push` 子命令（`--slot`/`--dry-run`），`cmd_push` |
| `scripts/run_push.sh` | crontab 启动包装（conda python + PYTHONPATH） |
| `scripts/install_push_cron.sh` | 幂等安装 4 条 crontab |
| `tests/test_scan_push.py` | 消息渲染、归类编排容错、非交易日跳过、dry-run、推送路径 |
