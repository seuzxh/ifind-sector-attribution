# 前后端架构梳理（第一性原理 + 对抗式审查）

> 生成于 2026-07-02，2026-07-24 按真实代码复核。
> 目的：为后续开发/扩展提供清晰的边界与约束。

---

## 一、整体架构

单体 FastAPI 应用 + Vue 3 SPA，单进程、单端口（8000）、单机部署。

```
┌──────────────────────────────────────────────────────────────┐
│  systemd ifind-monitor  (单进程, 0.0.0.0:8000)                 │
│  main.py server → uvicorn → FastAPI app (api_server.py)       │
│                                                               │
│  前端：Vue3 SPA (static/index.html, Hash 路由)                │
│  后端：api_server.py (24 个 API/SSE 接口)                     │
│         │                                                      │
│    ┌────┴──────────────────────────────────────────────┐      │
│    │  业务引擎层（各自带缓存/锁）                         │      │
│    │  realtime_engine / auction_engine / rotation_agent │      │
│    ├─────────────────────────────────────────────────────┤      │
│    │  计算层（纯函数）  core_calculator / stock_scorer   │      │
│    ├─────────────────────────────────────────────────────┤      │
│    │  数据层  database(SQLite,9表) / intraday_fetcher    │      │
│    │           ifind_client / mcp_proxy                  │      │
│    ├─────────────────────────────────────────────────────┤      │
│    │  AI 层  llm_agent / mcp_proxy                       │      │
│    ├─────────────────────────────────────────────────────┤      │
│    │  基础  config(leaf) / trade_calendar(三级缓存)       │      │
│    └─────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────┘
```

### 部署
- 后端：`python main.py server`，systemd 守护，`Restart=always`
- 前端：`cd frontend && npm run build` → 输出到 `static/`，FastAPI `/` 直接 serve
- `/` 只返回 Vue SPA；旧版 `templates/` 与 `?legacy=1` 回退已删除

---

## 二、后端架构

### 模块依赖图（clean DAG，无循环导入）
```
config (leaf)
├─ database ← config
├─ core_calculator ← config              (纯计算，无 I/O)
├─ stock_scorer ← (stdlib)               (纯计算，最干净的叶子)
├─ mcp_proxy ← config
├─ ifind_client ← config
├─ intraday_fetcher ← config, kline_fetcher(外部)
├─ llm_agent ← config, mcp_proxy
├─ trade_calendar ← config (+ lazy database/kline_fetcher)
├─ sync_pipeline ← ifind_client, database, core_calculator
├─ realtime_engine ← database, intraday_fetcher, core_calculator, stock_scorer
├─ auction_engine ← database, intraday_fetcher, stock_scorer
├─ rotation_agent ← llm_agent (+ lazy database/kline_fetcher)
└─ api_server ← database, core_calculator (+ 函数级 lazy import 其余全部)
```

**关键设计**：`api_server` 用**函数级 lazy import**（每个 handler 内 `from X import Y`），避免启动时加载重模块，也规避潜在循环。这是正确的模式，**不要改成顶层导入**。

### 分层职责（严格边界）
| 层 | 模块 | 职责 | 禁止 |
|----|------|------|------|
| **服务层** | api_server.py | HTTP 路由、参数校验、响应组装 | ❌ 不内联业务计算（见审查项3） |
| **引擎层** | realtime/auction/rotation_engine | 编排数据获取+计算+缓存 | ❌ 不直接碰 HTTP |
| **计算层** | core_calculator, stock_scorer | 纯函数，无副作用 | ❌ 不做 I/O（config 只读） |
| **数据层** | database, ifind_client, intraday_fetcher, mcp_proxy | 读写外部存储/API | — |
| **基础** | config(只读常量), trade_calendar | 全局配置、日历 | config 不导入业务模块 |

### 后端接口清单（24 个）
| 类别 | 方法 | 路径 | 用途 |
|------|------|------|------|
| 页面 | GET | `/` | SPA 入口；入口 no-cache，hash assets 可长缓存 |
| 看板 | GET | `/api/realtime/dashboard` | 板块强度实时 |
| 看板 | GET | `/api/custom/dashboard` | 自选分组实时 |
| 看板 | GET | `/api/auction/dashboard` | 集合竞价 |
| 看板 | GET | `/api/history/dashboard` | 历史收盘 |
| 看板 | GET | `/api/custom/scan` | 自选强势归类(MCP选股) |
| 看板 | GET | `/api/market/scan` | 全市场强势归类 |
| 看板 | GET | `/api/rotation/analyze` | 板块轮动分析(SSE) |
| 数据 | GET | `/api/sector/rankings` | 板块排名 |
| 数据 | GET | `/api/concept/list` / `/members` | 概念板块 |
| 数据 | POST | `/api/attribution/stock` / `/portfolio` | 归因 |
| 数据 | GET | `/api/dates` | 已入库日期 |
| 数据 | GET | `/api/realtime/sector` | 最新板块 |
| 操作 | POST | `/api/realtime/clear_cache` | 清分时缓存 |
| 操作 | POST | `/api/auction/clear_cache` | 清竞价缓存 |
| 操作 | POST | `/api/custom/check_reload` | 自选分组重导 |
| 管理 | GET/POST | `/api/sector_manage/*` | 监控范围、后台刷新及状态；保存后清看板缓存 |
| 日历 | GET | `/api/trade_calendar` | 交易日(+today) |
| 日历 | GET | `/api/session_status` | 交易时段 |

---

## 三、前端架构

### 技术栈
Vue 3 (`<script setup>`) + Vite + TypeScript + Element Plus + Vue Router
（Pinia 已注册但当前没有 store，见审查项）

### 工程结构
```
frontend/src/
├── api/           # 类型化接口封装（按领域拆）
│   ├── client.ts      # axios 实例 + 拦截器
│   ├── dashboard.ts   # realtime/custom/history
│   ├── auction.ts / scan.ts / sectorManage.ts / session.ts / calendar.ts / custom.ts
│   └── types.ts       # 共享类型
├── components/dashboard/   # 复用组件
│   ├── RankTable.vue      # 排序排行表（5维排序）
│   ├── MemberCardGrid.vue # 成分股卡片网格
│   └── TimeBar.vue        # 时间轴
├── composables/    # DashboardPage 的轮询、会话和时间轴逻辑
│   ├── usePolling.ts / useSession.ts / usePlayTimeline.ts
├── layouts/AppLayout.vue  # 顶部 tab + router-view + keep-alive
├── views/          # 7 个页面
│   ├── DashboardPage.vue  # sector+custom 共用（⚠ 约400行，仍偏大）
│   ├── AuctionPage.vue / ScanPage.vue / RotationPage.vue / SectorManagePage.vue
├── utils/          # format.ts / markdown.ts（轮动输出）
├── router/index.ts # Hash 路由，7 个路由
├── main.ts         # ElementPlus + Router + Pinia 注册
└── styles/global.css
```

### 路由
| path | name | 组件 | 说明 |
|------|------|------|------|
| `/sector` | sector | DashboardPage | 板块强度 |
| `/custom` | custom | DashboardPage | 自选分组（同一组件，按 route.name 区分） |
| `/auction` | auction | AuctionPage | 集合竞价 |
| `/scan` | scan | ScanPage | 自选强势归类 |
| `/market_scan` | market_scan | ScanPage | 全市场（同组件，按 route.name 区分） |
| `/rotation` | rotation | RotationPage | 板块轮动 |
| `/sector_manage` | sector_manage | SectorManagePage | 监控板块管理 |

### 关键交互模式
- **3s 轮询 + 竞态守卫**：`usePolling` 统一序号，仅最新响应允许渲染
- **会话感知**：盘前/非交易日停轮询，10s 探测到点恢复
- **刷新可观察**：状态栏同时展示行情分钟与最近响应秒数；分钟不变不等于 3s 轮询停止
- **全量成员按需排序**：主看板只返 `members_top10`，点击字段后由 `/api/dashboard/members` 对全部有效成员排序再返前 10
- **红涨绿跌**：A 股惯例，全局 `.up{#ef4444}/.down{#10b981}`
- **SSE 流式**：rotation 用 fetch ReadableStream 解析
- **keep-alive**：tab 切换保留状态；DashboardPage 和 ScanPage 监听共享组件的路由切换并按新范围重拉

---

## 四、对抗式审查：发现的问题

### 🔴 P0（建议尽快修）

**1. `/api/history/dashboard` 是大型单体 handler，内联业务逻辑**
- handler 约 220 行，包含内联成员排名、涨停判定、scope 聚合和 market_stats
- 直接 `sqlite3.connect` 绕过 Database 类（2 处）
- 重复了 realtime_engine 的成员排名 + stock_scorer 的涨停规则
- `force_calc` 分支在请求里同步跑 ~2 分钟 SyncPipeline，阻塞 worker
- **修复**：抽到 core_calculator/stock_scorer；force_calc 改后台任务

### 🟡 P1（技术债）

**4. 涨停规则三处重复**
- stock_scorer.is_limit_up、realtime_engine._market_stats._is_limit、api_server.get_history_dashboard._is_limit
- 同一规则三份拷贝，易漂移
- **修复**：统一到 stock_scorer，其余引用

**5. 未加锁的全局可变状态**
- `auction_engine.py:364-367`：`_last_result/_last_result_time/_engine_instance` 无锁（realtime_engine 有锁，auction 没有）
- `api_server.py:309`：`_custom_groups_mtime` 无锁，首次并发可能双重导入
- **修复**：仿 realtime_engine 加 threading.Lock

**6. Pinia 装了没用**
- main.ts 注册了 Pinia，但全项目无 `defineStore`
- **修复**：要么用（跨组件状态如排序状态/选中模型），要么移除依赖

**7. Element Plus 全量引入（1.1MB / gzip 383KB）**
- main.ts `app.use(ElementPlus)` 全量注册
- **修复**：按需引入（unplugin-vue-components）或 manualChunks 拆 vendor

### 🟢 P2（优化项）

**8. api_server.py 746 行偏大**
- 24 个接口全在一个文件
- **修复**：按领域拆 router（dashboard/auction/rotation/sector-manage）

**9. config.ACCESS_TOKEN 运行时被 ifind_client 改写**
- 全局可变 config，虽有 _refresh_lock 保护，但是隐式耦合
- **修复**：token 管理封装到独立类（长期）

**10. 前端 DashboardPage 约 400 行**
- 定时器已抽入现役 composables，但页面仍承担数据装载、排序、持仓、ZT 和历史模式组装
- **修复**：继续抽出 useDashboard 数据层并拆分控制栏/状态栏子组件

---

## 五、后续开发扩展约束框架

### 后端约束
| 编号 | 约束 | 理由 |
|------|------|------|
| BE-1 | **新增接口必须走 Database 类**，禁止 handler 内 `sqlite3.connect` | 统一连接管理/事务/WAL |
| BE-2 | **业务计算放计算层**（core_calculator/stock_scorer），handler 只做路由+组装 | 单体 handler 已是技术债 |
| BE-3 | **共享状态必须加锁**，仿 realtime_engine 的 per-key lock 模式 | FastAPI sync handler 跑在 40 线程池 |
| BE-4 | **耗时操作（>5s）不能在请求里同步执行**，用后台线程/任务 | 避免阻塞 worker |
| BE-5 | **保持函数级 lazy import 模式**，不要改 api_server 顶层导入 | 规避循环、加速启动 |
| BE-6 | **单一数据源**：涨停规则/板块过滤/A股判定等只用 stock_scorer/config 的一份 | DRY，防漂移 |
| BE-7 | **config 只读**，token 刷新是唯一例外（且须加锁） | 全局可变 config 是隐患 |

### 前端约束
| 编号 | 约束 | 理由 |
|------|------|------|
| FE-1 | 新增轮询必须保留请求序号守卫和卸载清理；DashboardPage 优先复用现役 composables | 防止复制页面内逻辑 |
| FE-2 | JSON 接口必须经 src/api/ 封装；SSE 流式接口允许 view 使用原生 fetch + ReadableStream | 类型安全与流式读取兼容 |
| FE-3 | **组件超 300 行考虑拆分**（DashboardPage 已超标） | 可维护性 |
| FE-4 | **红涨绿跌用全局 .up/.down class**，不要硬编码颜色 | A股惯例一致性 |
| FE-5 | **跨组件状态用 Pinia**（如选中模型/排序状态持久化） | 已装未用 |
| FE-6 | **路由用 Hash 模式**（createWebHashHistory） | 无需后端 catch-all |
| FE-7 | **build 产物路径 base=/static/**，dev 用 / | 已配，勿动 |

### 跨端约束
| 编号 | 约束 |
|------|------|
| X-1 | API 返回 error 用 `{error: "msg"}`（HTTP 200），前端在 api 层统一判断 |
| X-2 | 时间格式：日期 YYYYMMDD，时刻 HH:MM，前端不假设时区（用服务端 today） |
| X-3 | 涨跌色由前端渲染，后端只返回数值 |

---

## 六、优化优先级建议

| 优先级 | 项 | 工作量 | 收益 |
|--------|-----|--------|------|
| P0 | history dashboard 抽离业务逻辑 | 中 | 消除重复+单体handler |
| P1 | 涨停规则统一到 stock_scorer | 小 | DRY |
| P1 | auction_engine 加锁 | 小 | 并发安全 |
| P1 | Element Plus 按需引入 | 小 | 包体积-60% |
| P2 | api_server 拆 router | 中 | 可维护性 |
| P2 | DashboardPage 抽数据层并拆子组件 | 中 | 可维护性 |

### 已解决项

- **Dashboard composables**：`usePolling`、`useSession`、`usePlayTimeline` 已接入 DashboardPage，页面不再自行持有对应定时器。
- **SQLite WAL / busy timeout**：`Database._init_db()` 已启用 WAL，连接统一设置 `busy_timeout` 和 `synchronous=NORMAL`。
