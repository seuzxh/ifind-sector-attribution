# 更新日志

本文件记录 ifind-sector-attribution 项目的版本改动，与 git commit 历史对应。

## [Unreleased] - 2026-06-15

### 新增：集合竞价（9:15~9:25）强弱监控
- **集合竞价阶段可用 ref_price 算涨跌幅**：实测 pre_market 在 9:15~9:25 有 201 个点（3秒采样），末点 ref_price = 开盘价，首点 ref_price = 昨收
  - 此阶段仅看涨跌幅，`speed/body/acceleration` 置 0（无连续价格序列）
  - snapshot_time 回看正常：拖滑块到 9:20 即看当时撮合价的板块强弱
- **`intraday_fetcher.py`**：`_fetch_one` 返回结构新增 `pre_market` 字段（之前只取首尾两个 ref_price，逐点序列被丢弃）
- **`realtime_engine.py`**：
  - `_build_indicator_df` 新增集合竞价分支：trading 严格按 snapshot_time 切片（不兜底回退），切片为空则用 pre_market 末点 ref_price 算涨跌幅
  - `_ensure_series` 时间轴纳入 pre_market 时间点（之前只从 trading 构建，集合竞价期间 available_times 为空）
- **进度条自动从 09:15 开始**：available_times 含 `['09:15',...,'09:25','09:30',...]` 共 252 点
- 验证：氟化工板块 9:20 集合竞价时三美/巨化/中欣氟材均 +10% 涨停，正确反映开盘前抢筹信号
- 新增 `probe_auction.py`：集合竞价数据探针脚本（生产环境验证 pre_market 形态用）

### 新增：交易日历模块（复用 kline-fetcher）
- **新增 `trade_calendar.py`**：`TradeCalendar` 单例，三级缓存（内存 → `data/trade_calendar.txt` → 网络 → DB 兜底）
  - 数据源：`kline_fetcher.fetch_trade_calendar`（拉上证指数日K反推交易日）
  - API：`is_trading_day` / `get_latest_trade_day` / `next_trade_day` / `session_phase` / `next_open_time`
  - `session_phase` 区分：`pre_open`(<9:15) / `auction`(9:15-9:25) / `pre_morning` / `morning` / `lunch` / `afternoon` / `closed`
  - 未来日期超出已加载范围时用工作日规则粗筛（节假日由实时接口无数据兜底）
- **`api_server.py` 新增端点**：
  - `GET /api/trade_calendar?year=2026` — 返回交易日列表（日期选择器用）
  - `GET /api/session_status` — 返回当前交易时段状态（前端盘前判断用）

### 改进：前端体验
- **表头排序在 3s 刷新后保持**：新增 `rankSort`/`cardSort` 全局状态，`applyRankSort`/`applyCardSort` 内核函数供点击与重渲染复用；修正表头默认视觉 bug（s1/score 原双标 sorted，现仅 score）
- **9:15 之前停止轮询 + 友好提示**：`checkSession` 查 `/api/session_status`，非交易日/盘前(`pre_open`)/收盘后停轮询并显示提示（如"⏰ 盘前 · 09:15 后自动开始监控"）；`scheduleResume` 每分钟检查，进入集合竞价/盘中自动恢复轮询
- **日期选择器联动交易日**：`loadTradeCalendar` 拉当年交易日，选非交易日给橙色提示
- **集合竞价阶段（9:15-9:25）自动启动轮询**：`session_phase=auction` 时 checkSession 启动轮询，配合后端集合竞价计算逻辑

### 重构（实时链路改用分时数据）
- **实时数据源切换**：盘中实时链路从 iFinD 接口4（1min K 线）改用 **kline-fetcher 的分时数据**（`TrendFetcher`，中焯行情 API）
  - 每只股票返回完整分时序列：集合竞价 `pre_market`（09:15~09:25，每3秒）+ 盘中 `trading`（09:30~15:00，每分钟 241 点）
  - 数据形态根本不同：分时只有 `last_price/avg_price/volume/turnover`，**无 OHLC、无 changeRatio**，需自行用 `last_price` 推导
  - 依赖：新增 `kline-fetcher` 包（pip install -e /root/Projects/kline-fetcher）；新增 `config.KLINE_API_BASE_URL`（走 config_local.py，敏感不入库）
- **新增 `intraday_fetcher.py`**：分时数据批量并发封装层
  - 32 线程拉取，每线程独立 `TrendFetcher` 实例绕过共享 throttle
  - 代码格式转换（`002430.SZ` → `SZ002430`）；watchlist 279 只仅 **1.5s** 拉取完成
- **`realtime_engine.py` 重写**：基于分时序列缓存 + 时刻切片
  - 分时序列按 (trade_date, mode_key) 缓存，TTL 5s，历史日期全天缓存
  - `snapshot_time` 切片：拖时间条回看任意时刻，纯内存毫秒级（不触发网络）
  - 指标基于切片末点重算：涨幅 / body / 涨速 / 加速
- **`stock_scorer.py` 算法改造**（适配分时数据）
  - `compute_speed`：从"近3分钟"改为 **1min 滚动序列**，`speed[t]=(last[t]-last[t-1])/last[t-1]`；新增 `compute_speed_series`
  - `compute_body_ratio`：语义从"最后一根K线实体"改为 **开盘至今涨幅** `(last-开盘价)/开盘价`，开盘价 = `pre_market[-1].ref_price`（09:25 集合竞价价）
  - 新增 `compute_acceleration`：涨速加速 `speed[t]-speed[t-1]`，>0 加速 / <0 减缓，**仅展示不进综合分**
- **新增可拖动时间条**（前端）：拖动回看任意时刻的板块排名，可观察板块轮动；"自动跟随最新"开关、"回到最新"按钮
- **轮询周期**：前端 `15s → 3s`；3s 轮询由后端 TTL 缓存挡住，大部分走纯内存（缓存命中 0.12s）
- **默认模式**：实时模式默认 **watchlist 聚焦**（279 只，1.5s），移除全市场模式开关

### 改动
- `api_server.py` `/api/realtime/dashboard`：移除 `start_time/end_time`，新增 `trade_date`/`snapshot_time`，默认 `watchlist_mode=True`；新增 `POST /api/realtime/clear_cache`
- 返回结构新增字段：`snapshot_time` / `latest_time` / `available_times`（时间轴）/ `is_today` / 成分股 `acceleration`
- `templates/index.html` 重做：时间滑块 + 3s 轮询 + 加速列（▲/▼）+ 默认实时模式

### 算法对照（分时数据 vs 旧 1min K）
| 指标 | 旧（1min K） | 新（分时数据） |
|---|---|---|
| 昨收 | 接口直给 changeRatio | `pre_market[0].ref_price` |
| 开盘价 | K线 open | `pre_market[-1].ref_price`（09:25 集合竞价价） |
| 涨幅 | changeRatio | `(last-昨收)/昨收` |
| body | `(close-open)/open`（单根K线） | `(last-开盘价)/开盘价`（开盘至今累计） |
| 涨速 | `(close[-1]-close[-3])/close[-3]`（近3分钟） | `(last[t]-last[t-1])/last[t-1]`（1min 滚动序列） |
| 加速 | 无 | `speed[t]-speed[t-1]`（新增） |

### 实测（2026-06-12 历史 + 4 板块样本验证）
- 性能：watchlist 279 只 32 并发 1.5s；缓存命中 0.12s；全市场 5530 只 ~30s
- 时间切片对比：9:50 半导体设备领涨（盛美上海 +13.34% / 加速 +1.2%），15:00 铜板块第一，体现板块轮动
- 4 板块验证：沪深主板/科创/创业/北交所分时结构一致（pre_market 201 点 + trading 241 点）

---

## [Unreleased] - 2026-06-14

### 新增
- **盘前筛选（prescreen）功能**：5 日累计涨幅选板块+成分股，存入 watchlist，实时监控可聚焦
  - `prescreen.py`：筛选核心（板块 5d 涨幅均值 → 前 20；每板块成分股 5d 涨幅 → 各前 30）
  - 新股过滤：剔除上市不足 5 个交易日的新股（从 K 线历史反推），避免连板新股撑高板块涨幅
  - `watchlist` 表：持久化筛选结果（PK: 日期+板块+成分股）
  - `realtime_engine.py` watchlist 模式：实时拉取范围从全市场 5530 只缩减到 ~290 只，响应 2 分钟 → 30 秒
  - 三入口：`main.py prescreen` 命令、页面"盘前筛选"按钮、`POST /api/prescreen`
  - 前端新增"盘前筛选"模式（展示 watchlist）+ 实时模式 watchlist 聚焦开关
- **systemd 部署支持**：开机自启 + 崩溃自动重启 + 外网访问
  - `ifind-monitor.service`：绑 0.0.0.0:8000，Restart=always，KillSignal=SIGINT
  - `install_service.sh`：一键安装脚本（检查环境→安装→启动→验证）
  - `docs/DEPLOYMENT.md`：部署运维手册（外网访问、安全组、crontab、故障排查、升级回滚）
- **盘中实时监控可视化网站**：FastAPI + 单页 HTML + plotly.js，支持实时/历史/watchlist 三模式
  - `stock_scorer.py`：成分股四维综合评分（涨幅 0.4 / 涨速 0.2 / 实体涨幅 0.2 / 涨停 0.2），涨停判定按板块（主板 9.8% / 创业科创 19.5% / 北交 29%）
  - `realtime_engine.py`：实时引擎，接口4 拉全市场 1min K → 构造 realtime_df → 算板块强度 + 成分股排名，带 10 秒内存缓存
  - `templates/index.html`：单页前端，日期选择 + Top10/Bottom10 双柱状图 + 成分股卡片 + 15 秒轮询，plotly.js 走 CDN
- **新 API 路由**：`/api/realtime/dashboard`（实时看板，支持 watchlist_mode）、`/api/history/dashboard`（历史看板）、`/api/prescreen`（盘前筛选）、`/api/watchlist`（读 watchlist）、`/api/dates`（可用日期列表）
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
- `calc_multi_period_score` 的 5d/20d 闭包 `_period_return_df` 提取为模块级函数 `calc_period_return_df`，供 prescreen 复用

### 实测数据（2026-06-08 ~ 06-12 全市场 A 股）
- 每日：5527 条 K 线 → 1068 板块多周期评分 → 5500 股票归因
- 板块强度 Top 排名准确反映市场主线（机器人/电源设备/有色金属）
- 归因逻辑自洽（平安银行=蓝筹概念、同花顺=科技金融、中信证券=券商概念）
- 多周期融合捕捉中期趋势（铜板块 6/12 登顶，前几日单日排名靠后）
- **实时监控验证**（6/12 09:30~09:35 模拟）：5512 股拉取成功，Top3=航天装备/磁性材料/印制电路板，成分股 301323 涨 4.71%/涨速 8.73%/综合分 2.67 排第一
- **历史看板验证**（6/12）：秒级响应，Top3=铜/铅锌/铜A股，104 只涨停，成分股为真实涨停股
- **盘前筛选验证**（6/12）：1068 有效板块中选出 20 个 + 去重 290 只成分股，Top5=工业气体/非金属材料/机器人/半导体材料；watchlist 模式实时拉取量从 5530→290 只，响应 2 分钟→30 秒

---

## [Initial] - 首次提交

### 初始功能
- 5 个 iFinD 接口封装（含指数退避重试、批量分片）
- 板块强度三维评分（S1 涨幅 / S2 广度 / S4 相对强度）
- L1 权重归因法
- SQLite 7 张表数据模型
- FastAPI 6 个 REST 接口
- CLI：init / daily / server / test
