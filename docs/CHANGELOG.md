# 更新日志

本文件记录 ifind-sector-attribution 项目的版本改动，与 git commit 历史对应。

## [Unreleased] - 2026-06-14

### 新增
- **盘中实时监控可视化网站**：FastAPI + 单页 HTML + plotly.js，支持实时/历史双模式
  - `stock_scorer.py`：成分股四维综合评分（涨幅 0.4 / 涨速 0.2 / 实体涨幅 0.2 / 涨停 0.2），涨停判定按板块（主板 9.8% / 创业科创 19.5% / 北交 29%）
  - `realtime_engine.py`：实时引擎，接口4 拉全市场 1min K → 构造 realtime_df → 算板块强度 + 成分股排名，带 10 秒内存缓存
  - `templates/index.html`：单页前端，日期选择 + Top10/Bottom10 双柱状图 + 成分股卡片 + 15 秒轮询，plotly.js 走 CDN
- **新 API 路由**：`/api/realtime/dashboard`（实时看板）、`/api/history/dashboard`（历史看板）、`/api/dates`（可用日期列表）
- **`init_concept_universe` 流程**：扫描全市场股票补全 `885xxx/886xxx` 概念板块码的字典+成分股+映射，解决双概念编码体系脱节问题（归因从 0 → 5500+ 股票）
- **多周期融合**：`calc_multi_period_score` 启用 1d/5d/20d 三周期评分，5d/20d 用累计涨幅（期末 close / 期初 preClose - 1）
- **A股过滤**：`config.py` 新增 `is_a_share_code` / `is_a_share_concept` 判定函数 + 前缀白名单；全链路（init/daily/计算）限定 A 股
- **`purge` 命令**：`main.py purge [--vacuum]` 删除已入库海外数据，幂等可复用
- **`get_daily_kline_by_date_range`**：区间 K 线查询，支撑多周期计算
- **并发拉取**：`init_concept_members` 改 `ThreadPoolExecutor` 8 线程并发，加进度打印与失败汇总
- **`MIN_MEMBER_COUNT` 配置**：板块强度计算过滤迷你概念（命中 K 线成分股 < 6 不参与排名）
- **架构文档**：`docs/ARCHITECTURE.md` 记录双编码体系、永久缓存语义、多周期算法、A股过滤策略、实时监控架构

### 修复
- **日期键不匹配**：`get_stock_concepts` / `get_concept_stocks` / `get_concept_members` 日期参数改为可选，不传时取 `MAX(date)` 最新一份；同时修复日期格式不统一（带/不带横杠）问题
- **`concept_returns` 覆盖不全**：归因的概念收益从"查概念指数 K 线"改为"成分股涨幅均值"，复用全市场股票 K 线
- **nan 传染**：三层防御（concept_returns 过滤 nan 成分股、跳过 nan 涨幅股票、nan concept_return 视为 0）
- **`data/` 目录缺失**：`Database.__init__` 自动创建 DB 父目录
- **`calc_multi_period_score` 三大问题**：日期格式 `strptime` 错误、数据获取逻辑错误（三周期用同份数据）、未被调用导致 score_5d/20d 恒为 0
- **`init_concept_members` 1121 次串行**：改为 8 线程并发，约 1-2 分钟完成

### 变更
- `run_daily` 的 `all_codes` 改为可选，不传则自动从成分股表反查全市场 A 股股票池
- `cmd_daily` 不再硬编码 7 只测试代码
- `init_concept_dict` 跳过海外行业指数（861/871 等）
- API `/api/concept/members` 不再用"今天"兜底，`date=None` 直接取最新缓存

### 实测数据（2026-06-08 ~ 06-12 全市场 A 股）
- 每日：5527 条 K 线 → 1068 板块多周期评分 → 5500 股票归因
- 板块强度 Top 排名准确反映市场主线（机器人/电源设备/有色金属）
- 归因逻辑自洽（平安银行=蓝筹概念、同花顺=科技金融、中信证券=券商概念）
- 多周期融合捕捉中期趋势（铜板块 6/12 登顶，前几日单日排名靠后）
- **实时监控验证**（6/12 09:30~09:35 模拟）：5512 股拉取成功，Top3=航天装备/磁性材料/印制电路板，成分股 301323 涨 4.71%/涨速 8.73%/综合分 2.67 排第一
- **历史看板验证**（6/12）：秒级响应，Top3=铜/铅锌/铜A股，104 只涨停，成分股为真实涨停股

---

## [Initial] - 首次提交

### 初始功能
- 5 个 iFinD 接口封装（含指数退避重试、批量分片）
- 板块强度三维评分（S1 涨幅 / S2 广度 / S4 相对强度）
- L1 权重归因法
- SQLite 7 张表数据模型
- FastAPI 6 个 REST 接口
- CLI：init / daily / server / test
