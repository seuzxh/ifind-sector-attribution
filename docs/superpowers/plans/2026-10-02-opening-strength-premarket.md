---
title: "计划：开盘板块强弱盘前归因"
parent: "架构与设计"
nav_order: 22
---

# 开盘板块强弱盘前归因 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现三特色指数候选池到概念/行业 Top 1～3 归因的可解释、可回放、原子冻结盘前链路。

**Architecture:** 在现有单体内新增独立 `opening_strength` 领域包；sector-hub 只提供源池和权威 Membership，monitor 保存运行版本和冻结结果。数据库继续由 `database` 包统一管理，旧实时引擎和题材催化模块不参与第一阶段链路。

**Tech Stack:** Python 3.11、标准库 dataclasses/statistics/sqlite3、ifind-sector-hub、unittest；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-02-opening-strength-premarket-design.md`

## Global Constraints

- Python 固定使用 `/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python`；下文 `$PY` 指向该绝对路径，命令在仓库根目录执行并设置 `PYTHONPATH=.`。
- 股票仅接受 `.SH/.SZ/.BJ`，必须调用 `config.is_a_share_code()`；Theme 仅接受 `884/885/886`。
- 行业为 `884xxx`，概念为 `885/886xxx`；不处理 `700/881` 和自定义静态板块。
- 源池固定为 `883926.TI`、`883409.TI`、`883910.TI`，任一失败、无效或为空均阻止冻结。
- 实时、API 和前端不在本计划范围内；`opening_strength` 不得导入 `realtime_engine`、`theme_catalyst` 或 `api`。
- 所有新增行为先写失败测试，再写最小实现；真实 iFinD 仅用于可跳过 smoke test。
- 全部实现最多四个逻辑提交；现有未跟踪备份文件不得纳入提交。

## Review Focus

- 同一股票在两个源池出现且名称不同：以源池定义顺序中首次非空名称为准，仍保留两条来源记录；Task 1 测试。
- sector-hub 某个 Theme 发布了合法空快照：不得回退旧成员，也不得把候选错误映射进去；Task 1 测试。
- 日线含 `None`、重复日期或目标交易日数据：忽略无效值、日期去重并严格排除目标日；Task 2 测试。
- 同日新版本冻结事务中途失败：旧 `FROZEN` 必须仍是唯一可读版本；Task 3 测试。
- 当天 09:30 后重跑且已有冻结版本：无 `--force-replace` 时拒绝替换，有参数时允许；Task 3 服务测试和 Task 4 CLI 传参测试。

---

## File Map

| 文件 | 职责 |
|---|---|
| `opening_strength/models.py` | 领域枚举、不可变 DTO、默认版本化归因配置 |
| `opening_strength/source_pools.py` | p03473 适配、三池解析、候选合并 |
| `opening_strength/memberships.py` | sector-hub 权威 Membership 批量读取与反转 |
| `opening_strength/features.py` | 历史日线补齐、近期强度和 20 日同步度 |
| `opening_strength/attribution.py` | 纯函数评分、Top Theme 选择、原因码和权重 |
| `opening_strength/snapshot_service.py` | 盘前运行编排、校验、失败记录与冻结 |
| `database/opening_strength.py` | 四张新表的读写和原子冻结仓储 |
| `database/schema.py` | 四张表、索引及单日唯一冻结约束 |
| `database/core.py` | 组装 `OpeningStrengthMixin` |
| `main.py` | `opening-premarket` 薄命令入口 |
| `tests/opening_strength/` | 与领域包镜像的单元和服务测试 |
| `tests/database/test_opening_strength.py` | 仓储、状态机和事务测试 |
| `tests/test_opening_strength_cli.py` | CLI 参数与 09:30 替换保护测试 |
| `tests/test_layering.py` | 把新领域包纳入分层扫描 |

### Task 1: 候选池与权威 Membership

**Files:**
- Create: `opening_strength/__init__.py`
- Create: `opening_strength/models.py`
- Create: `opening_strength/source_pools.py`
- Create: `opening_strength/memberships.py`
- Create: `tests/opening_strength/__init__.py`
- Create: `tests/opening_strength/test_source_pools.py`
- Create: `tests/opening_strength/test_memberships.py`

**Interfaces:**
- Consumes: `config.is_a_share_code()`；sector-hub client 的 `get_concept_members(code, date)`；store 的 `get_concept_names()`、`get_concept_members_map(codes)`、`get_latest_member_date()`。
- Produces:
  - `SourcePoolSpec(pool_id: str, name: str, index_code: str)`
  - `PoolSource(pool_id: str, source_rank: int)`
  - `CandidateStock(stock_code: str, stock_name: str, sources: tuple[PoolSource, ...])`
  - `ThemeMembership(stock_code: str, theme_code: str, theme_name: str, theme_type: ThemeType)`
  - `MembershipResolution(memberships: tuple[ThemeMembership, ...], unmapped_stock_codes: tuple[str, ...], hub_member_date: str, mapped_count: int, coverage_ratio: float)`
  - `IFindSourcePoolProvider.resolve(spec: SourcePoolSpec, trade_date: str) -> tuple[tuple[str, str], ...]`
  - `resolve_candidates(provider, trade_date: str, specs=SOURCE_POOL_SPECS) -> tuple[CandidateStock, ...]`
  - `resolve_memberships(store, candidates: Sequence[CandidateStock]) -> MembershipResolution`

- [ ] **Step 1: Write failing source-pool tests**

Add tests asserting that duplicate `600001.SH` from `high_beta` and `hot_stock` becomes one candidate with two ordered `PoolSource` entries; the first nonempty name wins; `00700.HK` is filtered; missing `tables`, mismatched `p03473_f001/f002` lengths, and an empty fixed pool raise `SourcePoolError` with distinct reason codes.

- [ ] **Step 2: Run source-pool tests and verify RED**

Run: `PYTHONPATH=. /root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python -m unittest tests.opening_strength.test_source_pools -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'opening_strength'`.

- [ ] **Step 3: Implement models and source-pool resolver**

Define `SOURCE_POOL_SPECS` in the order high beta, recent strong, hot stock. Parse `p03473_f001` as names and `p03473_f002` as codes; use the API list index plus one as `source_rank`. Keep DTOs frozen and candidate ordering by first source-pool appearance, then source rank, then stock code.

- [ ] **Step 4: Run source-pool tests and verify GREEN**

Run the Step 2 command. Expected: all tests PASS.

- [ ] **Step 5: Write failing Membership tests**

Use a fake store whose dictionary includes `700/881/884/885/886` and whose bulk member map includes current, empty and unrelated Theme rows. Assert that only `884/885/886` are requested; relations are restricted to candidates; `884` maps to `INDUSTRY`; `885/886` map to `CONCEPT`; unmapped candidates are reported; a valid empty latest Theme does not resurrect an older relation.

- [ ] **Step 6: Run Membership tests and verify RED**

Run: `PYTHONPATH=. $PY -m unittest tests.opening_strength.test_memberships -v` with `PY=/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python`.

Expected: FAIL because `opening_strength.memberships` does not exist.

- [ ] **Step 7: Implement Membership resolver**

`MembershipResolution` contains ordered `memberships`, `unmapped_stock_codes`, `hub_member_date`, `mapped_count` and `coverage_ratio`. Sort relations by stock code, Theme type (`INDUSTRY` first) and Theme code. Perform one `get_concept_members_map()` call, never one connection per stock.

- [ ] **Step 8: Run Task 1 tests and verify GREEN**

Run: `PYTHONPATH=. $PY -m unittest discover -s tests/opening_strength -p 'test_source_pools.py' -v && PYTHONPATH=. $PY -m unittest discover -s tests/opening_strength -p 'test_memberships.py' -v`.

Expected: both suites PASS.

- [ ] **Step 9: Commit Task 1**

Commit only Task 1 files with message `feat(opening-strength): 添加候选池与权威归属解析`.

### Task 2: 历史特征与归因引擎

**Files:**
- Create: `opening_strength/features.py`
- Create: `opening_strength/attribution.py`
- Create: `tests/opening_strength/test_features.py`
- Create: `tests/opening_strength/test_attribution.py`

**Interfaces:**
- Consumes: Task 1 DTO；`Database.get_daily_kline()`、`save_daily_kline()`；hub client `get_history_quotation()`。
- Produces:
  - `AttributionConfig(model_version='opening-attribution-v1', config_version='opening-default-v1', feature_weights=(0.30, 0.35, 0.20, 0.15), return_window_weights=(0.4, 0.3, 0.2, 0.1), min_confidence=0.35, support_saturation=5, sync_window=20, sync_min_observations=10, max_industries=1, max_concepts=2)`
  - `HistoricalFeatures(theme_recent_strength: Mapping[str, float | None], stock_theme_sync: Mapping[tuple[str, str], float | None], coverage_ratio: float)`
  - `AttributionItem(stock_code: str, theme_code: str, theme_name: str, theme_type: ThemeType, rank: int, raw_score: float, weight: float, confidence: float, reason_codes: tuple[str, ...], evidence: Mapping[str, object])`
  - `DailyHistoryFeatureProvider.build(trade_date: str, candidates, memberships) -> HistoricalFeatures`
  - `score_attributions(candidates, memberships, history, config=DEFAULT_ATTRIBUTION_CONFIG) -> tuple[AttributionItem, ...]`

- [ ] **Step 1: Write failing historical-feature tests**

Assert with fixed series that an N-day return is `product(1 + change_ratio_i / 100) - 1` over the latest N valid sessions; each window uses average-rank percentile `(average_rank - 1) / (theme_count - 1)`, with a single Theme defined as `1.0`; the four percentiles combine as `0.4/0.3/0.2/0.1`. A 20-observation perfectly aligned pair maps correlation `1.0` to sync `(corr + 1) / 2 = 1.0`; fewer than 10 common observations returns `None`. Include `None`, duplicate dates and a row equal to `trade_date`, and assert they are ignored or excluded as specified.

- [ ] **Step 2: Run feature tests and verify RED**

Run: `PYTHONPATH=. $PY -m unittest tests.opening_strength.test_features -v`.

Expected: FAIL because `opening_strength.features` does not exist.

- [ ] **Step 3: Implement `DailyHistoryFeatureProvider`**

Fetch a 60-calendar-day window ending the day before `trade_date`, in batches of 50, with indicators `preClose,close,changeRatio`. Parse the current sector-hub response shape, save normalized `YYYYMMDD` rows through `Database`, merge with cached rows by `(code, trade_date)`, and compute using at most the latest 20 valid observations. A fetch failure leaves the affected feature missing rather than substituting stale data as current.

- [ ] **Step 4: Run feature tests and verify GREEN**

Run the Step 2 command. Expected: all tests PASS.

- [ ] **Step 5: Write failing attribution tests**

Use one stock with two `884` and three `885/886` Memberships. Assert raw score `0.30*recent + 0.35*support + 0.20*diversity + 0.15*sync` when complete; missing sync re-normalizes the other weights and sets feature coverage `0.85`; confidence is `0.45*coverage + 0.35*support + 0.20*diversity`; selection keeps one industry and two concepts; normalized weights sum to 1 within `1e-6`.

Add cases for stable tie ordering, zero-score equal weights, low-confidence one-item fallback, `LOW_SUPPORT`, `MISSING_HISTORY`, and multi-pool peer support. Name conflicts from Review Focus do not affect scoring identity, which is code-based.

- [ ] **Step 6: Run attribution tests and verify RED**

Run: `PYTHONPATH=. $PY -m unittest tests.opening_strength.test_attribution -v`.

Expected: FAIL because `opening_strength.attribution` does not exist.

- [ ] **Step 7: Implement attribution pure functions**

Compute peer support excluding the current stock and saturate at five; compute diversity as distinct source pools across all candidate members of the Theme divided by three. If a feature is missing, divide the available weighted sum by the sum of its available base weights. Emit `AttributionItem` with rank, raw score, normalized weight, confidence, sorted reason codes and a JSON-serializable evidence mapping.

Reason thresholds are fixed for v1: recent strength `>=0.75` gives `STRONG_RECENT_THEME`; at least two peers gives `PEER_CONFIRMATION`, otherwise `LOW_SUPPORT`; at least two source pools gives `MULTI_POOL_SUPPORT`; sync `>=0.75` gives `HIGH_SYNCHRONY`; either historical feature missing gives `MISSING_HISTORY`; confidence below `0.35` gives `LOW_CONFIDENCE`. Do not read databases or call external clients in this module.

- [ ] **Step 8: Run Task 2 tests and verify GREEN**

Run: `PYTHONPATH=. $PY -m unittest tests.opening_strength.test_features tests.opening_strength.test_attribution -v`.

Expected: all tests PASS.

- [ ] **Step 9: Commit Task 2**

Commit Task 2 files together with any Task 1 model additions using message `feat(opening-strength): 实现历史特征与归因评分`.

### Task 3: 快照仓储与盘前编排

**Files:**
- Modify: `database/schema.py`
- Create: `database/opening_strength.py`
- Modify: `database/core.py`
- Create: `opening_strength/snapshot_service.py`
- Create: `tests/database/__init__.py`
- Create: `tests/database/test_opening_strength.py`
- Create: `tests/opening_strength/test_snapshot_service.py`

**Interfaces:**
- Consumes: Tasks 1–2 interfaces and existing `Database` connection lifecycle.
- Produces repository methods:
  - `create_opening_run(run: Mapping) -> None`
  - `save_opening_candidates(run_id: str, rows: Sequence[Mapping]) -> None`
  - `save_opening_memberships(run_id: str, rows: Sequence[Mapping]) -> None`
  - `save_opening_attributions(run_id: str, rows: Sequence[Mapping]) -> None`
  - `mark_opening_run_validated(run_id: str, metrics: Mapping) -> None`
  - `freeze_opening_run(run_id: str, replace_existing: bool) -> None`
  - `fail_opening_run(run_id: str, failure_code: str, failure_detail: str) -> None`
  - `get_frozen_opening_run(trade_date: str) -> dict | None`
  - `get_opening_candidates(run_id: str) -> list[dict]`
  - `get_opening_memberships(run_id: str) -> list[dict]`
  - `get_opening_attributions(run_id: str, stock_code: str | None = None) -> list[dict]`
- Produces service method: `OpeningPremarketService.run_and_freeze(trade_date: str, force_replace: bool = False) -> PremarketRunResult`.
- `PremarketRunResult` exposes `run_id`, `trade_date`, `status`, `candidate_count`, `mapped_count`, `coverage_ratio` and `history_coverage_ratio`.

- [ ] **Step 1: Write failing schema and repository tests**

Create a temporary database and assert all four tables exist. Test full insert/read round trip; reject transition from `RUNNING` directly to `FROZEN`; freeze only `VALIDATED`; successful replacement changes the old run to `SUPERSEDED`; `replace_existing=False` preserves the old run and raises `FrozenRunExistsError`.

Inject a SQLite trigger that aborts the new-run `FROZEN` update and assert the replacement transaction rolls back, leaving the old run as the sole `FROZEN` record.

- [ ] **Step 2: Run repository tests and verify RED**

Run: `PYTHONPATH=. $PY -m unittest tests.database.test_opening_strength -v`.

Expected: FAIL because the tables and mixin do not exist.

- [ ] **Step 3: Implement schema and `OpeningStrengthMixin`**

Use the four tables and fields from spec §6. Store reasons/evidence as deterministic JSON (`ensure_ascii=False, sort_keys=True`, no NaN). Add a partial unique index on `opening_premarket_run(trade_date) WHERE status='FROZEN'`. In one transaction, validate current state, supersede the old frozen row, then freeze the new row.

- [ ] **Step 4: Run repository tests and verify GREEN**

Run the Step 2 command. Expected: all tests PASS.

- [ ] **Step 5: Write failing service tests**

With fake pool, Membership and history dependencies, assert the exact state sequence, persisted provenance, `coverage_ratio`, and frozen result. Add failures for one empty source pool, Membership coverage `0.899`, invalid prefix, more than one industry, more than two concepts, weight sum beyond `1e-6`, and missing model/config versions. Assert every failure marks only the current run `FAILED` and leaves an old frozen run untouched.

Add deterministic replay: run identical fixtures twice before 09:30 and assert candidates, Memberships, ranks, raw scores, confidence, weights, reasons and evidence are equal after excluding `run_id` and timestamps.

With an injected Asia/Shanghai clock and an existing frozen run for today, assert 09:29 replacement is allowed, 09:30 replacement is rejected unless `force_replace=True`, and historical-date replacement ignores the wall-clock guard.

- [ ] **Step 6: Run service tests and verify RED**

Run: `PYTHONPATH=. $PY -m unittest tests.opening_strength.test_snapshot_service -v`.

Expected: FAIL because `opening_strength.snapshot_service` does not exist.

- [ ] **Step 7: Implement `OpeningPremarketService`**

Constructor receives repository, pool provider, Membership store, history provider, config and a clock callable. `run_and_freeze()` creates `RUNNING`, persists immutable inputs, scores, validates, marks `VALIDATED`, and calls the repository atomic freeze. Convert exceptions to bounded `failure_code/failure_detail` without tokens or raw API responses, then re-raise a domain `PremarketRunError` carrying `run_id`.

- [ ] **Step 8: Run Task 3 tests and verify GREEN**

Run: `PYTHONPATH=. $PY -m unittest tests.database.test_opening_strength tests.opening_strength.test_snapshot_service -v`.

Expected: all tests PASS.

- [ ] **Step 9: Commit Task 3**

Commit Task 3 files with message `feat(opening-strength): 添加盘前快照冻结链路`.

### Task 4: CLI、分层约束与全链路验收

**Files:**
- Modify: `main.py`
- Modify: `tests/test_layering.py`
- Create: `tests/test_opening_strength_cli.py`
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: `OpeningPremarketService.run_and_freeze()` and `get_hub()`.
- Produces CLI: `python main.py opening-premarket --date YYYYMMDD [--force-replace]`.

- [ ] **Step 1: Write failing CLI tests**

Patch the service factory and call `main()` with argv. Assert `--date` is required and eight digits; normal invocation passes `force_replace=False`; the flag passes `True`; successful output includes `run_id/status/candidate_count/mapped_count/coverage_ratio`; domain errors return exit code 1 without printing secrets.

Assert the CLI passes `force_replace=False` by default and `True` only when `--force-replace` is present; the time policy itself remains covered by Task 3 service tests.

- [ ] **Step 2: Run CLI tests and verify RED**

Run: `PYTHONPATH=. $PY -m unittest tests.test_opening_strength_cli -v`.

Expected: FAIL because the subcommand is absent.

- [ ] **Step 3: Implement thin CLI assembly**

Add `cmd_opening_premarket(args)` with lazy imports. Build `Database`, `get_hub()`, source-pool provider, Membership resolver, history provider and service; keep all business decisions in the service. Register the exact command and flags from the interface block.

- [ ] **Step 4: Extend layering tests**

Add `opening_strength` to the scanned packages and engine layer. Add an assertion that its modules never import `realtime_engine`, `theme_catalyst` or `api`; run `PYTHONPATH=. $PY -m unittest tests.test_layering -v` and expect PASS.

- [ ] **Step 5: Update operator documentation**

Document the new command, four tables, three source indices, Theme prefix scope, offline-test behavior and the first-stage no-UI boundary in `README.md` and `AGENTS.md`. Do not update `data/DATABASE_MANIFEST.json` until a real runtime database has been migrated and inspected.

- [ ] **Step 6: Run focused opening-strength suite**

Run: `PYTHONPATH=. $PY -m unittest discover -s tests/opening_strength -p 'test_*.py' -v && PYTHONPATH=. $PY -m unittest tests.database.test_opening_strength tests.test_opening_strength_cli tests.test_layering -v`.

Expected: all tests PASS; only documented live smoke tests may skip.

- [ ] **Step 7: Run full backend suite**

Run: `PYTHONPATH=. $PY -m unittest discover -s tests -p 'test_*.py' -v`.

Expected: exit 0 with no failures or errors; report the exact pass/skip counts.

- [ ] **Step 8: Run documentation and whitespace checks**

Run: `$PY scripts/lint_docs.py && git diff --check`.

Expected: `0 errors / 0 warnings` and no whitespace errors.

- [ ] **Step 9: Commit Task 4**

Commit Task 4 files and any directly related documentation with message `feat(cli): 接入盘前归因快照命令`.

## Final Verification

- [ ] Confirm `git status --short` contains only the two pre-existing backup files and no task residue.
- [ ] Confirm exactly four implementation commits at most, each matching the project Conventional Commits rules.
- [ ] Confirm no runtime database, token file, logs, static build output or scratch file is staged.
- [ ] Run the full backend suite and docs lint again immediately before any completion claim.
