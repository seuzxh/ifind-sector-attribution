---
title: "计划：开盘题材动态强弱看板"
parent: "架构与设计"
nav_order: 22
---

# 开盘题材动态强弱看板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 monitor 中交付可生产使用的“开盘题材”Tab，以冻结盘前归因和 09:30 后分时行情提供实时排名与历史回放。

**Architecture:** 新增独立的 `opening_strength` 实时聚合路径：纯函数聚合器只处理规范化 DTO，行情提供器复用 `IntradayFetcher` 并负责缓存与时点切片，服务层只读取冻结快照并编排两者。FastAPI 提供一个只读接口，Vue 页面使用布局 A 和 3 秒轮询；旧 `RealtimeEngine` 与现有看板接口不改。

**Tech Stack:** Python 3.11、FastAPI、SQLite、stdlib dataclasses/threading/unittest；Vue 3、TypeScript、Vite、Element Plus；新增开发依赖 Vitest、Vue Test Utils、jsdom。

**Spec:** `docs/superpowers/specs/2026-10-03-opening-theme-dashboard-design.md`

## Global Constraints

- Python 固定使用 `/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python`；下文 `$PY` 指向该绝对路径，命令在仓库根执行并设置 `PYTHONPATH=.`。
- 页面和 API 只读取 `FROZEN` 运行；不得执行归因、写数据库或替换快照。
- Theme 只接受行业 `884xxx` 与概念 `885xxx/886xxx`；不处理自定义静态板块。
- 只使用 09:30 及之后的 `trading` 分时点；集合竞价 `pre_market` 不进入本阶段计算。
- 主榜按归因权重加权涨幅排序；首版不创建不透明综合分，成交额和换手率不参与排名。
- 实时轮询固定 3 秒；行情序列 TTL 15 秒，聚合结果 TTL 3 秒；不新增数据库表或 WebSocket。
- `opening_strength` 不得导入 `realtime_engine`、`theme_catalyst`、`api` 或 `api_server`；聚合器还不得导入 `features` 与 `attribution`。
- 前端依赖只能新增 `vitest@3.2.4`、`@vue/test-utils@2.4.6` 和 `jsdom@26.1.0`，并更新 `package-lock.json`。
- 新行为遵循 TDD；离线自动化测试不得依赖真实 iFinD 或生产数据库。
- 用户现有 `.gitignore.bak.20260927`、`AGENTS.md.bak.20260927` 和 `.superpowers/` 不得删除、覆盖或提交。
- 生产发布目标固定为现有 `ifind-monitor` 服务和 `http://115.191.14.82:8000/#/opening-themes`；测试未全绿不得部署。

## Review Focus

- 多股票的分钟点不齐：使用全局时间轴中不晚于请求时点的分钟，并为每只股票独立向下取最近点；Task 1、Task 2 测试。
- 快照在缓存期间被人工替换：`run_id` 进入服务和结果缓存键，新快照不得返回旧归因结果；Task 2 测试。
- 上游并发慢请求或失败：同一行情键只抓取一次；有旧缓存返回 `stale`，没有缓存返回稳定 503；Task 2、Task 3 测试。
- `<keep-alive>` 隐藏页面：停掉轮询和时间轴播放，重新激活时只恢复一个定时器并立即刷新；Task 5 测试。
- 生产库无冻结快照：页面显示可操作空状态，接口返回 `SNAPSHOT_NOT_FOUND`，不得伪造数据或自动运行盘前命令；Task 3、Task 5、Task 6 验证。

---

## File Map

| 文件 | 职责 |
|---|---|
| `opening_strength/realtime_models.py` | 规范化行情、归因、题材指标、贡献明细与看板 DTO |
| `opening_strength/realtime_aggregation.py` | 纯函数指标计算、标签、稳定排序与榜单引用 |
| `opening_strength/quote_provider.py` | `IntradayFetcher` 适配、15 秒缓存、并发合并、分钟切片与旧缓存降级 |
| `opening_strength/dashboard_service.py` | 读取冻结快照、规范化候选与归因、3 秒结果缓存、错误语义 |
| `api/routers/opening_strength.py` | 参数校验、服务装配、HTTP 与稳定错误结构 |
| `api/app.py` | 注册新增路由 |
| `frontend/src/api/openingThemes.ts` | API 类型、成功响应和结构化错误解析 |
| `frontend/src/components/opening-themes/OpeningThemeTable.vue` | 左侧题材强弱榜 |
| `frontend/src/components/opening-themes/OpeningChangeLists.vue` | 右侧加速榜和扩散榜 |
| `frontend/src/components/opening-themes/OpeningContributors.vue` | 下方贡献个股明细 |
| `frontend/src/views/OpeningThemesPage.vue` | 实时/历史控制、时间轴、轮询生命周期、状态和布局 A |
| `frontend/src/router/index.ts`、`frontend/src/layouts/AppLayout.vue` | 新 Tab 和 `#/opening-themes` 路由 |
| `frontend/vite.config.ts`、`frontend/package*.json` | Vitest/jsdom 配置与锁定依赖 |
| `tests/opening_strength/` | 聚合器、行情提供器和服务离线测试 |
| `tests/test_opening_strength_api.py` | FastAPI 契约与错误测试 |
| `frontend/src/**/__tests__/*` | API、组件和页面交互测试 |
| `README.md`、`AGENTS.md`、`docs/architecture/FRONTEND.md`、`docs/guides/api.md` | 使用、边界与接口文档 |

### Task 1: 纯计算模型与题材聚合器

**Files:**
- Create: `opening_strength/realtime_models.py`
- Create: `opening_strength/realtime_aggregation.py`
- Create: `tests/opening_strength/test_realtime_aggregation.py`
- Modify: `tests/test_layering.py`

**Interfaces:**
- Consumes: 盘前仓储行已规范化后的原始值，不直接读取数据库。
- Produces:
  - `AttributedStock(stock_code: str, stock_name: str, theme_code: str, theme_name: str, theme_type: str, attribution_weight: float, confidence: float, reason_codes: tuple[str, ...], source_pool_ids: tuple[str, ...])`
  - `StockQuote(stock_code: str, quote_time: str, pre_close: float, last_price: float, avg_price: float | None, turnover: float | None)`
  - `StockContribution`、`ThemeSnapshot`、`OpeningDashboard` 冻结 dataclass；字段逐一覆盖 spec §5–6。
  - `aggregate_themes(attributions: Sequence[AttributedStock], quotes: Mapping[str, StockQuote], previous_quotes: Mapping[str, StockQuote], *, stale_quote: bool = False) -> tuple[ThemeSnapshot, ...]`
  - `build_rankings(themes: Sequence[ThemeSnapshot]) -> tuple[tuple[ThemeSnapshot, ...], tuple[str, ...], tuple[str, ...]]`

- [ ] **Step 1: Write failing formula and edge-case tests**

Create fixed fixtures for two themes and assert exact `level`, `momentum_1m`, `up_ratio`, `breadth_delta_1m`, `support_weight`, `source_pool_diversity`, `data_health`, contribution values and top-1/top-3 concentration. Include all-negative returns (`concentration=None`), zero/invalid `pre_close`, missing quotes, 09:30 same-minute previous quotes and uneven stock minute points already normalized to the same requested time.

- [ ] **Step 2: Run aggregation tests and verify RED**

Run: `PYTHONPATH=. $PY -m unittest tests.opening_strength.test_realtime_aggregation -v`

Expected: FAIL because `opening_strength.realtime_models` and `realtime_aggregation` do not exist.

- [ ] **Step 3: Implement minimal immutable DTOs and aggregation**

Group by `(theme_code, theme_name, theme_type)`, deduplicate each stock by code, calculate spec §5 formulas, preserve missing-quote contributors at the end, and round only in `to_dict()` serialization. Emit tags with exact thresholds: single stock, support below two, top-1 concentration at least `0.70`, data health below `0.60`, and caller-supplied stale quote.

- [ ] **Step 4: Write and pass stable-ranking tests**

Assert main order `rankable → level desc → momentum desc → code asc`; acceleration and breadth lists require `valid_quote_count >= 2` and `data_health >= 0.60`; their ties end with code ascending. Run the Step 2 command and expect all PASS.

- [ ] **Step 5: Strengthen the layering contract**

Extend `tests/test_layering.py` so `realtime_aggregation.py` and `realtime_models.py` also reject any import of `features` or `attribution`. Run `PYTHONPATH=. $PY -m unittest tests.test_layering -v` and expect PASS.

- [ ] **Step 6: Commit Task 1**

Commit only Task 1 files with message `feat(opening-strength): add realtime theme aggregation`.

### Task 2: 行情切片、缓存与冻结快照编排

**Files:**
- Create: `opening_strength/quote_provider.py`
- Create: `opening_strength/dashboard_service.py`
- Create: `tests/opening_strength/test_quote_provider.py`
- Create: `tests/opening_strength/test_dashboard_service.py`

**Interfaces:**
- Consumes: `IntradayFetcher.fetch_batch(codes: list[str], date: str | None) -> dict`；`Database.get_frozen_opening_run()`、`get_opening_candidates()`、`get_opening_attributions()`；Task 1 interfaces。
- Produces:
  - `QuoteSlice(available_times: tuple[str, ...], snapshot_time: str, latest_time: str, quotes: Mapping[str, StockQuote], previous_quotes: Mapping[str, StockQuote], cache_status: str)`
  - `OpeningQuoteProvider.load(codes: Sequence[str], trade_date: str, snapshot_time: str | None = None) -> QuoteSlice`
  - `OpeningDashboardError(code: str, message: str, retryable: bool)`
  - `OpeningDashboardService.build(trade_date: str, snapshot_time: str | None = None) -> OpeningDashboard`

- [ ] **Step 1: Write failing quote-provider tests**

Use an injected fake fetcher and clock. Assert codes are sorted/deduplicated; today calls `fetch_batch(..., date=None)` while historical dates pass `YYYYMMDD`; all pre-09:30 points are removed; global `available_times` is the ordered union; requested times resolve downward; each stock independently takes its last point not later than the resolved minute; previous quotes use the prior global minute and 09:30 reuses current quotes.

- [ ] **Step 2: Add cache, concurrency and failure tests**

With an injected monotonic clock and barriers, assert today refetches after 15 seconds, history remains stable, simultaneous calls cause one fetch, failed refresh returns prior data with `cache_status='stale'`, and first-load failure raises `OpeningDashboardError('QUOTE_PROVIDER_FAILED', ..., True)` without raw upstream text.

- [ ] **Step 3: Run quote-provider tests and verify RED**

Run: `PYTHONPATH=. $PY -m unittest tests.opening_strength.test_quote_provider -v`

Expected: FAIL because `opening_strength.quote_provider` does not exist.

- [ ] **Step 4: Implement `OpeningQuoteProvider`**

Use a lock plus one in-flight event per `(trade_date, sorted-code digest)`; never hold the global lock during network I/O. Store the last successful full series with fetch timestamp. Raise `QUOTE_DATA_UNAVAILABLE` when the snapshot exists but no 09:30+ point can be resolved. Determine stale-trading-minute status with an injected Asia/Shanghai clock that excludes pre-open, lunch and after-close intervals.

- [ ] **Step 5: Write failing dashboard-service tests**

Build temporary SQLite snapshots and a fake provider. Assert candidates collapse their multiple source rows; only persisted Attribution rows form Theme relationships; no frozen run raises `SNAPSHOT_NOT_FOUND`; `run_id` is in result-cache identity; a replaced run bypasses the old 3-second result; partial quotes produce data-health labels; historical builds make no database writes; no premarket service, `features.py`, or `attribution.py` function is invoked.

- [ ] **Step 6: Implement `OpeningDashboardService` and pass Task 2 tests**

Normalize JSON reason codes already decoded by the repository, build `AttributedStock` rows, call the provider and Task 1 pure functions, and cache serialized results for 3 seconds by `(run_id, resolved request time)`. Run:

`PYTHONPATH=. $PY -m unittest tests.opening_strength.test_quote_provider tests.opening_strength.test_dashboard_service -v`

Expected: all tests PASS.

- [ ] **Step 7: Commit Task 2**

Commit Task 2 files with message `feat(opening-strength): add frozen snapshot dashboard service`.

### Task 3: FastAPI 只读接口

**Files:**
- Create: `api/routers/opening_strength.py`
- Modify: `api/routers/__init__.py`
- Modify: `api/app.py`
- Create: `tests/test_opening_strength_api.py`

**Interfaces:**
- Consumes: `OpeningDashboardService.build()` and `api.deps.db`.
- Produces: `GET /api/opening-strength/dashboard?trade_date=YYYYMMDD[&snapshot_time=HH:MM]` with spec §6 response and errors.

- [ ] **Step 1: Write failing API contract tests**

Using FastAPI `TestClient` and an overridable/lazy service factory, assert success serialization; missing or malformed date, malformed time and time before 09:30 return HTTP 422 with `INVALID_REQUEST`; domain errors map to the exact 404/503 codes and nested `{"error": {"code", "message", "retryable"}}`; unexpected exceptions return sanitized 503 without local paths or secrets.

- [ ] **Step 2: Run API tests and verify RED**

Run: `PYTHONPATH=. $PY -m unittest tests.test_opening_strength_api -v`

Expected: FAIL because the route is not registered.

- [ ] **Step 3: Implement the thin router and lazy singleton**

Validate with anchored date/time expressions plus `datetime.strptime`; instantiate `IntradayFetcher` only on the first valid dashboard request; return `JSONResponse` for the stable error shape. Keep all metric and cache decisions out of the router.

- [ ] **Step 4: Verify route isolation and API suite**

Run: `PYTHONPATH=. $PY -m unittest tests.test_opening_strength_api tests.test_layering -v`

Expected: all tests PASS; importing `api.app` must not construct `IntradayFetcher` or require `KLINE_API_BASE_URL`.

- [ ] **Step 5: Commit Task 3**

Commit Task 3 files with message `feat(api): expose opening theme dashboard`.

### Task 4: 前端 API 契约与测试基础

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/vite.config.ts`
- Create: `frontend/src/api/openingThemes.ts`
- Create: `frontend/src/api/__tests__/openingThemes.spec.ts`
- Create: `frontend/src/test/setup.ts`

**Interfaces:**
- Consumes: Task 3 JSON contract and existing Axios client.
- Produces:
  - TypeScript types `OpeningDashboardPayload`, `OpeningTheme`, `OpeningContribution`, `OpeningDashboardErrorPayload`.
  - `getOpeningThemesDashboard(params: { trade_date: string; snapshot_time?: string }): Promise<OpeningDashboardPayload>`.
  - npm script `test` invoking Vitest.

- [ ] **Step 1: Install only approved test dependencies**

Run in `frontend/`: `npm install --save-dev vitest@3.2.4 @vue/test-utils@2.4.6 jsdom@26.1.0`.

Expected: only `package.json` and `package-lock.json` dependency metadata changes; no `node_modules` or build output is staged.

- [ ] **Step 2: Configure Vitest and write failing API tests**

Configure jsdom, Vue plugin reuse, `@` alias and `src/test/setup.ts`. Mock Axios and assert exact query parameters, successful typed payload, and preservation of nested server error code/message/retryable for the page state machine.

- [ ] **Step 3: Run frontend API test and verify RED**

Run: `cd frontend && npm test -- --run src/api/__tests__/openingThemes.spec.ts`

Expected: FAIL because `openingThemes.ts` does not exist.

- [ ] **Step 4: Implement the API module and verify GREEN**

Use the existing client instance; do not add another Axios singleton. Run the Step 3 command and expect PASS, then run `npm run type-check` and expect exit 0.

- [ ] **Step 5: Commit Task 4**

Commit Task 4 files with message `test(frontend): add opening dashboard test harness`.

### Task 5: 布局 A 页面、回放与生命周期

**Files:**
- Create: `frontend/src/components/opening-themes/OpeningThemeTable.vue`
- Create: `frontend/src/components/opening-themes/OpeningChangeLists.vue`
- Create: `frontend/src/components/opening-themes/OpeningContributors.vue`
- Create: `frontend/src/components/opening-themes/__tests__/OpeningThemeComponents.spec.ts`
- Create: `frontend/src/views/OpeningThemesPage.vue`
- Create: `frontend/src/views/__tests__/OpeningThemesPage.spec.ts`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/layouts/AppLayout.vue`

**Interfaces:**
- Consumes: Task 4 API/types; existing `TimeBar.vue`, `usePolling()` and `usePlayTimeline()`.
- Produces: top-level route name `opening-themes`, path `/opening-themes`, title `开盘题材`; component events select a `theme_code` shared across all three panels.

- [ ] **Step 1: Write failing component tests**

Assert the main table renders level, momentum, support/valid counts, breadth delta, concentration, data health and all five risk labels; emits the clicked code. Assert change lists follow API code order and emit selection. Assert contributor rows show positive, negative and missing quotes in API order with sources, weight, confidence and reasons.

- [ ] **Step 2: Implement the three focused presentation components**

Use semantic buttons/rows with keyboard activation and `aria-selected`; keep formatting helpers local or in existing `utils/format.ts`, not in the page orchestration. Run the component test and expect PASS.

- [ ] **Step 3: Write failing page-state and lifecycle tests**

With fake timers and mocked API, cover: real-time date fixed to current Asia/Shanghai date; history date editable; 3-second auto-follow polling; slider stops auto-follow; play requests each minute; stale async response is discarded; selection survives refresh or falls back to first theme; loading, snapshot-not-found command, quote unavailable, degraded and generic error states. Mount within `<KeepAlive>` and assert deactivation stops polling/playback and reactivation performs one immediate refresh without duplicate timers.

- [ ] **Step 4: Implement `OpeningThemesPage.vue`**

Compose layout A and reuse `TimeBar`. Use `onActivated`, `onDeactivated` and `onUnmounted` to manage polling/playback. Do not provide a browser button that runs premarket attribution. Make the grid responsive: main/side columns above 1100px, one column below it; keep the contributors section full width.

- [ ] **Step 5: Add the top-level Tab and route**

Register the lazy page component and insert “开盘题材” after “板块强度监控”. Verify direct hash navigation and that existing tab names/routes remain unchanged.

- [ ] **Step 6: Run complete frontend verification**

Run in `frontend/`:

```text
npm test -- --run
npm run type-check
npm run build
```

Expected: all tests PASS, type check exits 0, and Vite produces `../static/index.html` without warnings that indicate broken imports.

- [ ] **Step 7: Commit Task 5**

Commit frontend source and tests, excluding `static/`, with message `feat(frontend): add opening theme dashboard`.

### Task 6: 文档、全量验证与生产发布

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/architecture/FRONTEND.md`
- Modify: `docs/guides/api.md`
- Modify: `docs/reference/CHANGELOG.md`

**Interfaces:**
- Consumes: Tasks 1–5 and the existing deployment contract in `docs/ops/DEPLOYMENT.md`.
- Produces: current operating documentation, pushed commits, rebuilt static assets and a restarted/verified `ifind-monitor` service.

- [ ] **Step 1: Update project knowledge**

Document the eighth Tab, route, API, formulas, frozen-snapshot prerequisite, manual command, 3-second/15-second caches, test commands and empty-production behavior. Record that scheduling, auction, custom static themes, WebSocket and ranking persistence remain excluded.

- [ ] **Step 2: Run focused backend verification**

Run:

```text
PYTHONPATH=. $PY -m unittest tests.opening_strength.test_realtime_aggregation tests.opening_strength.test_quote_provider tests.opening_strength.test_dashboard_service tests.test_opening_strength_api tests.test_layering -v
```

Expected: all tests PASS; only the pre-existing optional import-linter test may skip when unavailable.

- [ ] **Step 3: Run full backend and documentation verification**

Run:

```text
PYTHONPATH=. $PY -m unittest discover -s tests -t . -p 'test_*.py' -v
$PY scripts/lint_docs.py
git diff --check
```

Expected: no failures/errors, documentation reports `0 errors / 0 warnings`, and whitespace check is empty. Report exact pass/skip counts.

- [ ] **Step 4: Re-run complete frontend verification from the lockfile**

Run in `frontend/`: `npm ci && npm test -- --run && npm run type-check && npm run build`.

Expected: dependency install follows `package-lock.json`; tests, type check and production build all exit 0.

- [ ] **Step 5: Commit docs and request whole-branch review**

Commit documentation with message `docs: document opening theme dashboard`. Use a fresh reviewer to compare the branch to the spec, then fix and re-run affected tests before release.

- [ ] **Step 6: Push the verified `main` branch**

Confirm `git status --short` contains only the three known untracked user/scratch entries, then run `git push origin main`. Verify local and remote `main` point at the same commit.

- [ ] **Step 7: Deploy the existing production service**

Record the pre-deploy commit and service state. From the production worktree run `git pull --ff-only`, `cd frontend && npm ci && npm run build`, then `sudo systemctl restart ifind-monitor`. Do not run `opening-premarket` and do not alter the production database.

- [ ] **Step 8: Production API and UI smoke verification**

Verify service is active and its start time is newer than the deployment. Check:

- `/` returns 200 and the new hashed SPA assets return 200;
- `#/opening-themes` loads the Vue page without a blank screen;
- `/api/opening-strength/dashboard?trade_date=<today>` returns either a valid dashboard or the expected structured `SNAPSHOT_NOT_FOUND` for the currently empty production opening tables;
- `#/sector` and one other existing Tab still load;
- frontend component/UI tests already exercise populated, partial-data and error render paths.

If restart or smoke verification fails, stop, collect the bounded error, and restore the previously recorded code/build before reporting failure.

- [ ] **Step 9: Final release record**

Update the handoff with commit hash, test counts, frontend checks, deployment/restart time, public URL, actual production data state and any remaining runtime prerequisite. Never claim real rankings are visible when the production date lacks a frozen snapshot.

## Final Verification

- [ ] `git status --short` contains no task residue and preserves the three pre-existing untracked entries.
- [ ] Every task has an implementation commit and reviewer result; the whole branch has a final independent review.
- [ ] Full backend suite, Vitest, type check, Vite build, docs lint and `git diff --check` are green immediately before release.
- [ ] `origin/main` equals the deployed production commit.
- [ ] `ifind-monitor` restarted successfully and public old/new routes were smoke-tested.
- [ ] No token, database, log, `node_modules`, `static/` output or scratch file is committed.
