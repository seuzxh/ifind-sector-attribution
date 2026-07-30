# 前端架构

> 本文档描述 `ifind-sector-attribution` 项目的前端架构、技术栈与编程规范，供新成员和 AI 智能体快速上手。所有结论均基于实际代码，行号/文件名以仓库现状为准。

## 目录
- [1. 总体架构](#1-总体架构)
- [2. 技术栈](#2-技术栈)
- [3. 目录结构](#3-目录结构)
- [4. 构建与部署](#4-构建与部署)
- [5. 路由与页面](#5-路由与页面)
- [6. 数据流：API 层](#6-数据流程api-层)
- [7. 数据流：状态与轮询](#7-数据流状态与轮询)
- [8. 样式体系](#8-样式体系)
- [9. 编程规范](#9-编程规范)
- [10. 架构统一（无旧版）](#10-架构统一无旧版)

---

## 1. 总体架构

前端是一个 **Vue 3 单页应用（SPA）**，源码在 `frontend/`，用 Vite 构建，产物输出到上级 `static/`，由 FastAPI 直接以静态文件方式 serve。

```
┌──────────────────────────────────────────────────────────┐
│  浏览器  http://server:8000/                             │
│   └─ 加载 static/index.html（Vue SPA，Hash 路由）        │
│       └─ 通过 /api/* 调后端                              │
└──────────────────────────────────────────────────────────┘
            │ /static/*（静态资源）     │ /api/*（数据接口）
            ▼                           ▼
┌──────────────────────┐    ┌───────────────────────────┐
│ FastAPI              │    │ FastAPI                   │
│ app.mount('/static') │    │ @app.get/post('/api/...') │
│ ← static/ 目录        │    │ ← api_server.py           │
└──────────────────────┘    └───────────────────────────┘
```

**关键设计**：
- 前后端**同源**，`/api` 走相对路径，无跨域问题。
- 开发时 Vite dev server (5173) 用 proxy 把 `/api` 转发到本地 FastAPI (8000)，模拟同源。
- Hash 路由（`createWebHashHistory`），无需后端做 history fallback。

---

## 2. 技术栈

| 类别 | 选型 | 版本 | 说明 |
|---|---|---|---|
| 框架 | Vue 3 | ^3.5.13 | `<script setup>` 组合式 API |
| 构建 | Vite | ^6.0.5 | `frontend/vite.config.ts` |
| 语言 | TypeScript | ~5.7.2 | **strict 模式**全开 |
| UI 库 | Element Plus | ^2.9.1 | 中文 locale (`zhCn`) |
| 图标 | @element-plus/icons-vue | ^2.3.1 | 全量全局注册 |
| 路由 | Vue Router | ^4.5.0 | Hash 模式 |
| 状态 | Pinia | ^2.3.0 | 已安装并注册，当前尚无 store |
| HTTP | Axios | ^1.7.9 | 统一实例 + 拦截器 |
| 类型检查 | vue-tsc | ^2.1.10 | `npm run build` 前置 |

**注意**：不使用 Vuex；不使用图表库（K 线/排名均用原生 HTML table + CSS，无 ECharts/Plotly 依赖——这与旧版不同）。

**npm 脚本**（`frontend/package.json`）：
- `npm run dev` — 开发服务器（5173）
- `npm run build` — `vue-tsc --noEmit && vite build`，产物输出到 `../static/`
- `npm run type-check` — 仅类型检查

---

## 3. 目录结构

```
frontend/
├── package.json
├── vite.config.ts          # 构建/代理/base 路径配置
├── tsconfig.json           # strict TS 配置，@/* → ./src/*
├── env.d.ts                # Vue SFC 类型声明
├── index.html              # Vite 入口 HTML
└── src/
    ├── main.ts             # 应用入口：挂载 Pinia/Router/ElementPlus
    ├── App.vue             # 根组件（仅 <router-view />）
    ├── router/
    │   └── index.ts        # 路由表（7 个 Tab）
    ├── layouts/
    │   └── AppLayout.vue   # 顶部 Tab 导航 + <router-view>（keep-alive）
    ├── views/              # 页面级组件（每个 Tab 一个）
    │   ├── DashboardPage.vue   # 板块强度 / 自选分组（同组件复用）
    │   ├── AuctionPage.vue     # 集合竞价
    │   ├── ScanPage.vue        # 强势归类（自选/全市场同组件复用）
    │   ├── RotationPage.vue    # 板块轮动（SSE）
    │   └── SectorManagePage.vue # 监控板块管理（勾选+多周期涨幅）
    ├── components/
    │   └── dashboard/      # 看板子组件
    │       ├── TimeBar.vue         # 时间轴播放控件
    │       ├── RankTable.vue       # 板块排行表
    │       └── MemberCardGrid.vue  # 成分股卡片网格
    ├── composables/        # DashboardPage 的可复用交互逻辑
    │   ├── usePolling.ts          # 轮询 + 竞态守卫
    │   ├── usePlayTimeline.ts     # 时间轴自动播放
    │   └── useSession.ts          # 交易时段感知（盘前停轮询）
    ├── api/                # 接口封装层
    │   ├── client.ts       # axios 实例 + 拦截器（★所有请求出口）
    │   ├── types.ts        # 公共类型（DashboardPayload 等）
    │   ├── dashboard.ts    # 看板数据接口
    │   ├── auction.ts      # 集合竞价接口
    │   ├── scan.ts         # 强势归类接口
    │   ├── custom.ts       # 自选分组 reload
    │   ├── calendar.ts     # 交易日历
    │   └── session.ts      # 交易时段状态
    ├── utils/
    │   ├── format.ts       # fmt/fmtPct/changeCls 等格式化
    │   └── markdown.ts     # Markdown 渲染（Rotation 用）
    └── styles/
        └── global.css      # 全局样式 + CSS 变量
```

---

## 4. 构建与部署

### 构建产物流向
```
frontend/src  ──(vite build)──►  static/   ←─ FastAPI mount("/static")
                                  ├── index.html
                                  └── assets/*.js, *.css
```

### `vite.config.ts` 关键配置
| 配置 | 值 | 原因 |
|---|---|---|
| `base` | dev `'/'` / prod `'/static/'` | prod 下 index.html 内 asset 引用为 `/static/assets/...`，能被 FastAPI serve |
| `build.outDir` | `'../static'` | 产物直接落到项目根 `static/` |
| `build.emptyOutDir` | `true` | 构建前清空 `static/` |
| `resolve.alias['@']` | `./src` | `@/...` 路径别名 |
| `server.proxy['/api']` | → `localhost:8000` | 开发时同源，免跨域 |

### 开发流程
```bash
cd frontend
npm install
npm run dev        # 前端 5173，/api 代理到后端 8000
# 另一个终端：
python main.py server --port 8000
```

### 生产部署
```bash
cd frontend && npm run build   # 产物进 static/
python main.py server          # FastAPI 同时 serve static/ 和 /api
```

> `static/` 是构建产物，**不应手改**，也无需纳入版本控制的核心内容（重新 build 即可重生）。

---

## 5. 路由与页面

路由定义在 `src/router/index.ts`，**Hash 模式**（`#/sector` 等）。所有页面挂在 `AppLayout` 下（顶部 Tab 栏 + 内容区）。

| 路径 | name | 组件 | Tab 标题 |
|---|---|---|---|
| `/` | — | redirect → `/sector` | — |
| `/sector` | sector | DashboardPage | 📊 板块强度监控 |
| `/custom` | custom | DashboardPage | ⭐ 自选分组监控 |
| `/auction` | auction | AuctionPage | ⚡ 集合竞价 |
| `/scan` | scan | ScanPage | 🎯 自选强势归类 |
| `/market_scan` | market_scan | ScanPage | 🌐 全市场强势归类 |
| `/rotation` | rotation | RotationPage | 🔮 板块轮动分析 |
| `/sector_manage` | sector_manage | SectorManagePage | 🛠️ 监控板块管理 |

**组件复用约定**：
- `DashboardPage` 同时服务 `sector` 和 `custom` —— 用 `route.name === 'custom'` 区分数据源。
- `ScanPage` 同时服务 `scan` 和 `market_scan` —— 同理按路由名切换。
- `AppLayout` 用 `<keep-alive>` 包裹 `<router-view>`，切 Tab 保留各页状态。

`RotationPage` 解析 `/api/rotation/analyze` 的 SSE：行情采集的
`[PROGRESS]collect|done|total|pct` 标记只覆盖更新进度条，不写入结果卡片；
分批分析结果逐批形成卡片，阶段分隔符必须是独立一行的 `---`。

---

## 6. 数据流：API 层

### 统一出口 `src/api/client.ts`
```ts
const http = axios.create({ timeout: 60000 })
// 响应拦截器：成功直接返回 resp.data（脱壳），失败抛 Error(detail)
```
普通 JSON 接口统一经 `http` 实例，享受统一超时与错误处理。SSE/流式接口（当前为 Rotation）例外，使用原生 `fetch` + `ReadableStream`。成功返回的是业务数据（已被拦截器脱壳），失败抛 `Error(message)`。

### 按域分文件
| 文件 | 后端路由前缀 | 用途 |
|---|---|---|
| `dashboard.ts` | `/api/realtime`, `/api/custom`, `/api/auction`, `/api/history` | 看板数据 |
| `auction.ts` | `/api/auction/*` | 集合竞价 |
| `scan.ts` | `/api/custom/scan`, `/api/market/scan` | 强势归类 |
| `custom.ts` | `/api/custom/check_reload` | 自选分组热更新 |
| `calendar.ts` | `/api/trade_calendar`, `/api/dates` | 交易日历 |
| `session.ts` | `/api/session_status` | 交易时段 |

### 类型契约 `src/api/types.ts`
- 后端返回 JSON 统一为 `{ ...data }`（成功）或 `{ "error": "..." }` / `{ "detail": "..." }`（失败）。
- `DashboardPayload` 是看板返回的通用骨架，`SectorEntry` / `MemberStock` / `MarketStats` 为其子结构。
- **新增接口时**：先在 `types.ts` 定义返回类型，再在对应域文件写封装函数，标注返回类型。

### 接口封装范式（必须遵守）
```ts
import http from './client'
import type { DashboardPayload } from './types'

export function getRealtimeDashboard(params: DashboardParams = {}): Promise<DashboardPayload> {
  return http.get('/api/realtime/dashboard', { params })
}
```
- 每个函数一个接口，命名 `getXxx` / `postXxx`。
- 参数用对象解构默认 `{}`，永远不传位置参数。
- 显式标注 `Promise<返回类型>`。

---

## 7. 数据流：状态与轮询

### 轮询与竞态（核心机制）
实时看板每 3 秒轮询一次，但请求可能乱序返回。`DashboardPage` 通过 `usePolling` 的共享序号守卫，只允许最新响应渲染：

```ts
const { triggerNow, currentSeq } = usePolling(loadDashboard, 3000)
async function loadDashboard(mySeq: number) {
  const data = await fetchDashboard()
  if (mySeq !== currentSeq()) return
}
```
暂停自动跟随时，定时 tick 不占用新序号，避免把时间轴手动请求误判为过期。`AuctionPage` 和 `SectorManagePage` 仍各自管理其专用轮询。

状态栏同时展示行情分钟和最近响应秒数，例如 `行情 14:12 · 刷新 14:12:08 · 自动3s`。行情源是分钟级，行情分钟在同一分钟内不变并不代表轮询停止；“刷新”时间用于直接确认页面定时器仍在工作。

### 成分股全量字段排序

看板主响应只携带每个板块的 `members_top10`，避免 3 秒轮询反复传输所有成员。用户点击涨幅、涨速、加速、开盘至今或综合分列时：

1. `MemberCardGrid` 调用 `GET /api/dashboard/members`；
2. 后端复用当前分时序列，对该板块/分组全部有效成员计算并按所选字段排序；
3. 接口只返回排序后的前 10 支；
4. 新行情分钟到达后，组件自动刷新用户已选择过的全量排序。

历史收盘模式没有完整分时指标，仍对主响应中的 10 支做本地展示排序。

### 三个 composable
| composable | 作用 | 当前状态 |
|---|---|---|
| `usePolling` | 定时轮询、手动刷新、共享竞态序号、自动清理 | `DashboardPage` 已接入 |
| `usePlayTimeline` | 时间轴自动快进、调速、跳转和停止 | `DashboardPage` 已接入 |
| `useSession` | 交易时段感知；盘前 10 秒探测、正常时段 60 秒复核 | `DashboardPage` 已接入 |

### 状态管理
- Pinia 已安装并注册，但当前没有 `defineStore`；页面状态均为局部状态。
- 但目前多数状态是**页面级局部状态**（`ref` / `reactive` 在 `<script setup>` 内），不强行提升到 store。仅在确有跨页共享需求时才建 store。

---

## 8. 样式体系

### CSS 变量（`src/styles/global.css`）
全局配色集中定义为 CSS 变量，**所有组件应引用变量而非硬编码色值**：

```css
:root {
  --color-primary: #1e40af;       /* 主色（蓝） */
  --color-up: #ef4444;            /* 涨（红）—— A 股惯例 */
  --color-down: #10b981;          /* 跌（绿）—— A 股惯例 */
  --color-bg: #f0f2f5;
  --color-card: #ffffff;
  --color-border: #e5e7eb;
  --color-text: #1f2937;
  --color-text-light: #6b7280;
  --color-holding: #f59e0b;       /* 持仓（金） */
}
```

**A 股涨跌色约定（重要，与欧美相反）**：**涨红跌绿**。全局提供 `.up` / `.down` 工具类，颜色判断统一走 `format.ts` 的 `changeCls()`，不要散落写 `:class="n>0 ? 'red' : 'green'"`。

### 样式作用域
- 组件内样式用 `<style scoped>`，避免污染。
- 全局/工具类放 `global.css`。
- Element Plus 主色通过覆盖 `--el-color-primary` 系列变量对齐项目蓝。

### 格式化函数（`src/utils/format.ts`）
| 函数 | 作用 |
|---|---|
| `fmt(n)` | null/NaN → `'-'`；正数加 `+`；保留 2 位小数 |
| `fmtPct(n)` | `fmt(n) + '%'` |
| `changeCls(n)` | 返回 `'up'` / `'down'` / `''` |
| `accelText(a)` | 加速度 ▲▼ 指示 |
| `scoreColor(score, tab)` | 评分背景色（top 红 / bottom 绿） |
| `rankClass(i)` | 排名徽章 class（前 3 名特殊色） |

**所有数值显示必须经这些函数**，确保空值、正负号、小数位一致。

---

## 9. 编程规范

### 命名
| 类型 | 规范 | 示例 |
|---|---|---|
| 文件 | 组件 PascalCase.vue；其余 kebab-case 或 camelCase | `DashboardPage.vue`, `format.ts` |
| 组件 | PascalCase | `RankTable` |
| 函数/变量 | camelCase | `getRealtimeDashboard`, `availableTimes` |
| ref 状态 | camelCase，布尔用 `is/has/can` 前缀 | `isCustom`, `hasHolding`, `canCalc` |
| 类型/接口 | PascalCase | `DashboardPayload` |
| API 路径 | 后端 snake_case，前端保持一致 | `change_ratio`, `concept_code` |

### Vue 组件写法
- 一律 `<script setup lang="ts">`，不写 Options API。
- 顺序：`import` → 响应式状态 → computed → 函数 → 生命周期。
- props 用 `defineProps<XxxProps>()` 泛型定义；emit 用 `defineEmits<{...}>()`。
- 模板里逻辑简单，复杂判断提到 `computed` 或 `utils`。

### TypeScript
- **strict 全开**（`tsconfig.json`），且 `noUnusedLocals` / `noUnusedParameters` 开启——**禁止声明未用的变量/参数**。
- 后端字段可能 null/缺失，类型用 `number | null`，访问前判断。
- 禁用 `any`（`DashboardPayload` 末尾的索引签名是后端灵活字段的妥协，新字段尽量具名化）。

### 错误处理
- API 调用用 `try/catch`，`catch (e: any)` 取 `e.message`。
- 用户可见错误用 **`ElMessage`**（warning/error），不要用原生 `alert`。
  - 例外：`ScanPage` 用原生 `prompt`（代码注释说明：ElMessageBox.prompt 在某些环境有渲染/聚焦问题，故用原生更可靠）。
- 接口返回 `{ error }` 业务错误也要检查（不只是 HTTP 异常）：
  ```ts
  const d = await getXxx()
  if (d.error) { ElMessage.warning(d.error); return }
  ```

### 性能约定
- 定时器（`setInterval`）一律在 `onUnmounted` 清理（composable 已封装）。
- 大列表避免无 key 的 `v-for`，用业务主键（`concept_code` / `code`）作 key。
- 小型完整列表优先在前端排序；`members_top10` 不是完整列表，实时成分股列排序必须调用 `/api/dashboard/members` 覆盖全部有效成员，同时避免把全量成员放进 3 秒主响应。

---

## 10. 架构统一（无旧版）

项目已完成从「旧版原生 JS 单文件」到 **Vue 3 SPA** 的迁移，旧版已删除，前后端架构统一：

- **前端唯一入口**：`frontend/` 源码 → `npm run build` → `static/` 产物。
- **后端唯一入口路由** `api_server.py:root()`：`GET /` 返回 `static/index.html`（Vue SPA）并设置 `Cache-Control: no-cache, no-store, must-revalidate`，避免新构建后旧入口继续引用已不存在的 hash asset；SPA 未构建时返回构建提示（不再回退旧版）。
- 已删除：`templates/`（旧版 `index.html` / `tabs.html` / `chat.html`）、`root()` 的 `legacy` / `board` 参数。
- `DashboardPage` 的板块实时模式使用“监控板块管理”有效范围（勾选且最新成员数 10~500）及其全部去重成分股；不再提供盘前筛选或 watchlist 模式。

---

## 附：上手清单
1. `cd frontend && npm install`
2. 后端起 `python main.py server`（8000）
3. 前端 `npm run dev`（5173），浏览器开 `http://localhost:5173`
4. 改代码热更新；改完 `npm run build` 让产物进 `static/` 供生产用
5. 加接口：后端 `api_server.py` 加路由 → 前端 `api/types.ts` 加类型 → `api/<域>.ts` 加封装 → view 里调用
6. 加页面：`views/XxxPage.vue` + `router/index.ts` 加路由 + `AppLayout.vue` 加 Tab
