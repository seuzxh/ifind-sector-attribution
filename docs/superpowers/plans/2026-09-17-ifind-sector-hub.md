# ifind-sector-hub 抽离实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 monitor 的板块/概念数据层（ifind client + 三表存储 + 同步流程）抽离为独立 Python 包 `ifind-sector-hub`，monitor 全量切换为组件调用方，行为等价。

**Architecture:** 新包按 codes/tokens/client/storage/sync/service 六模块分层，`SectorHub` 门面聚合；monitor 侧 `Database` 类保留为门面（板块方法一行委托组件，调用方签名不变），新增 `ifind_hub.py` 提供进程级单例（FileTokenStore 落盘 token）。数据不迁移：组件存储指向现有 `data/sector_attribution.db`。

**Tech Stack:** Python 3（conda env vibe-trading）、requests、sqlite3（标准库）、FastAPI（仅 service 可选层）、unittest（不引入 pytest）。

**Spec:** `docs/architecture/DESIGN-ifind-sector-hub.md`（本计划从 spec 出发，执行者须同时读 spec）

## Global Constraints

- **Python 解释器**：一律用 `/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python`（下文以 `$PY` 代指）。monitor 命令须在 `/root/projects/2.monitor_940/ifind-sector-attribution` 下执行且 `PYTHONPATH=.`。
- **测试框架**：unittest（`$PY -m unittest`），环境无 pytest，禁止引入。
- **A股硬约束**：个股代码只认 `.SH/.SZ/.BJ`，概念前缀只认 `700/881/883/884/885/886`；判定只用组件 `ifind_sector_hub.codes` 的函数，不写新正则。
- **行为等价红线**（spec §八 全文适用）：日期格式跨表差异（字典/映射 `YYYY-MM-DD`，其余 `YYYYMMDD`）原样保留；三表不传日期取 `MAX(date)` 快照；`replace_concept_dict` 替换+级联清理+watched 迁移同事务；watched 种子逻辑不变；对外 API 响应结构零变化。
- **git 纪律**：monitor 仓库 main 分支上有他人未提交改动（README.md、api_server.py、theme_catalyst 相关）——**只 `git add <本任务明确列出的文件>`，严禁 `git add -A`/`git add .`，严禁 push**。包仓库（Task 1 新建）无此限制。
- **不碰**：`core_calculator.py`、`kg_builder.py`、`kg_analysis.py`、`scan_push.py`、`open_scan_engine.py`、`rotation_agent.py`、`frontend/`、数据库文件、`config_local.py`。
- **真实 API 只在 Task 10 冒烟一次**（`main.py test`）；其余任务全部 mock，不消耗配额。

## File Map

**包仓库 `/root/Projects/ifind-sector-hub/`**（Task 1 `git init`）：

| 文件 | 职责 |
|---|---|
| `pyproject.toml` | 包元数据；核心依赖仅 requests；optional-dependencies `service = ["fastapi", "pydantic"]` |
| `ifind_sector_hub/codes.py` | A股代码/前缀过滤（自 monitor config.py 迁入） |
| `ifind_sector_hub/tokens.py` | TokenStore / FileTokenStore（刷新+落盘+锁） |
| `ifind_sector_hub/client.py` | IfindClient（自 monitor ifind_client.py 迁入，配置注入化） |
| `ifind_sector_hub/storage.py` | SectorStore：三表 DDL + 快照读写 + replace_concept_dict |
| `ifind_sector_hub/sync.py` | SectorSync：字典/成分/映射同步编排 |
| `ifind_sector_hub/service.py` | 可选 FastAPI APIRouter（只交付不部署） |
| `ifind_sector_hub/__init__.py` | HubConfig + SectorHub 门面 |
| `tests/test_*.py` | 包单测（unittest + mock） |

**monitor 仓库 `/root/projects/2.monitor_940/ifind-sector-attribution/`**：

| 文件 | 改动 |
|---|---|
| `ifind_hub.py` | 新增：进程级 hub 单例（FileTokenStore）+ `refresh_token_now()` |
| `database.py` | 三表方法委托 hub.store；删三表 DDL 与 `get_concept_stocks`；watched 迁移钩子 |
| `sync_pipeline.py` | 板块半边薄编排（委托 hub.sync） |
| `main.py` | cmd_refresh_boards 重排（走 hub，去重接口2解析） |
| `api_server.py` | 2 处裸 SQL 收编 |
| `realtime_engine.py` / `sector_manage.py` / `auction_engine.py` / `kg_sources.py` / `theme_catalyst.py` | 各自裸 SQL/import 收编 |
| `ifind_client.py` | **删除** |
| `scripts/backfill_style_history.py` | import 切换 |
| `scripts/verify_sector_hub_equivalence.py` | 新增：等价性验证 |
| `config.py` / `requirements.txt` / `install_service.sh` / `.gitignore` / `AGENTS.md` / `README.md` / `docs/architecture/ARCHITECTURE.md` / spec 访问器清单微调 | 收尾 |

---

### Task 1: 包骨架 + codes 模块

**Files:**
- Create: `/root/Projects/ifind-sector-hub/pyproject.toml`
- Create: `/root/Projects/ifind-sector-hub/ifind_sector_hub/__init__.py`（本任务先只导出 codes）
- Create: `/root/Projects/ifind-sector-hub/ifind_sector_hub/codes.py`
- Test: `/root/Projects/ifind-sector-hub/tests/test_codes.py`

**Interfaces:**
- Produces: `is_a_share_code(code: str) -> bool`、`is_a_share_concept(concept_code: str) -> bool`、`A_SHARE_SUFFIXES: tuple`、`A_SHARE_CONCEPT_PREFIXES: tuple`（后续所有任务的过滤判定入口）

- [ ] **Step 1: 建骨架与 pyproject**

```bash
mkdir -p /root/Projects/ifind-sector-hub/ifind_sector_hub /root/Projects/ifind-sector-hub/tests
cd /root/Projects/ifind-sector-hub && git init
```

`pyproject.toml`：

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "ifind-sector-hub"
version = "0.1.0"
description = "ifind 板块/概念数据层：客户端、三表存储、同步编排（公共组件）"
requires-python = ">=3.9"
dependencies = ["requests>=2.31.0"]

[project.optional-dependencies]
service = ["fastapi>=0.104.0", "pydantic>=2.5.0"]

[tool.setuptools.packages.find]
include = ["ifind_sector_hub*"]
```

`ifind_sector_hub/__init__.py`（本任务临时内容，Task 6 会重写为门面）：

```python
from .codes import A_SHARE_CONCEPT_PREFIXES, A_SHARE_SUFFIXES, is_a_share_code, is_a_share_concept

__all__ = ["A_SHARE_CONCEPT_PREFIXES", "A_SHARE_SUFFIXES", "is_a_share_code", "is_a_share_concept"]
```

- [ ] **Step 2: 写失败测试**

`tests/test_codes.py`（逻辑逐字对应 monitor `config.py:68-85`）：

```python
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from ifind_sector_hub.codes import is_a_share_code, is_a_share_concept


class CodesTests(unittest.TestCase):
    def test_a_share_code(self):
        self.assertTrue(is_a_share_code("600519.SH"))
        self.assertTrue(is_a_share_code("000001.SZ"))
        self.assertTrue(is_a_share_code("430047.BJ"))
        self.assertFalse(is_a_share_code("AAPL.US"))
        self.assertFalse(is_a_share_code(None))

    def test_a_share_concept(self):
        for pfx in ("700", "881", "883", "884", "885", "886"):
            self.assertTrue(is_a_share_concept(pfx + "001.TI"), pfx)
        # 海外前缀（spec：861/864/865/871/875 必须排除）
        for pfx in ("861", "864", "865", "871", "875"):
            self.assertFalse(is_a_share_concept(pfx + "001.TI"), pfx)
        self.assertFalse(is_a_share_concept(None))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试确认失败**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_codes -v
```
Expected: FAIL/ERROR（`ModuleNotFoundError: ifind_sector_hub.codes`）

- [ ] **Step 4: 实现 codes.py**

`ifind_sector_hub/codes.py`（从 monitor `config.py:68-85` 迁入，逻辑逐字保留）：

```python
# -*- coding: utf-8 -*-
"""A股市场过滤（数据域有效性约束：本组件只服务沪深北 A 股板块/概念数据）。"""

# A股个股代码后缀（沪深北交易所）
A_SHARE_SUFFIXES = (".SH", ".SZ", ".BJ")

# 有效 A股概念前缀白名单（700/881/883/884/885/886 = A股行业与概念；861/864/865/871/875 为海外）
A_SHARE_CONCEPT_PREFIXES = ("700", "881", "883", "884", "885", "886")


def is_a_share_code(code: str) -> bool:
    """判断个股代码是否为 A股（沪深北交易所）"""
    return code is not None and code.endswith(A_SHARE_SUFFIXES)


def is_a_share_concept(concept_code: str) -> bool:
    """判断概念代码是否为 A股相关概念（按编码前缀白名单，海外行业指数返回 False）。"""
    return concept_code is not None and concept_code[:3] in A_SHARE_CONCEPT_PREFIXES
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_codes -v
```
Expected: PASS（2 tests）

- [ ] **Step 6: 装为可编辑包并提交**

```bash
$PY -m pip install -e /root/Projects/ifind-sector-hub
cd /root/Projects/ifind-sector-hub && \
  $PY -c "from ifind_sector_hub import is_a_share_concept; print(is_a_share_concept('885001.TI'))" && \
  git add pyproject.toml ifind_sector_hub/ tests/ && \
  git commit -m "feat: 包骨架 + A股代码过滤 codes 模块"
```
Expected: 打印 `True`；提交成功。

---

### Task 2: tokens 模块（TokenStore / FileTokenStore）

**Files:**
- Create: `/root/Projects/ifind-sector-hub/ifind_sector_hub/tokens.py`
- Test: `/root/Projects/ifind-sector-hub/tests/test_tokens.py`

**Interfaces:**
- Consumes: 无（仅 requests + 标准库）
- Produces:
  - `TokenStore(access_token: str = "", refresh_token: str = "")`，属性 `access_token: str`、`refresh_token: str`；方法 `refresh_access_token(stale_token: str) -> str`
  - `FileTokenStore(path: str, access_token: str = "", refresh_token: str = "")`（同上，JSON 落盘 + fcntl 锁）
  - `resolve_tokens(access_token: str, refresh_token: str) -> Tuple[str, str]`（显式 > 环境变量 `IFIND_ACCESS_TOKEN`/`IFIND_REFRESH_TOKEN`）

- [ ] **Step 1: 写失败测试**

`tests/test_tokens.py`：

```python
# -*- coding: utf-8 -*-
import sys, os, json, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest import mock
from ifind_sector_hub import tokens as tk


def _ok_response(new_at, new_rt=None):
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"errorcode": 0, "data": {
        "access_token": new_at, "refresh_token": new_rt, "expired_time": "2099-01-01"}}
    return resp


class TokenStoreTests(unittest.TestCase):
    def test_refresh_returns_new_token_and_persists_rotation(self):
        store = tk.TokenStore(access_token="old-at", refresh_token="rt1")
        with mock.patch.object(tk.requests, "post", return_value=_ok_response("new-at", "rt2")) as p:
            got = store.refresh_access_token("old-at")
        self.assertEqual(got, "new-at")
        self.assertEqual(store.refresh_token, "rt2")  # 轮换被记住
        p.assert_called_once()

    def test_double_check_reuses_other_thread_refresh(self):
        # stale_token 与内存不一致 → 判定别人已刷新，直接复用，不再打接口
        store = tk.TokenStore(access_token="fresh-at", refresh_token="rt1")
        with mock.patch.object(tk.requests, "post") as p:
            got = store.refresh_access_token("stale-at")
        self.assertEqual(got, "fresh-at")
        p.assert_not_called()

    def test_refresh_without_refresh_token_raises(self):
        store = tk.TokenStore(access_token="old-at", refresh_token="")
        with self.assertRaises(RuntimeError):
            store.refresh_access_token("old-at")

    def test_resolve_tokens_env_fallback(self):
        with mock.patch.dict(os.environ, {"IFIND_ACCESS_TOKEN": "e-at", "IFIND_REFRESH_TOKEN": "e-rt"}):
            self.assertEqual(tk.resolve_tokens("", ""), ("e-at", "e-rt"))
        self.assertEqual(tk.resolve_tokens("x", "y"), ("x", "y"))


class FileTokenStoreTests(unittest.TestCase):
    def test_seed_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tok.json")
            tk.FileTokenStore(path, access_token="a1", refresh_token="r1")
            self.assertTrue(os.path.exists(path))  # 首次用显式 token 种子引导
            s2 = tk.FileTokenStore(path)
            self.assertEqual((s2.access_token, s2.refresh_token), ("a1", "r1"))

    def test_refresh_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tok.json")
            store = tk.FileTokenStore(path, access_token="a1", refresh_token="r1")
            with mock.patch.object(tk.requests, "post", return_value=_ok_response("a2", "r1")):
                store.refresh_access_token("a1")
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["access_token"], "a2")
            # 新实例从文件读到刷新后的值
            self.assertEqual(tk.FileTokenStore(path).access_token, "a2")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_tokens -v
```
Expected: ERROR（`cannot import name 'tokens'`）

- [ ] **Step 3: 实现 tokens.py**

```python
# -*- coding: utf-8 -*-
"""Token 生命周期管理：refresh_token 换新 access_token + 可选跨进程持久化。

- TokenStore：进程内存态（默认，等价旧实现：刷新只改本进程，重启重新引导）。
- FileTokenStore：JSON 落盘 + fcntl 文件锁。同机多消费方共用账号时刷新结果共享，
  REFRESH_TOKEN 轮换自动持久化（取代旧实现正则改写 config_local.py）。
"""

import datetime
import fcntl
import json
import os
import threading
from typing import Tuple

import requests

_GET_ACCESS_TOKEN_URL = "https://quantapi.51ifind.com/api/v1/get_access_token"


def _fetch_access_token(refresh_token: str, timeout: int = 30) -> dict:
    """用 refresh_token 换新 access_token，返回响应 data 字段（含轮换后的 refresh_token）。"""
    resp = requests.post(_GET_ACCESS_TOKEN_URL, json={"refresh_token": refresh_token}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errorcode") not in (0, None) and not data.get("data"):
        raise RuntimeError(f"刷新 access_token 失败: {data}")
    return data["data"]


class TokenStore:
    """token 存取与刷新（进程内 threading 锁双检；子类扩展持久化）。"""

    def __init__(self, access_token: str = "", refresh_token: str = ""):
        self._lock = threading.Lock()
        self._access_token = access_token or ""
        self._refresh_token = refresh_token or ""

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    def refresh_access_token(self, stale_token: str) -> str:
        """
        刷新 access_token（client 401 重试专用）。
        :param stale_token: 调用方当前持有、刚被判 401 的 token
        :return: 可用 token；若其他线程已刷新过（双检命中）直接复用，不打接口
        """
        with self._lock:
            fresh = self._load()
            if fresh["access_token"] and fresh["access_token"] != stale_token:
                return fresh["access_token"]
            if not fresh["refresh_token"]:
                raise RuntimeError("REFRESH_TOKEN 未配置，无法刷新 access_token")
            data = _fetch_access_token(fresh["refresh_token"])
            new_token = data["access_token"]
            new_refresh = data.get("refresh_token") or fresh["refresh_token"]
            self._save(new_token, new_refresh)
            print(f"[IFIND-HUB] access_token 已刷新，有效至 {data.get('expired_time')}")
            return new_token

    # 持久化钩子（默认内存态，子类覆盖）
    def _load(self) -> dict:
        return {"access_token": self._access_token, "refresh_token": self._refresh_token}

    def _save(self, access_token: str, refresh_token: str) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token


class FileTokenStore(TokenStore):
    """JSON 文件持久化；写路径加 fcntl 锁做跨进程双检（与本进程锁叠加）。"""

    def __init__(self, path: str, access_token: str = "", refresh_token: str = ""):
        super().__init__(access_token, refresh_token)
        self.path = path
        if not os.path.exists(path) and (access_token or refresh_token):
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            self._write(access_token, refresh_token)
        # 初始化即从文件同步内存（文件存在时以文件为准）
        d = self._read()
        self._access_token, self._refresh_token = d["access_token"], d["refresh_token"]

    def _read(self) -> dict:
        if not os.path.exists(self.path):
            return {"access_token": "", "refresh_token": ""}
        with open(self.path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {"access_token": d.get("access_token", ""), "refresh_token": d.get("refresh_token", "")}

    def _write(self, access_token: str, refresh_token: str) -> None:
        payload = {"access_token": access_token, "refresh_token": refresh_token,
                   "updated_at": datetime.datetime.now().isoformat(timespec="seconds")}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _load(self) -> dict:
        d = self._read()
        self._access_token, self._refresh_token = d["access_token"], d["refresh_token"]
        return d

    def _save(self, access_token: str, refresh_token: str) -> None:
        # 锁内再读一次：其他进程刚刷新过则以文件为准（access_token 均有效，无需回写）
        lock_path = self.path + ".lock"
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                cur = self._read()
                if cur["access_token"] and cur["access_token"] != access_token:
                    return
                self._write(access_token, refresh_token)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)


def resolve_tokens(access_token: str, refresh_token: str) -> Tuple[str, str]:
    """token 解析顺序：显式传入 > 环境变量 IFIND_ACCESS_TOKEN / IFIND_REFRESH_TOKEN。"""
    at = access_token or os.environ.get("IFIND_ACCESS_TOKEN", "")
    rt = refresh_token or os.environ.get("IFIND_REFRESH_TOKEN", "")
    return at, rt
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_tokens -v
```
Expected: PASS（6 tests）

- [ ] **Step 5: 提交**

```bash
cd /root/Projects/ifind-sector-hub && git add ifind_sector_hub/tokens.py tests/test_tokens.py && \
  git commit -m "feat: TokenStore/FileTokenStore——token 刷新双检锁 + fcntl 跨进程持久化"
```

---

### Task 3: client 模块（IfindClient 迁移 + 配置注入化）

**Files:**
- Create: `/root/Projects/ifind-sector-hub/ifind_sector_hub/client.py`
- Test: `/root/Projects/ifind-sector-hub/tests/test_client.py`
- 参照源（迁移后不修改）：monitor `ifind_client.py`（501 行，本任务后 monitor 侧该文件仍在，Task 9 才删除）

**Interfaces:**
- Consumes: `TokenStore`（Task 2）
- Produces:
  - `IfindClient(tokens: TokenStore, base_url_quant: str = "https://quantapi.51ifind.com/api/v1", base_url_ft: str = "https://ft.10jqka.com.cn/api/v1", timeout: int = 30, max_retries: int = 3, batch_size: int = 100)`
  - 方法签名与 monitor `ifind_client.py` **完全一致**：`_post`、`get_stock_concepts`、`get_concept_members`、`get_history_quotation`、`get_high_frequency`、`get_realtime_quotation`、`batch_get_realtime_quotation`、`get_concept_basic_info`、`smart_pick_boards`、`get_all_ths_boards`、`search_board_by_name`、`smart_pick_stocks`、`batch_get_stock_concepts`、`batch_get_concept_basic_info`；类属性 `BOARD_CATEGORY_QUERIES` 原样保留
  - 属性 `headers: dict`（client 自持，取代 config.HEADERS 引用）

- [ ] **Step 1: 写失败测试**

`tests/test_client.py`：

```python
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest import mock
from ifind_sector_hub.tokens import TokenStore
from ifind_sector_hub.client import IfindClient


def _resp(status=200, json_data=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = json_data if json_data is not None else {"errorcode": 0}
    r.raise_for_status.return_value = None
    return r


def make_client():
    return IfindClient(TokenStore(access_token="at1", refresh_token="rt1"))


class PostTests(unittest.TestCase):
    def test_post_ok(self):
        c = make_client()
        with mock.patch("ifind_sector_hub.client.requests.post", return_value=_resp()) as p:
            out = c._post("http://x/api", {"a": 1})
        self.assertEqual(out, {"errorcode": 0})
        p.assert_called_once()
        self.assertEqual(p.call_args.kwargs["headers"]["access_token"], "at1")

    def test_401_refresh_then_retry(self):
        c = make_client()
        # 第1次 401；刷新返回 new-at；第2次成功
        with mock.patch("ifind_sector_hub.client.requests.post",
                        side_effect=[_resp(status=401), _resp()]) as p, \
             mock.patch.object(c.tokens, "refresh_access_token", return_value="new-at") as rf:
            out = c._post("http://x/api", {})
        self.assertEqual(out, {"errorcode": 0})
        self.assertEqual(p.call_count, 2)
        rf.assert_called_once_with("at1")                      # 双检参数 = 旧 token
        self.assertEqual(p.call_args.kwargs["headers"]["access_token"], "new-at")  # 重试带新 token
        self.assertEqual(c.headers["access_token"], "new-at")  # client 自持 headers 已更新

    def test_retry_backoff_on_request_exception(self):
        import requests as real_requests
        c = make_client()
        exc = real_requests.exceptions.RequestException("boom")
        with mock.patch("ifind_sector_hub.client.requests.post", side_effect=[exc, exc, _resp()]) as p, \
             mock.patch("ifind_sector_hub.client.time.sleep") as sl:
            out = c._post("http://x/api", {})
        self.assertEqual(out, {"errorcode": 0})
        self.assertEqual(p.call_count, 3)          # max_retries=3
        self.assertEqual(sl.call_count, 2)
        sl.assert_any_call(1)                       # 2**1
        sl.assert_any_call(2)                       # 2**2

    def test_retry_exhausted_raises(self):
        import requests as real_requests
        c = make_client()
        exc = real_requests.exceptions.RequestException("boom")
        with mock.patch("ifind_sector_hub.client.requests.post", side_effect=exc), \
             mock.patch("ifind_sector_hub.client.time.sleep"):
            with self.assertRaises(real_requests.exceptions.RequestException):
                c._post("http://x/api", {})


class BatchTests(unittest.TestCase):
    def test_batch_get_realtime_quotation_splits_batches(self):
        c = make_client()
        codes = [f"8850{i:02d}.TI" for i in range(250)]  # 3 批（100/100/50）
        def fake_rt(batch, indicators=""):
            code = batch[0]
            return {"errorcode": 0, "tables": [
                {"thscode": cc, "table": {"changeRatio": [1.0]}} for cc in batch]}
        with mock.patch.object(c, "get_realtime_quotation", side_effect=fake_rt):
            out = c.batch_get_realtime_quotation(codes, batch_size=100)
        self.assertEqual(len(out), 250)
        self.assertEqual(out["885000.TI"]["changeRatio"], 1.0)

    def test_batch_get_stock_concepts_parses_comma_separated(self):
        c = make_client()
        resp = {"errorcode": 0, "tables": [{
            "thscode": "600519.SH",
            "table": {
                "ths_the_ths_concept_index_stock": ["白酒,高端白酒"],
                "ths_the_ths_concept_index_code_stock": ["885555.TI,886666.TI"],
            }}]}
        with mock.patch.object(c, "get_stock_concepts", return_value=resp):
            out = c.batch_get_stock_concepts(["600519.SH"], "2026-09-17")
        self.assertEqual(out["600519.SH"], [
            {"concept_name": "白酒", "concept_code": "885555.TI"},
            {"concept_name": "高端白酒", "concept_code": "886666.TI"}])

    def test_batch_size_default_from_client_config(self):
        c = make_client()  # batch_size=100
        resp = {"errorcode": 0, "tables": []}
        codes = [f"60000{i}.SH" for i in range(150)]
        with mock.patch.object(c, "get_stock_concepts", return_value=resp) as g:
            c.batch_get_stock_concepts(codes, "2026-09-17")
        self.assertEqual(g.call_count, 2)  # 100 + 50


class SmartPickTests(unittest.TestCase):
    def test_smart_pick_boards_parses_table(self):
        c = make_client()
        resp = {"errorcode": 0, "tables": [{"table": {
            "指数代码": ["885001.TI", "885002.TI"],
            "指数简称": ["人工智能", "机器人"],
            "同花顺概念级别": ["概念", "概念"],
            "涨跌幅:xxx": [1.234, None],
        }}]}
        with mock.patch.object(c, "_post", return_value=resp):
            boards = c.smart_pick_boards("同花顺概念指数")
        self.assertEqual(boards[0], {"concept_code": "885001.TI", "concept_name": "人工智能",
                                     "category": "概念", "change_ratio": 1.23})
        self.assertIsNone(boards[1]["change_ratio"])

    def test_get_all_ths_boards_dedup_and_level(self):
        c = make_client()
        seen_queries = []
        def fake_sp(q):
            seen_queries.append(q)
            if q == "同花顺概念指数":
                return [{"concept_code": "885001.TI", "concept_name": "A", "category": "c", "change_ratio": None},
                        {"concept_code": "884001.TI", "concept_name": "B", "category": "c", "change_ratio": None}]
            return [{"concept_code": "884001.TI", "concept_name": "B2", "category": "c", "change_ratio": None}]
        with mock.patch.object(c, "smart_pick_boards", side_effect=fake_sp):
            boards = c.get_all_ths_boards()
        self.assertEqual(len(seen_queries), 3)
        self.assertEqual(len(boards), 2)  # 884001 去重
        self.assertEqual(boards[-1]["level"], "industry_l3")

    def test_smart_pick_stocks_parses(self):
        c = make_client()
        resp = {"errorcode": 0, "tables": [{"table": {
            "股票代码": ["600519.SH"], "股票简称": ["贵州茅台"],
            "所属概念": ["白酒"], "涨跌幅:前复权[20260917]": [2.0]}}]}
        with mock.patch.object(c, "_post", return_value=resp):
            rows = c.smart_pick_stocks("白酒概念")
        self.assertEqual(rows, [{"stock_code": "600519.SH", "stock_name": "贵州茅台",
                                 "concepts": "白酒", "change_ratio": 2.0}])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_client -v
```
Expected: ERROR（`cannot import name 'client'`）

- [ ] **Step 3: 实现 client.py**

从 monitor `ifind_client.py` **逐文件复制**到 `/root/Projects/ifind-sector-hub/ifind_sector_hub/client.py`，然后做且仅做以下修改：

1. 头部 import 改为：

```python
# -*- coding: utf-8 -*-
"""
iFinD API 客户端封装（ifind-sector-hub 数据源层）。
支持 5 个核心接口 + smart_stock_picking 特色接口；token 生命周期交给 TokenStore。
"""

import time
import requests
from typing import List, Dict, Optional, Any

from .tokens import TokenStore
```

（删除 `import os`、`import config`、`_GET_ACCESS_TOKEN_URL` 常量、`_refresh_lock`、`refresh_access_token()`、`_persist_refresh_token()` 整两个模块级函数——刷新职责已移入 Task 2 的 tokens.py。）

2. 类与构造函数替换（原 94-102 行）：

```python
class IFindClient:
    """iFinD API 客户端（配置注入，token 由 TokenStore 管理，headers 自持）。"""

    def __init__(self, tokens: TokenStore,
                 base_url_quant: str = "https://quantapi.51ifind.com/api/v1",
                 base_url_ft: str = "https://ft.10jqka.com.cn/api/v1",
                 timeout: int = 30,
                 max_retries: int = 3,
                 batch_size: int = 100):
        self.tokens = tokens
        self.base_url_quant = base_url_quant
        self.base_url_ft = base_url_ft
        self.timeout = timeout
        self.max_retries = max_retries
        self.batch_size = batch_size
        self.headers = {"Content-Type": "application/json", "access_token": tokens.access_token}
```

3. `_post` 替换（原 104-129 行，重试/退避/仅刷新一次语义不变）：

```python
    def _post(self, url: str, payload: dict) -> dict:
        """带重试的 POST。HTTP 401 时经 TokenStore 刷新（双检锁）后重试本轮（仅一次，防死循环）。"""
        for attempt in range(self.max_retries):
            try:
                resp = requests.post(url, headers=self.headers, json=payload, timeout=self.timeout)
                if resp.status_code == 401:
                    self.headers["access_token"] = self.tokens.refresh_access_token(
                        self.headers["access_token"])
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        return {}
```

4. 其余方法体（`get_stock_concepts` 132-154 … `batch_get_concept_basic_info` 463-501）**逐字复制，仅改两处引用**：
   - `batch_get_stock_concepts` 内 `batch_size = batch_size or config.BATCH_SIZE` → `batch_size = batch_size or self.batch_size`
   - 全文不得再出现 `config.`（grep 验证）
5. `BOARD_CATEGORY_QUERIES` 类属性（316-320 行）原样保留在 `smart_pick_boards` 定义之前。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_client -v && \
  ! grep -n "config\." ifind_sector_hub/client.py && echo "无 config 残留"
```
Expected: PASS（10 tests）；grep 无输出。

- [ ] **Step 5: 提交**

```bash
cd /root/Projects/ifind-sector-hub && git add ifind_sector_hub/client.py tests/test_client.py && \
  git commit -m "feat: IfindClient 迁移——配置注入化 + token 委托 TokenStore + headers 自持"
```

---

### Task 4: storage 模块（三表存储）

**Files:**
- Create: `/root/Projects/ifind-sector-hub/ifind_sector_hub/storage.py`
- Test: `/root/Projects/ifind-sector-hub/tests/test_storage.py`

**Interfaces:**
- Consumes: `codes.is_a_share_concept`（仅 `get_stock_concepts_from_members` 之外的 A股过滤；注意该方法在 monitor 侧用 `is_in_sector_pool` 过滤，**留在 monitor 不迁**——见 Step 3 第 6 条）
- Produces: `SectorStore(db_path: str, busy_timeout_ms: int = 5000)`，方法（签名与返回结构逐字对应 monitor `database.py` 同名方法）：
  - 写：`save_concept_dict(concepts, update_date=None)`、`save_stock_concept_map(mappings, map_date)`、`save_concept_members(concept_code, members, member_date)`
  - 读：`get_all_concept_codes()`、`get_all_member_stock_codes()`、`get_all_mapped_stock_codes()`、`get_concept_name(concept_code)`、`get_stock_concepts(stock_code, map_date=None)`、`get_concept_members(concept_code, member_date=None)`、`get_concept_members_map(concept_codes)`（单连接批量优化保留）
  - 替换：`replace_concept_dict(boards, migrate_hook=None) -> Dict`（原 `refresh_concept_dict_replace`，watched 迁移抽为钩子，见下）
  - **新增访问器**（收编裸 SQL）：`get_concept_names() -> Dict[str, str]`、`get_latest_member_date() -> str`、`get_latest_member_stock_names() -> Dict[str, str]`（最新快照 code→name，首个出现优先）、`get_all_member_stock_names() -> Dict[str, str]`（全历史 `MAX(stock_name) GROUP BY stock_code`）、`get_latest_members_snapshot() -> Tuple[str, Dict[str, List[Tuple[str, str]]]]`（最新快照日期 + 全部概念成分 `{cc: [(stock_code, stock_name), ...]}`）
  - **不迁**：`get_a_share_concept_codes` / `get_observe_concept_codes` / watched 三方法 / `get_concept_stocks`（monitor 私有或废弃）
  - `migrate_hook` 契约：`migrate_hook(conn, removed_codes: set, old_name_map: dict, new_name_map: dict) -> (migrated, dropped_watched)`——在**同一事务、同一连接**内被调用，负责迁移/清理消费方自己的关联表（monitor 的 watched_concepts），返回值原样进入结果 dict

- [ ] **Step 1: 写失败测试**

`tests/test_storage.py`：

```python
# -*- coding: utf-8 -*-
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from ifind_sector_hub.storage import SectorStore

BOARDS = [
    {"concept_code": "885101.TI", "concept_name": "人工智能", "category": "概念"},
    {"concept_code": "885102.TI", "concept_name": "机器人", "category": "概念"},
]


class StorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SectorStore(os.path.join(self._tmp.name, "t.db"))

    def tearDown(self):
        self._tmp.cleanup()

    # ---- 快照语义（行为等价红线 #2）----
    def test_members_latest_snapshot_semantics(self):
        self.store.save_concept_members("885101.TI", [
            {"stock_code": "600519.SH", "stock_name": "贵州茅台"}], "20260901")
        self.store.save_concept_members("885101.TI", [
            {"stock_code": "600519.SH", "stock_name": "贵州茅台"},
            {"stock_code": "000001.SZ", "stock_name": "平安银行"}], "20260910")
        # 不传日期 → MAX(member_date) 快照（且历史不清理）
        got = self.store.get_concept_members("885101.TI")
        self.assertEqual(len(got), 2)
        got = self.store.get_concept_members("885101.TI", "20260901")
        self.assertEqual(len(got), 1)
        mmap = self.store.get_concept_members_map(["885101.TI"])
        self.assertEqual(len(mmap["885101.TI"]), 2)

    def test_stock_concepts_map_semantics(self):
        self.store.save_concept_dict([{"concept_code": "885101.TI", "short_name": "人工智能"}])
        self.store.save_stock_concept_map(
            {"600519.SH": [{"concept_name": "人工智能", "concept_code": "885101.TI"}]}, "2026-09-01")
        rows = self.store.get_stock_concepts("600519.SH")
        self.assertEqual(rows[0]["concept_code"], "885101.TI")
        self.assertEqual(rows[0]["weight"], 1.0)

    # ---- replace + 级联 + 迁移钩子（红线 #3：同事务）----
    def test_replace_cascade_and_migrate_hook(self):
        # 旧字典含 885101；替换后字典只有 885999 → 885101 成为 removed，级联清成分股
        self.store.save_concept_dict([{"concept_code": "885101.TI", "short_name": "人工智能"}])
        self.store.save_concept_members("885101.TI", [
            {"stock_code": "600519.SH", "stock_name": "x"}], "20260901")
        new_boards = [
            {"concept_code": "885999.TI", "concept_name": "人工智能", "category": "概念"},
        ]

        def hook(conn, removed_codes, old_name_map, new_name_map):
            # 模拟 monitor 的 watched 迁移：名称匹配新码后 UPDATE 关联表
            self.assertEqual(removed_codes, {"885101.TI"})
            self.assertEqual(old_name_map.get("885101.TI"), "人工智能")
            conn.execute("CREATE TABLE IF NOT EXISTS watched_concepts "
                         "(concept_code TEXT PRIMARY KEY, added_at TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO watched_concepts VALUES ('885101.TI', 'now')")
            conn.execute("UPDATE watched_concepts SET concept_code='885999.TI' "
                         "WHERE concept_code='885101.TI'")
            return [("885101.TI", "885999.TI", "人工智能")], []

        result = self.store.replace_concept_dict(new_boards, migrate_hook=hook)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["migrated"], [("885101.TI", "885999.TI", "人工智能")])
        # 字典已替换
        self.assertEqual(self.store.get_all_concept_codes(), ["885999.TI"])
        # 级联清理：被移除旧码的成分股已删
        self.assertEqual(self.store.get_concept_members("885101.TI"), [])
        self.assertEqual(self.store.get_concept_name("885999.TI"), "人工智能")

    def test_replace_without_hook_keeps_unchanged_members(self):
        # 无钩子；885101 在新旧字典都在（BOARDS 含它）→ 不属 removed，成分股保留
        self.store.save_concept_dict([{"concept_code": "885101.TI", "short_name": "人工智能"}])
        self.store.save_concept_members("885101.TI", [
            {"stock_code": "600519.SH", "stock_name": "x"}], "20260901")
        result = self.store.replace_concept_dict(BOARDS)
        self.assertEqual(result["migrated"], [])
        self.assertEqual(result["dropped_watched"], [])
        self.assertNotEqual(self.store.get_concept_members("885101.TI"), [])

    # ---- 新增访问器 ----
    def test_new_accessors(self):
        self.store.save_concept_dict([{"concept_code": "885101.TI", "short_name": "人工智能"}])
        self.store.save_concept_members("885101.TI", [
            {"stock_code": "600519.SH", "stock_name": "贵州茅台"}], "20260901")
        self.store.save_concept_members("885102.TI", [
            {"stock_code": "000001.SZ", "stock_name": "平安银行"}], "20260905")
        self.assertEqual(self.store.get_concept_names(), {"885101.TI": "人工智能"})
        self.assertEqual(self.store.get_latest_member_date(), "20260905")
        # 最新快照（20260905）只有 000001；600519 只在旧快照 → 不在最新名称映射
        self.assertNotIn("600519.SH", self.store.get_latest_member_stock_names())
        self.assertIn("000001.SZ", self.store.get_latest_member_stock_names())
        # 全历史名称映射两者都有
        self.assertEqual(self.store.get_all_member_stock_names(),
                         {"600519.SH": "贵州茅台", "000001.SZ": "平安银行"})
        snap, mmap = self.store.get_latest_members_snapshot()
        self.assertEqual(snap, "20260905")
        self.assertEqual(mmap, {"885102.TI": [("000001.SZ", "平安银行")]})
```

```python
class AShareFilterTests(unittest.TestCase):
    def test_get_all_member_stock_codes_filters_a_share(self):
        with tempfile.TemporaryDirectory() as d:
            store = SectorStore(os.path.join(d, "t.db"))
            store.save_concept_members("885101.TI", [
                {"stock_code": "600519.SH", "stock_name": "a"},
                {"stock_code": "AAPL.US", "stock_name": "b"}], "20260901")
            self.assertEqual(store.get_all_member_stock_codes(), ["600519.SH"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_storage -v
```
Expected: ERROR（`cannot import name 'storage'`）

- [ ] **Step 3: 实现 storage.py**

骨架与 `_connect`（从 monitor `database.py:26-42` 逐字复制，仅把 `config.DB_BUSY_TIMEOUT_MS` 改为构造参数默认 5000）：

```python
# -*- coding: utf-8 -*-
"""三表存储：ths_concept_dict / stock_concept_map / concept_members。

快照语义（行为等价红线）：表带日期快照列，读方法日期参数缺省取 MAX(date) 最新快照，
历史不清理，与同步日期解耦。schema 与 monitor database.py 原始定义逐字一致（同一库文件兼容）。
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from .codes import is_a_share_concept

# 迁移钩子：同一事务/连接内被调用，返回 (migrated, dropped_watched)
MigrateHook = Callable[[sqlite3.Connection, set, Dict[str, str], Dict[str, str]], tuple]

_SECTOR_DDL = """
CREATE TABLE IF NOT EXISTS ths_concept_dict (
    concept_code  TEXT PRIMARY KEY,
    concept_name  TEXT NOT NULL,
    full_name     TEXT,
    index_code    TEXT,
    main_code     TEXT,
    thscode       TEXT,
    update_date   TEXT
);

CREATE TABLE IF NOT EXISTS stock_concept_map (
    stock_code    TEXT NOT NULL,
    concept_code  TEXT NOT NULL,
    map_date      TEXT NOT NULL,
    weight        REAL DEFAULT 1.0,
    PRIMARY KEY (stock_code, concept_code, map_date)
);
CREATE INDEX IF NOT EXISTS idx_scm_concept ON stock_concept_map(concept_code);

CREATE TABLE IF NOT EXISTS concept_members (
    concept_code  TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    stock_name    TEXT,
    member_date   TEXT NOT NULL,
    PRIMARY KEY (concept_code, stock_code, member_date)
);
CREATE INDEX IF NOT EXISTS idx_cm_concept ON concept_members(concept_code);
"""


class SectorStore:
    """三表读写；可指向任意 SQLite 库文件（monitor 沿用 sector_attribution.db）。"""

    def __init__(self, db_path: str, busy_timeout_ms: int = 5000):
        self.db_path = db_path
        self.busy_timeout_ms = busy_timeout_ms
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._init_tables()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self):
        with self._connect() as conn:
            conn.executescript(_SECTOR_DDL)
```

其余方法从 monitor `database.py` 逐字复制并按下表映射（仅去掉 `self._init_db` 相关与 config 引用，SQL 一字不改）：

| SectorStore 方法 | 复制自 database.py | 修改点 |
|---|---|---|
| `save_concept_dict` | :242-259 | 无 |
| `get_all_concept_codes` | :343-347 | 无 |
| `get_all_member_stock_codes` | :592-604 | 无（A股 LIKE 过滤已在 SQL 内） |
| `get_all_mapped_stock_codes` | :606-617 | 无 |
| `get_concept_name` | :619-627 | 无 |
| `save_stock_concept_map` | :630-645 | 无 |
| `get_stock_concepts` | :647-672 | 无 |
| `save_concept_members` | :722-730 | 无 |
| `get_concept_members` | :732-753 | 无 |
| `get_concept_members_map` | :755-781 | 无 |

**`replace_concept_dict`（重写自 :261-341，watched 迁移抽为钩子，事务原子性不变）：**

```python
    def replace_concept_dict(self, boards: List[Dict[str, str]],
                             migrate_hook: Optional[MigrateHook] = None) -> Dict:
        """
        以枚举结果为准全量替换板块字典 + 级联清理 concept_members（同事务）。
        watched 等消费方关联表的迁移由 migrate_hook 在同一事务内完成（可空）。
        :param boards: [{"concept_code", "concept_name", "category", ...}]
        :return: {added, removed, total, migrated, dropped_watched}
        """
        update_date = datetime.now().strftime("%Y-%m-%d")
        new_codes = {b["concept_code"] for b in boards}
        new_name_map = {b["concept_code"]: b["concept_name"] for b in boards}
        migrated: list = []
        dropped_watched: list = []
        with self._connect() as conn:
            old_name_map = {r["concept_code"]: r["concept_name"]
                            for r in conn.execute("SELECT concept_code, concept_name FROM ths_concept_dict")}
            removed_codes = set(old_name_map) - new_codes
            added_codes = new_codes - set(old_name_map)

            if migrate_hook is not None:
                migrated, dropped_watched = migrate_hook(
                    conn, removed_codes, old_name_map, new_name_map)

            conn.execute("DELETE FROM ths_concept_dict")
            conn.executemany("""
                INSERT INTO ths_concept_dict
                (concept_code, concept_name, full_name, update_date)
                VALUES (?, ?, ?, ?)
            """, [
                (b["concept_code"], b["concept_name"],
                 b.get("category", "") or "", update_date)
                for b in boards
            ])

            dead_codes = sorted(removed_codes)
            if dead_codes:
                ph = ",".join("?" * len(dead_codes))
                conn.execute(f"DELETE FROM concept_members WHERE concept_code IN ({ph})", dead_codes)

        return {
            "added": len(added_codes),
            "removed": len(removed_codes),
            "total": len(new_codes),
            "migrated": migrated,
            "dropped_watched": dropped_watched,
        }
```

**新增访问器（收编裸 SQL 的等价实现）：**

```python
    # ========== 消费方常用只读访问器（原 monitor 各处裸 SQL 的统一入口） ==========
    def get_concept_names(self) -> Dict[str, str]:
        """字典 code → name 全量映射（原 api_server/realtime_engine/sector_manage 裸 SQL）。"""
        with self._connect() as conn:
            return {r["concept_code"]: r["concept_name"]
                    for r in conn.execute("SELECT concept_code, concept_name FROM ths_concept_dict")}

    def get_latest_member_date(self) -> str:
        """成分股表最新快照日期，空表返回空串（原 kg_sources._latest_snapshot_date 裸 SQL）。"""
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(member_date) FROM concept_members").fetchone()
            return row[0] or ""

    def get_latest_member_stock_names(self) -> Dict[str, str]:
        """最新快照股票名映射，首个出现优先（原 realtime_engine/api_server/auction_engine 裸 SQL）。"""
        names: Dict[str, str] = {}
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT stock_code, stock_name FROM concept_members "
                "WHERE member_date = (SELECT MAX(member_date) FROM concept_members)"
            ):
                if row["stock_code"] not in names:
                    names[row["stock_code"]] = row["stock_name"]
        return names

    def get_all_member_stock_names(self) -> Dict[str, str]:
        """全历史股票名映射 MAX(stock_name)（原 theme_catalyst.build_market_historical 裸 SQL）。"""
        with self._connect() as conn:
            return {r[0]: r[1] for r in conn.execute(
                "SELECT stock_code, MAX(stock_name) FROM concept_members GROUP BY stock_code")}

    def get_latest_members_snapshot(self) -> Tuple[str, Dict[str, List[Tuple[str, str]]]]:
        """最新快照全体成分（原 theme_catalyst.theme_members_and_size 裸 SQL）。"""
        result: Dict[str, List[Tuple[str, str]]] = {}
        with self._connect() as conn:
            snap = conn.execute("SELECT MAX(member_date) FROM concept_members").fetchone()[0] or ""
            if not snap:
                return snap, result
            for row in conn.execute(
                "SELECT concept_code, stock_code, stock_name FROM concept_members WHERE member_date = ?",
                (snap,),
            ):
                result.setdefault(row["concept_code"], []).append(
                    (row["stock_code"], row["stock_name"]))
        return snap, result
```

**明确不迁**（留在 monitor 的 `Database`）：`get_a_share_concept_codes`（watched 优先逻辑）、`get_observe_concept_codes`（观察池规则）、`get_watched_concept_codes` / `filter_monitorable_concept_codes` / `save_watched_concepts`、`get_stock_concepts_from_members`（其 `is_in_sector_pool` 过滤是 monitor 板块池语义——该方法整体留 monitor，SQL 中的三表查询仍走 `self._connect`，monitor `Database` 保留自有连接能力）、`get_concept_stocks`（无调用方，Task 7 删除）。

- [ ] **Step 4: 跑测试确认通过（按 Step 1 中两处修正后的断言）**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_storage -v
```
Expected: PASS（6 tests）

- [ ] **Step 5: 提交**

```bash
cd /root/Projects/ifind-sector-hub && git add ifind_sector_hub/storage.py tests/test_storage.py && \
  git commit -m "feat: SectorStore 三表存储——快照语义 + replace级联 + 迁移钩子 + 5个访问器"
```

---

### Task 5: sync 模块（同步编排）

**Files:**
- Create: `/root/Projects/ifind-sector-hub/ifind_sector_hub/sync.py`
- Test: `/root/Projects/ifind-sector-hub/tests/test_sync.py`

**Interfaces:**
- Consumes: `IfindClient`（Task 3）、`SectorStore`（Task 4）、`codes.is_a_share_concept`
- Produces: `SectorSync(client, store, concurrency: int = 8, progress_every: int = 100)`
  - `sync_concept_members(concept_codes: List[str], member_date: str, stock_filter: Optional[Callable[[str], bool]] = None) -> int`（并发内核，返回入库条数；`stock_filter` 可选逐股过滤，如 `is_a_share_code`）
  - `init_concept_dict(concept_codes: List[str]) -> List[Dict]`（内部做 A股概念过滤 + 接口5 + 入库）
  - `sync_stock_concept_map(stock_codes: List[str], map_date: str = None) -> Dict`
  - `init_concept_universe(map_date: str = None, skip: bool = False, batch_size: int = 100, existing_codes: Optional[set] = None) -> int`（`skip`=原板块池开关；`existing_codes`=调用方口径的"已参与"码集，缺省用字典全集）
  - `refresh_dict_and_members(concept_codes: List[str], member_date: str = None) -> Dict`（返回 `{dict_count, member_concepts, saved_records, member_date}`）

- [ ] **Step 1: 写失败测试**

`tests/test_sync.py`：

```python
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest import mock
from ifind_sector_hub.sync import SectorSync
from ifind_sector_hub.client import IfindClient
from ifind_sector_hub.tokens import TokenStore
from ifind_sector_hub.storage import SectorStore
import tempfile


def make_sync(tmpdir):
    client = IfindClient(TokenStore(access_token="at", refresh_token="rt"))
    store = SectorStore(os.path.join(tmpdir, "s.db"))
    return SectorSync(client, store), client, store


class SyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sync, self.client, self.store = make_sync(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_members_resp(self, codes):
        """接口2 假响应：每个概念 2 只成分股；指定一个概念抛异常模拟失败。"""
        def resp_of(cc, date):
            if cc == "885BAD.TI":
                raise RuntimeError("api error")
            return {"tables": [{"table": {
                "p03473_f002": ["600519.SH", "000001.SZ"],
                "p03473_f003": ["贵州茅台", "平安银行"]}}]}
        return resp_of

    def test_sync_concept_members_failure_does_not_interrupt(self):
        codes = ["885001.TI", "885BAD.TI", "885002.TI"]
        with mock.patch.object(self.client, "get_concept_members",
                               side_effect=self._fake_members_resp(codes)):
            saved = self.sync.sync_concept_members(codes, "20260917")
        self.assertEqual(saved, 4)  # 2 个成功概念 × 2 股；失败概念不中断
        self.assertEqual(len(self.store.get_concept_members("885001.TI")), 2)
        self.assertEqual(self.store.get_concept_members("885BAD.TI"), [])

    def test_sync_concept_members_stock_filter(self):
        with mock.patch.object(self.client, "get_concept_members",
                               side_effect=lambda cc, d: {"tables": [{"table": {
                                   "p03473_f002": ["600519.SH", "AAPL.US"],
                                   "p03473_f003": ["a", "b"]}}]}):
            saved = self.sync.sync_concept_members(
                ["885001.TI"], "20260917",
                stock_filter=lambda sc: sc.endswith((".SH", ".SZ", ".BJ")))
        self.assertEqual(saved, 1)

    def test_init_concept_dict_filters_overseas(self):
        fake = [{"concept_code": c, "short_name": c[:6], "full_name": "", "index_code": "",
                 "main_code": "", "thscode": ""} for c in ["885001.TI", "861001.TI"]]
        with mock.patch.object(self.client, "batch_get_concept_basic_info", return_value=fake):
            got = self.sync.init_concept_dict(["885001.TI", "861001.TI"])
        # 海外码 861 不入字典
        self.assertEqual(self.store.get_all_concept_codes(), ["885001.TI"])
        self.assertEqual(got, fake)  # 返回接口原始结果（与旧行为一致）

    def test_init_concept_universe_collects_and_backfills(self):
        # 全市场 2 只股票；接口1 返回一个新概念 885777 + 一个海外概念 861777
        self.store.save_concept_dict([{"concept_code": "884001.TI", "short_name": "旧"}])
        self.store.save_concept_members("884001.TI", [
            {"stock_code": "600519.SH", "stock_name": "a"},
            {"stock_code": "000001.SZ", "stock_name": "b"}], "20260901")
        iface1 = {"600519.SH": [{"concept_name": "新概念", "concept_code": "885777.TI"},
                                {"concept_name": "海外", "concept_code": "861777.TI"}],
                  "000001.SZ": []}
        dict_info = [{"concept_code": "885777.TI", "short_name": "新概念"}]
        members_resp = {"tables": [{"table": {
            "p03473_f002": ["600519.SH"], "p03473_f003": ["a"]}}]}
        with mock.patch.object(self.client, "batch_get_stock_concepts", return_value=iface1), \
             mock.patch.object(self.client, "batch_get_concept_basic_info", return_value=dict_info), \
             mock.patch.object(self.client, "get_concept_members", return_value=members_resp):
            added = self.sync.init_concept_universe("2026-09-17")
        self.assertEqual(added, 1)  # 仅 885777 新增
        self.assertIn("885777.TI", self.store.get_all_concept_codes())
        self.assertNotIn("861777.TI", self.store.get_all_concept_codes())
        # 全市场映射已入库（海外概念被过滤）
        rows = self.store.get_stock_concepts("600519.SH")
        self.assertEqual([r["concept_code"] for r in rows], ["885777.TI"])

    def test_init_concept_universe_skip(self):
        with mock.patch.object(self.client, "batch_get_stock_concepts") as b:
            self.assertEqual(self.sync.init_concept_universe(skip=True), 0)
            b.assert_not_called()

    def test_refresh_dict_and_members_shape(self):
        fake_dict = [{"concept_code": "885001.TI", "short_name": "x"}]
        members_resp = {"tables": [{"table": {
            "p03473_f002": ["600519.SH"], "p03473_f003": ["a"]}}]}
        with mock.patch.object(self.client, "batch_get_concept_basic_info", return_value=fake_dict), \
             mock.patch.object(self.client, "get_concept_members", return_value=members_resp):
            out = self.sync.refresh_dict_and_members(["885001.TI"], "20260917")
        self.assertEqual(out, {"dict_count": 1, "member_concepts": 1,
                               "saved_records": 1, "member_date": "20260917"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_sync -v
```
Expected: ERROR（`cannot import name 'sync'`）

- [ ] **Step 3: 实现 sync.py**

从 monitor `sync_pipeline.py` 迁移改编（打印前缀统一保留原样以便日志识别）：

```python
# -*- coding: utf-8 -*-
"""板块数据同步编排：字典 / 成分股 / 个股-概念映射。

并发内核与日志行为自 monitor sync_pipeline.py 原样迁移（线程池逐概念拉接口2，
失败概念不中断，进度计数带锁）。
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Dict, List, Optional

from .codes import is_a_share_code, is_a_share_concept


class SectorSync:
    def __init__(self, client, store,
                 concurrency: int = 8, progress_every: int = 100):
        self.client = client
        self.store = store
        self.concurrency = concurrency
        self.progress_every = progress_every

    # ---------- 单概念成分股（线程池 worker） ----------
    def _fetch_one_concept_members(self, concept_code: str, member_date: str,
                                   stock_filter: Optional[Callable[[str], bool]] = None):
        """
        拉取单个概念的成分股。
        :return: (concept_code, members) 成功；(concept_code, None) 失败
        """
        try:
            resp = self.client.get_concept_members(concept_code, member_date)
            if "tables" in resp and len(resp["tables"]) > 0:
                table = resp["tables"][0].get("table", {})
                stock_codes = table.get("p03473_f002", [])
                stock_names = table.get("p03473_f003", [])
                members = []
                for i in range(len(stock_codes)):
                    sc = stock_codes[i]
                    if stock_filter is not None and not stock_filter(sc):
                        continue
                    members.append({
                        "stock_code": sc,
                        "stock_name": stock_names[i] if i < len(stock_names) else "",
                    })
                return (concept_code, members)
            return (concept_code, [])  # 无成分股数据视为空成功
        except Exception as e:
            print(f"[WARN] 获取 {concept_code} 成分股失败: {e}")
            return (concept_code, None)

    # ---------- 并发内核（原 _fetch_concept_members_batch） ----------
    def sync_concept_members(self, concept_codes: List[str], member_date: str,
                             stock_filter: Optional[Callable[[str], bool]] = None) -> int:
        """并发拉取给定概念列表的成分股并入库，返回入库条数（失败概念不中断）。"""
        total = len(concept_codes)
        done_count = saved_records = success_count = 0
        counter_lock = threading.Lock()
        failed_codes = []

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            future_to_code = {
                executor.submit(self._fetch_one_concept_members, cc, member_date, stock_filter): cc
                for cc in concept_codes
            }
            for future in as_completed(future_to_code):
                code, members = future.result()
                if members is None:
                    failed_codes.append(code)
                else:
                    if members:
                        self.store.save_concept_members(code, members, member_date)
                    success_count += 1
                    saved_records += len(members)
                with counter_lock:
                    done_count += 1
                    if done_count % self.progress_every == 0 or done_count == total:
                        print(f"[UNIVERSE] 成分股进度 {done_count}/{total}"
                              f"（成功 {success_count}，失败 {len(failed_codes)}，已入库 {saved_records} 条）")

        print(f"[UNIVERSE] 已保存共 {saved_records} 条成分股记录"
              f"（{total} 个概念中成功 {success_count} 个，失败 {len(failed_codes)} 个）")
        if failed_codes:
            preview = ", ".join(failed_codes[:20])
            more = "" if len(failed_codes) <= 20 else f" ...（共 {len(failed_codes)} 个）"
            print(f"[UNIVERSE] 失败概念码: {preview}{more}")
        return saved_records

    # ---------- 字典 ----------
    def init_concept_dict(self, concept_codes: List[str]) -> List[Dict]:
        """初始化/刷新板块字典（接口5，永久缓存；内部过滤海外概念）。"""
        print("[INIT] 开始初始化概念板块字典...")
        a_share_codes = [c for c in concept_codes if is_a_share_concept(c)]
        skipped = len(concept_codes) - len(a_share_codes)
        if skipped:
            print(f"[INIT] 过滤 {skipped} 个海外行业指数概念，仅保留 A股概念 {len(a_share_codes)} 个")
        concepts = self.client.batch_get_concept_basic_info(a_share_codes, batch_size=100)
        self.store.save_concept_dict(concepts)
        print(f"[INIT] 已保存 {len(concepts)} 个概念板块到字典")
        return concepts

    # ---------- 个股-概念映射 ----------
    def sync_stock_concept_map(self, stock_codes: List[str], map_date: str = None) -> Dict:
        map_date = map_date or datetime.now().strftime("%Y-%m-%d")
        print(f"[INIT] 开始初始化个股-概念映射，日期={map_date}...")
        mappings = self.client.batch_get_stock_concepts(stock_codes, map_date)
        self.store.save_stock_concept_map(mappings, map_date)
        print(f"[INIT] 已保存 {len(mappings)} 只个股的概念映射")
        return mappings

    # ---------- 概念全集补全（原 init_concept_universe） ----------
    def init_concept_universe(self, map_date: str = None, skip: bool = False,
                              batch_size: int = 100,
                              existing_codes: Optional[set] = None) -> int:
        """
        扫全市场股票收集实际在用概念码（885/886 等），补全字典与成分股。
        :param skip: 调用方要求跳过（原"板块池启用仅 884"开关）
        :param existing_codes: 调用方口径的"已参与"码集；缺省用字典全集
        :return: 新增概念码数量
        """
        if skip:
            print("[UNIVERSE] 板块池已启用（仅 884），跳过 885/886 概念扫描")
            return 0
        print("=" * 60)
        print("  补全概念板块全集（扫描全市场股票）")
        print("=" * 60)
        map_date = map_date or datetime.now().strftime("%Y-%m-%d")

        all_stocks = self.store.get_all_member_stock_codes()
        print(f"[UNIVERSE] 全市场股票 {len(all_stocks)} 只")

        collected: Dict[str, str] = {}
        all_mappings: Dict[str, list] = {}
        for i in range(0, len(all_stocks), batch_size):
            batch = all_stocks[i:i + batch_size]
            mappings = self.client.batch_get_stock_concepts(batch, map_date)
            for stock_code, concepts in mappings.items():
                a_concepts = [c for c in concepts if is_a_share_concept(c.get("concept_code", ""))]
                if a_concepts:
                    all_mappings[stock_code] = a_concepts
                    for c in a_concepts:
                        cc = c.get("concept_code")
                        if cc and cc not in collected:
                            collected[cc] = c.get("concept_name", "")
            if (i // batch_size) % 5 == 0:
                print(f"[UNIVERSE] 扫描进度 {min(i + batch_size, len(all_stocks))}/{len(all_stocks)}"
                      f"，已收集 A股概念码 {len(collected)}，映射 {len(all_mappings)} 只")
        print(f"[UNIVERSE] 扫描完成，共收集 A股概念码 {len(collected)} 个，映射 {len(all_mappings)} 只股票")

        self.store.save_stock_concept_map(all_mappings, map_date)
        print(f"[UNIVERSE] 已更新 stock_concept_map：{len(all_mappings)} 只股票")

        if existing_codes is None:
            existing_codes = set(self.store.get_all_concept_codes())
        new_codes = [cc for cc in collected
                     if cc not in existing_codes and is_a_share_concept(cc)]
        print(f"[UNIVERSE] 其中字典里已有的: {len(collected) - len(new_codes)}，需新增: {len(new_codes)}")
        if not new_codes:
            print("[UNIVERSE] 无需补充，概念字典已覆盖")
            return 0

        print(f"[UNIVERSE] 调用接口5 补全 {len(new_codes)} 个概念的字典信息...")
        concepts_info = self.client.batch_get_concept_basic_info(new_codes, batch_size=100)
        self.store.save_concept_dict(concepts_info)
        print(f"[UNIVERSE] 字典已补全 {len(concepts_info)} 个概念")

        print(f"[UNIVERSE] 调用接口2 补全 {len(new_codes)} 个概念的成分股...")
        today_compact = datetime.now().strftime("%Y%m%d")
        self.sync_concept_members(new_codes, today_compact, stock_filter=is_a_share_code)

        print("=" * 60)
        print("  概念板块全集补全完成")
        print("=" * 60)
        return len(new_codes)

    # ---------- 观察池刷新（原 refresh_observe_members 的数据侧） ----------
    def refresh_dict_and_members(self, concept_codes: List[str], member_date: str = None) -> Dict:
        """对给定码集刷字典（接口5）+ 成分股（接口2 并发），返回统计。"""
        member_date = member_date or datetime.now().strftime("%Y%m%d")
        print(f"[REFRESH] 开始刷新观察池板块信息，日期={member_date}...")
        a_share_codes = [c for c in concept_codes if is_a_share_concept(c)]
        concepts = self.client.batch_get_concept_basic_info(a_share_codes, batch_size=100)
        self.store.save_concept_dict(concepts)
        print(f"[REFRESH] 字典已刷新 {len(concepts)} 个概念")
        saved = self.sync_concept_members(concept_codes, member_date, stock_filter=is_a_share_code)
        print(f"[REFRESH] 完成：{len(concept_codes)} 个概念，{saved} 条成分股记录")
        return {"dict_count": len(concepts), "member_concepts": len(concept_codes),
                "saved_records": saved, "member_date": member_date}
```

> 与旧实现的语义差异说明（均可接受、写入迁移注释）：旧 `refresh_observe_members` 刷成分股**不过滤**海外个股（`_fetch_one_concept_members` 无过滤）；新版统一带 `stock_filter=is_a_share_code`，与 `cmd_refresh_boards` 旧行为一致，数据只会更干净。旧版返回键多一个 `failed_concepts`——monitor 侧组装时不再提供（api_server 的 `_refresh_state["result"]` 仅透传展示，无消费依赖）。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_sync -v
```
Expected: PASS（6 tests）

- [ ] **Step 5: 提交**

```bash
cd /root/Projects/ifind-sector-hub && git add ifind_sector_hub/sync.py tests/test_sync.py && \
  git commit -m "feat: SectorSync 同步编排——字典/成分股并发/映射/全集补全/观察池刷新"
```

---

### Task 6: 门面 __init__ + 可选 service 层（包完成）

**Files:**
- Modify: `/root/Projects/ifind-sector-hub/ifind_sector_hub/__init__.py`（重写为门面）
- Create: `/root/Projects/ifind-sector-hub/ifind_sector_hub/service.py`
- Create: `/root/Projects/ifind-sector-hub/README.md`
- Test: `/root/Projects/ifind-sector-hub/tests/test_service.py`

**Interfaces:**
- Consumes: Tasks 2-5 全部产出
- Produces:
  - `HubConfig(db_path: str, access_token: str = "", refresh_token: str = "", token_store: Optional[TokenStore] = None, base_url_quant=..., base_url_ft=..., timeout: int = 30, max_retries: int = 3, batch_size: int = 100, members_concurrency: int = 8, members_progress_every: int = 100)`（dataclass）
  - `SectorHub(cfg: HubConfig)`：属性 `client: IfindClient`、`store: SectorStore`、`sync: SectorSync`
  - `service.build_router(hub: SectorHub) -> fastapi.APIRouter`

- [ ] **Step 1: 重写 __init__.py**

```python
# -*- coding: utf-8 -*-
"""ifind-sector-hub：ifind 板块/概念数据层公共组件。

用法：
    from ifind_sector_hub import SectorHub, HubConfig, FileTokenStore
    hub = SectorHub(HubConfig(db_path="data/sector_attribution.db",
                              access_token=..., refresh_token=...,
                              token_store=FileTokenStore("data/token.json")))
    hub.client.smart_pick_stocks("...")   # 数据源
    hub.store.get_concept_members_map([]) # 三表快照读
    hub.sync.sync_concept_members([], "20260917")
"""

from dataclasses import dataclass
from typing import Optional

from .codes import A_SHARE_CONCEPT_PREFIXES, A_SHARE_SUFFIXES, is_a_share_code, is_a_share_concept
from .tokens import FileTokenStore, TokenStore, resolve_tokens
from .client import IFindClient
from .storage import SectorStore
from .sync import SectorSync

__all__ = [
    "HubConfig", "SectorHub", "FileTokenStore", "TokenStore", "resolve_tokens",
    "IFindClient", "SectorStore", "SectorSync",
    "is_a_share_code", "is_a_share_concept", "A_SHARE_SUFFIXES", "A_SHARE_CONCEPT_PREFIXES",
]


@dataclass
class HubConfig:
    db_path: str
    # token：显式传入优先，未传走环境变量（token_store 提供时以其为准）
    access_token: str = ""
    refresh_token: str = ""
    token_store: Optional[TokenStore] = None
    base_url_quant: str = "https://quantapi.51ifind.com/api/v1"
    base_url_ft: str = "https://ft.10jqka.com.cn/api/v1"
    timeout: int = 30
    max_retries: int = 3
    batch_size: int = 100
    members_concurrency: int = 8
    members_progress_every: int = 100


class SectorHub:
    """门面：聚合 client / store / sync，共享同一 token 管理与库文件。"""

    def __init__(self, cfg: HubConfig):
        if cfg.token_store is not None:
            tokens = cfg.token_store
        else:
            at, rt = resolve_tokens(cfg.access_token, cfg.refresh_token)
            tokens = TokenStore(at, rt)
        self.cfg = cfg
        self.client = IFindClient(tokens, base_url_quant=cfg.base_url_quant,
                                  base_url_ft=cfg.base_url_ft, timeout=cfg.timeout,
                                  max_retries=cfg.max_retries, batch_size=cfg.batch_size)
        self.store = SectorStore(cfg.db_path)
        self.sync = SectorSync(self.client, self.store,
                               concurrency=cfg.members_concurrency,
                               progress_every=cfg.members_progress_every)
```

- [ ] **Step 2: 写 service 失败测试**

`tests/test_service.py`：

```python
# -*- coding: utf-8 -*-
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest import mock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ifind_sector_hub import SectorHub, HubConfig, TokenStore
from ifind_sector_hub.service import build_router


def make_app(tmpdir):
    hub = SectorHub(HubConfig(db_path=os.path.join(tmpdir, "x.db"),
                              access_token="at", refresh_token="rt"))
    hub.store.save_concept_dict([
        {"concept_code": "885001.TI", "short_name": "人工智能"}])
    hub.store.save_concept_members("885001.TI", [
        {"stock_code": "600519.SH", "stock_name": "贵州茅台"}], "20260917")
    app = FastAPI()
    app.include_router(build_router(hub))
    return TestClient(app), hub


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client, self.hub = make_app(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_concepts_endpoint(self):
        r = self.client.get("/concepts")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"count": 1,
                                    "concepts": [{"concept_code": "885001.TI", "concept_name": "人工智能"}]})

    def test_members_endpoint(self):
        r = self.client.get("/concepts/885001.TI/members")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["members"][0]["stock_code"], "600519.SH")

    def test_sync_members_endpoint_triggers_sync(self):
        with mock.patch.object(self.hub.sync, "sync_concept_members", return_value=3) as s:
            r = self.client.post("/sync/members", json={"concept_codes": ["885001.TI"], "date": "20260917"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "saved_records": 3})
        s.assert_called_once_with(["885001.TI"], "20260917")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试确认失败后实现 service.py**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest tests.test_service -v
```
Expected: ERROR（`cannot import name 'service'`）。实现：

```python
# -*- coding: utf-8 -*-
"""可选 FastAPI 服务层（只读 + 刷新触发；本期只交付不部署）。

依赖 optional extras：pip install "ifind-sector-hub[service]"。
未安装 fastapi 时 import 本模块报 ImportError，不影响库的核心使用。
"""

from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel


def build_router(hub) -> APIRouter:
    """构建板块数据路由；挂到任一 FastAPI 应用即可对外供数。"""
    router = APIRouter()

    @router.get("/concepts")
    def list_concepts():
        names = hub.store.get_concept_names()
        concepts = [{"concept_code": c, "concept_name": n}
                    for c, n in sorted(names.items())]
        return {"count": len(concepts), "concepts": concepts}

    @router.get("/concepts/{concept_code}/members")
    def concept_members(concept_code: str, date: Optional[str] = None):
        members = hub.store.get_concept_members(concept_code, date)
        return {"concept_code": concept_code, "count": len(members), "members": members}

    @router.get("/stocks/{stock_code}/concepts")
    def stock_concepts(stock_code: str, date: Optional[str] = None):
        rows = hub.store.get_stock_concepts(stock_code, date)
        return {"stock_code": stock_code, "count": len(rows), "concepts": rows}

    class SyncMembersRequest(BaseModel):
        concept_codes: List[str]
        date: str

    @router.post("/sync/members")
    def sync_members(req: SyncMembersRequest):
        saved = hub.sync.sync_concept_members(req.concept_codes, req.date)
        return {"ok": True, "saved_records": saved}

    return router
```

- [ ] **Step 4: 跑全部包测试确认通过**

```bash
cd /root/Projects/ifind-sector-hub && $PY -m unittest discover -s tests -v
```
Expected: PASS（codes 2 + tokens 6 + client 10 + storage 7 + sync 6 + service 3 = 34 tests）

- [ ] **Step 5: 写包 README 并提交**

`README.md` 内容要点（自拟成文，包含：定位一句话、安装 `pip install -e /root/Projects/ifind-sector-hub`、快速开始代码块（HubConfig+FileTokenStore 示例，同 `__init__.py` docstring）、模块表、快照语义与日期格式注意事项、service 层挂载示例、测试命令）。

```bash
cd /root/Projects/ifind-sector-hub && git add ifind_sector_hub/__init__.py ifind_sector_hub/service.py tests/test_service.py README.md && \
  git commit -m "feat: SectorHub 门面 + 可选 FastAPI service 层，包 0.1.0 完成"
```

---

### Task 7: monitor 接入——基线捕获 + Database 门面化 + ifind_hub 单例

**Files:**
- Create: `/root/projects/2.monitor_940/ifind-sector-attribution/ifind_hub.py`
- Create: `/root/projects/2.monitor_940/ifind-sector-attribution/scripts/verify_sector_hub_equivalence.py`
- Modify: `database.py`（三表方法委托 + 删三表 DDL/`get_concept_stocks` + watched 迁移钩子）
- Modify: `config.py`（A股过滤函数改为从组件 re-export）
- Test: 既有 `tests/test_performance_architecture.py` + `tests/test_scan_push.py` 全绿

**Interfaces:**
- Consumes: `ifind_sector_hub`（Task 6）
- Produces（monitor 内部）:
  - `ifind_hub.get_hub() -> SectorHub`（进程级单例，FileTokenStore 指向 `data/ifind_sector_hub_token.json`）
  - `ifind_hub.refresh_token_now() -> str`
  - `Database` 既有公开方法签名全部不变（调用方零改动）；新增委托访问器 `get_concept_names` / `get_latest_member_date` / `get_latest_member_stock_names` / `get_all_member_stock_names` / `get_latest_members_snapshot`

- [ ] **Step 1: 捕获等价性基线（改动任何 monitor 代码之前）**

`scripts/verify_sector_hub_equivalence.py`（直接调用路由函数，不起服务、不走网络）：

```python
# -*- coding: utf-8 -*-
"""
sector-hub 切换等价性验证。
用法（在项目根目录、vibe-trading python）：
  PYTHONPATH=. python scripts/verify_sector_hub_equivalence.py capture  # 切换前捕获
  PYTHONPATH=. python scripts/verify_sector_hub_equivalence.py verify   # 切换后对比
基线存 data/equivalence_baseline.json（gitignore）。
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "data", "equivalence_baseline.json")
DB = os.path.join(ROOT, "data", "sector_attribution.db")

import api_server  # noqa: E402  （import 即触发 Database() 初始化，只读快照安全）


def _latest_strength_date():
    with sqlite3.connect(DB) as conn:
        row = conn.execute("SELECT MAX(calc_date) FROM concept_strength").fetchone()
        return row[0] or ""


def _db_fingerprint():
    fp = {}
    with sqlite3.connect(DB) as conn:
        for table, date_col in [("ths_concept_dict", "update_date"),
                                ("concept_members", "member_date"),
                                ("stock_concept_map", "map_date"),
                                ("watched_concepts", None)]:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            mx = None
            if date_col:
                mx = conn.execute(f"SELECT MAX({date_col}) FROM {table}").fetchone()[0]
            fp[table] = {"count": cnt, "max_date": mx}
    return fp


def _snapshot():
    date = _latest_strength_date()
    out = {"endpoints": {}, "db": _db_fingerprint()}
    calls = {
        "concept_list": lambda: api_server.get_concept_list(),
        "watched": lambda: api_server.sector_manage_watched(),
        "dates": lambda: api_server.get_dates(),
        "history_sector": lambda: api_server.history_dashboard(
            scope="sector", date=date, force_calc=False),
    }
    for name, fn in calls.items():
        try:
            out["endpoints"][name] = fn()
        except Exception as e:  # 记录错误本身也是基线的一部分
            out["endpoints"][name] = {"__error__": str(e)}
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    snap = _snapshot()
    if mode == "capture":
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2, sort_keys=True)
        print(f"[EQUIV] 基线已捕获 → {BASELINE}")
        return 0
    if mode != "verify" or not os.path.exists(BASELINE):
        print("用法: capture | verify（verify 前须先 capture）")
        return 2
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    diffs = []
    for key in ("endpoints", "db"):
        for k in sorted(set(base[key]) | set(snap[key])):
            a, b = base[key].get(k), snap[key].get(k)
            if json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str):
                diffs.append(f"[DIFF] {key}.{k}:\n  before={json.dumps(a, default=str)[:500]}\n  after ={json.dumps(b, default=str)[:500]}")
    if diffs:
        print("\n".join(diffs))
        print(f"[EQUIV] ❌ {len(diffs)} 处不一致")
        return 1
    print("[EQUIV] ✅ 端点响应与 DB 指纹与基线完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**先核对路由函数名**：`grep -n "def get_concept_list\|def sector_manage_watched\|def get_dates\|def history_dashboard" api_server.py`——若实际函数名不同（如带前缀），以实际为准修正 `calls` 字典后运行。捕获：

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  PYTHONPATH=. $PY scripts/verify_sector_hub_equivalence.py capture
```
Expected: `[EQUIV] 基线已捕获`（history_sector 若因无数据返回 error 字段也正常——错误信息同样作为基线）。

- [ ] **Step 2: 写 ifind_hub.py**

```python
# -*- coding: utf-8 -*-
"""monitor 的 ifind-sector-hub 接入点：进程级单例 + token 文件持久化。

token 解析（spec 决策 #6）：config_local.py / 环境变量仅作首次 bootstrap；
运行期刷新与轮换落 data/ifind_sector_hub_token.json（FileTokenStore + flock）。
"""

import os
import threading

import config
from ifind_sector_hub import FileTokenStore, HubConfig, SectorHub

_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "ifind_sector_hub_token.json")

_hub = None
_hub_lock = threading.Lock()


def get_hub() -> SectorHub:
    """进程级单例（同进程共享 token 刷新与 FileTokenStore 锁）。"""
    global _hub
    if _hub is None:
        with _hub_lock:
            if _hub is None:
                _hub = SectorHub(HubConfig(
                    db_path=config.DB_PATH,
                    access_token=config.ACCESS_TOKEN,
                    refresh_token=config.REFRESH_TOKEN,
                    token_store=FileTokenStore(_TOKEN_FILE),
                ))
    return _hub


def refresh_token_now() -> str:
    """手动刷新 access_token（调试用，等价旧 `from ifind_client import refresh_access_token`）。"""
    hub = get_hub()
    return hub.client.tokens.refresh_access_token(hub.client.headers["access_token"])
```

- [ ] **Step 3: config.py re-export A股过滤函数**

`config.py` 删除 `A_SHARE_SUFFIXES`（:68）、`A_SHARE_CONCEPT_PREFIXES`（:72）两个定义与 `is_a_share_code`（:75-77）、`is_a_share_concept`（:80-85）两个函数体，在原位置替换为：

```python
# ========== A股市场过滤（实现迁至 ifind-sector-hub 组件，此处 re-export 保持调用方不变） ==========
from ifind_sector_hub.codes import (  # noqa: F401
    A_SHARE_CONCEPT_PREFIXES,
    A_SHARE_SUFFIXES,
    is_a_share_code,
    is_a_share_concept,
)
```

注意：`config_local.py` 的 `from config_local import *`（config.py:42-45）在此之后——保持 re-export 在文件**靠前位置**（原函数所在 :63-85 区域），确保 `config.is_a_share_code` 对所有调用方可用。

- [ ] **Step 4: Database 门面化**

修改 `database.py`：

1. 头部 import 增加：`from ifind_sector_hub import SectorStore`。
2. `__init__` 增加 `self.sector_store = SectorStore(db_path or config.DB_PATH)`（组件负责三表 `CREATE IF NOT EXISTS`，幂等，对现有库无副作用），保留原 `self.db_path` 与目录创建逻辑。
3. `_init_db` 的 DDL 大字符串**删除**三段：`ths_concept_dict`（:48-56）、`stock_concept_map` + 其索引（:59-66）、`concept_members` + 其索引（:69-76）。其余表（daily_kline/watched/kg 等）原样保留。
4. 以下方法体替换为一行委托（方法签名、docstring 保留）：

```python
    def save_concept_dict(self, concepts, update_date=None):
        self.sector_store.save_concept_dict(concepts, update_date)

    def get_all_concept_codes(self):
        return self.sector_store.get_all_concept_codes()

    def get_all_member_stock_codes(self):
        return self.sector_store.get_all_member_stock_codes()

    def get_all_mapped_stock_codes(self):
        return self.sector_store.get_all_mapped_stock_codes()

    def get_concept_name(self, concept_code):
        return self.sector_store.get_concept_name(concept_code)

    def save_stock_concept_map(self, mappings, map_date):
        self.sector_store.save_stock_concept_map(mappings, map_date)

    def get_stock_concepts(self, stock_code, map_date=None):
        return self.sector_store.get_stock_concepts(stock_code, map_date)

    def save_concept_members(self, concept_code, members, member_date):
        self.sector_store.save_concept_members(concept_code, members, member_date)

    def get_concept_members(self, concept_code, member_date=None):
        return self.sector_store.get_concept_members(concept_code, member_date)

    def get_concept_members_map(self, concept_codes):
        return self.sector_store.get_concept_members_map(concept_codes)
```

5. `refresh_concept_dict_replace`（:261-341）替换为钩子 + 委托（迁移语义逐字保留：去Ⅲ/Ⅱ/Ⅰ后缀精确匹配、仅迁往观察池前缀、迁移失败才删）：

```python
    def _migrate_watched_hook(self, conn, removed_codes, old_name_map, new_name_map):
        """字典全量替换时的 watched 勾选迁移（在组件 replace 的同一事务内执行）。"""
        observe_prefixes = getattr(config, "OBSERVE_CONCEPT_PREFIXES", ("884", "885", "886"))
        name_to_new = {}
        for nc, nm in new_name_map.items():
            if nc[:3] in observe_prefixes:
                name_to_new.setdefault(nm, nc)
        migrated, dropped_watched = [], []
        for r in conn.execute("SELECT concept_code FROM watched_concepts").fetchall():
            cc = r["concept_code"]
            if cc in new_name_map:
                continue
            old_name = old_name_map.get(cc, "")
            base_name = old_name.rstrip("Ⅲ").rstrip("Ⅱ").rstrip("Ⅰ").strip()
            target = name_to_new.get(base_name)
            if target:
                migrated.append((cc, target, old_name))
                conn.execute("UPDATE watched_concepts SET concept_code=? WHERE concept_code=?",
                             (target, cc))
            else:
                dropped_watched.append((cc, old_name))
        if dropped_watched:
            ph = ",".join("?" * len(dropped_watched))
            conn.execute(f"DELETE FROM watched_concepts WHERE concept_code IN ({ph})",
                         [c for c, _ in dropped_watched])
        return migrated, dropped_watched

    def refresh_concept_dict_replace(self, boards):
        return self.sector_store.replace_concept_dict(boards, migrate_hook=self._migrate_watched_hook)
```

6. **删除** `get_concept_stocks`（:701-719，无调用方；先 `grep -rn "get_concept_stocks" --include="*.py" .` 复核仅 database.py 自身命中再删）。
7. **新增** 5 个委托访问器（供 Task 9 收编使用）：

```python
    def get_concept_names(self):
        return self.sector_store.get_concept_names()

    def get_latest_member_date(self):
        return self.sector_store.get_latest_member_date()

    def get_latest_member_stock_names(self):
        return self.sector_store.get_latest_member_stock_names()

    def get_all_member_stock_names(self):
        return self.sector_store.get_all_member_stock_names()

    def get_latest_members_snapshot(self):
        return self.sector_store.get_latest_members_snapshot()
```

8. 保留不动：`_connect`、`get_a_share_concept_codes`、`get_observe_concept_codes`、watched 三方法、`get_stock_concepts_from_members`（仍用 `self._connect` 查三表——三表物理上仍在同一库文件）、watched 种子逻辑（`_init_db` 内）及其余非板块方法。

- [ ] **Step 5: 跑既有测试 + 等价性中期检查**

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  PYTHONPATH=. $PY -m unittest discover -s tests -v && \
  PYTHONPATH=. $PY -c "
from database import Database
db = Database()
print('codes:', len(db.get_all_concept_codes()))
print('watched:', len(db.get_watched_concept_codes()))
print('names:', len(db.get_concept_names()))
" && \
  PYTHONPATH=. $PY scripts/verify_sector_hub_equivalence.py verify
```
Expected: 既有测试全绿；三个 print 输出真实规模（codes≈710、watched 为当前勾选数、names≈710）；等价性 verify——**注意**：`get_watched_concept_codes` 逻辑未动、三表数据未动，`endpoints` 与 `db` 应与基线一致（`ths_concept_dict` 的 `max(update_date)` 不变——本轮没有任何写库）。

- [ ] **Step 6: 提交（monitor 仓库，只加本任务文件）**

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  git add database.py config.py ifind_hub.py scripts/verify_sector_hub_equivalence.py && \
  git commit -m "refactor(sector-hub): Database 三表方法委托组件 + ifind_hub 单例 + 等价性基线工具"
```

---

### Task 8: sync_pipeline 薄编排 + refresh-boards 重排

**Files:**
- Modify: `sync_pipeline.py`（:24-251 板块半边）
- Modify: `main.py`（`cmd_refresh_boards` :135-211 + 顶部 import）
- Test: `PYTHONPATH=. $PY -m unittest discover -s tests` 全绿 + refresh-boards 干跑验证

**Interfaces:**
- Consumes: `ifind_hub.get_hub()`、`Database`（Task 7）
- Produces: `SyncPipeline` 公开方法签名全部不变（`run_init` / `run_daily` / `refresh_observe_members` / `init_*` / `_fetch_concept_members_batch` 保留为薄委托，kg_sources 的 `pipeline._fetch_concept_members_batch(...)` 调用点不受影响）

- [ ] **Step 1: 改 sync_pipeline.py**

1. 头部 `from ifind_client import IFindClient` 删除，改 `from ifind_hub import get_hub`；`__init__` 改为：

```python
    def __init__(self):
        self.hub = get_hub()
        self.client = self.hub.client
        self.db = Database()
```

（`self.client` 保留——`sync_daily_kline` 等行情半边继续用，接口不变。）

2. 板块半边方法体替换为委托（签名与返回值不变）：

```python
    def init_concept_dict(self):
        """步骤0: 初始化概念板块字典（委托组件；ALL_CONCEPT_CODES 为 monitor 口径）。"""
        return self.hub.sync.init_concept_dict(config.ALL_CONCEPT_CODES)

    def init_stock_concept_map(self, stock_codes: List[str], map_date: str = None):
        return self.hub.sync.sync_stock_concept_map(stock_codes, map_date)

    def init_concept_members(self, member_date: str = None):
        member_date = member_date or datetime.now().strftime("%Y%m%d")
        print(f"[INIT] 开始初始化概念板块成分股，日期={member_date}...")
        concept_codes = self.db.get_a_share_concept_codes()
        print(f"[INIT] 共 {len(concept_codes)} 个概念")
        return self.hub.sync.sync_concept_members(concept_codes, member_date)

    def refresh_observe_members(self, member_date: str = None):
        """一键刷新观察池全集字典+成分股（管理页刷新按钮用；编排留 monitor，数据侧走组件）。"""
        member_date = member_date or datetime.now().strftime("%Y%m%d")
        observe_codes = self.db.get_observe_concept_codes()
        return self.hub.sync.refresh_dict_and_members(observe_codes, member_date)

    def init_concept_universe(self, map_date: str = None):
        # 板块池开关是 monitor 语义；existing_codes 传 monitor 口径保持原判重行为
        return self.hub.sync.init_concept_universe(
            map_date,
            skip=bool(config.SECTOR_POOL_ENABLED and config.SECTOR_POOL_CODES),
            batch_size=config.BATCH_SIZE,
            existing_codes=set(self.db.get_a_share_concept_codes()),
        )

    def _fetch_concept_members_batch(self, concept_codes: List[str], member_date: str):
        """兼容入口（kg_sources 在用）：委托组件并发内核。"""
        return self.hub.sync.sync_concept_members(concept_codes, member_date)
```

3. **删除** `_fetch_one_concept_members`（:58-81）与 `_fetch_concept_members_batch` 原实现（:207-251）——已由上面的委托替代。行情半边（`sync_daily_kline`/`calc_daily_strength`/`calc_daily_attribution`/`run_daily`）与 `run_init` 编排**不动**。
4. `refresh_observe_members` 返回 dict 少了旧键 `failed_concepts`（旧实现就从未真正返回过该键——核对旧代码 :118-123 确认返回的四个键与 `refresh_dict_and_members` 一致；若核对发现旧代码确有该键则保持一致补上）。

- [ ] **Step 2: 重排 cmd_refresh_boards（main.py :135-211）**

替换整个函数体（去重接口2解析、消灭 :174-176 裸 SQL、成分股补拉改并发）：

```python
def cmd_refresh_boards(args):
    """
    统一以 smart_stock_picking 枚举结果刷新板块字典（行业 + 概念全集）。
    流程：动态枚举 → 全量替换字典（级联清理+勾选迁移）→ 新板块并发补拉成分股。
    """
    from datetime import datetime
    from collections import Counter
    from database import Database
    from ifind_hub import get_hub

    db = Database()
    hub = get_hub()

    # 1. 动态枚举板块全集
    print("[REFRESH-BOARDS] 枚举同花顺板块全集（行业+概念）...")
    boards = hub.client.get_all_ths_boards()
    if not boards:
        print("[REFRESH-BOARDS] ❌ 枚举失败（接口无返回），中止")
        sys.exit(1)
    prefix_cnt = Counter(b["concept_code"][:3] for b in boards)
    print(f"[REFRESH-BOARDS] 枚举到 {len(boards)} 个板块: {dict(prefix_cnt)}")

    # 2. 全量替换字典 + 级联清理 + 勾选名称迁移（同事务，watched 钩子在 Database 内）
    result = db.refresh_concept_dict_replace(boards)
    print(f"[REFRESH-BOARDS] 字典已刷新: 新增 {result['added']} / 移除 {result['removed']} / 总数 {result['total']}")
    for old, new, name in result["migrated"]:
        print(f"  勾选迁移: {name} {old} → {new}")
    for code, name in result["dropped_watched"]:
        print(f"  ⚠ 勾选被删（无同名新码，需手动重选）: {name} {code}")

    # 3. 新板块补拉成分股（仅观察池前缀且缺成分股的；组件并发内核，A股过滤）
    if args.skip_members:
        print("[REFRESH-BOARDS] --skip-members，跳过成分股补拉")
    else:
        have_members = {cc for cc in db.get_latest_members_snapshot()[1]}
        need = [cc for cc in db.get_observe_concept_codes() if cc not in have_members]
        need += [new for _, new, _ in result["migrated"]
                 if new not in have_members and new not in need]
        print(f"[REFRESH-BOARDS] 需补拉成分股的新板块: {len(need)} 个（接口2并发）")
        if need:
            member_date = datetime.now().strftime("%Y%m%d")
            hub.sync.sync_concept_members(
                need, member_date,
                stock_filter=lambda sc: sc.endswith((".SH", ".SZ", ".BJ")))

    print("[REFRESH-BOARDS] ✅ 完成。板块管理页候选将以新字典为准。")
```

（成分股补拉从"逐概念串行"变"8 线程并发"，结果等价、速度更快；逐股 A股过滤与旧 :198 行为一致。）

- [ ] **Step 3: 验证**

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  PYTHONPATH=. $PY -m unittest discover -s tests -v && \
  PYTHONPATH=. $PY -c "
from sync_pipeline import SyncPipeline
p = SyncPipeline()
print(type(p.hub).__module__, type(p.client).__name__)
print('_fetch_concept_members_batch 可用:', callable(p._fetch_concept_members_batch))
" && \
  PYTHONPATH=. $PY main.py refresh-boards --skip-members
```
Expected: 测试全绿；打印 `ifind_sector_hub SectorHub` / `True`；refresh-boards 干跑到"✅ 完成"（真实枚举接口调用一次，属预期：该命令本身就是数据刷新入口；若 token 失效会自动刷新重试）。

- [ ] **Step 4: 等价性复查 + 提交**

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  PYTHONPATH=. $PY scripts/verify_sector_hub_equivalence.py verify ; \
  git add sync_pipeline.py main.py && \
  git commit -m "refactor(sector-hub): sync_pipeline 板块半边薄编排 + refresh-boards 重排去重"
```

注意：`verify` 此时会因 refresh-boards **真的刷新了字典**（update_date 变化、字典行数变化）而在 `ths_concept_dict` 指纹上 DIFF——这是预期中的数据变化，人工确认 DIFF 仅限于 `ths_concept_dict.count/max_date` 与 `concept_members`（补拉新增），`endpoints.*` 语义字段（概念列表成员、watched）结构一致后，重跑 `capture` 刷新基线再继续。

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  PYTHONPATH=. $PY scripts/verify_sector_hub_equivalence.py capture
```

---

### Task 9: 裸 SQL 收编 + 删除 ifind_client.py + scripts 切换

**Files:**
- Modify: `api_server.py`（:511-532 两处）、`realtime_engine.py`（:62-86）、`sector_manage.py`（:40 import + :136-144）、`auction_engine.py`（:50-64）、`kg_sources.py`（:72-76 + :91 import）、`theme_catalyst.py`（:212-234 + :702-707 + client import 处）
- Modify: `scripts/backfill_style_history.py`（:24 import）
- Delete: `ifind_client.py`
- Test: 等价性 verify 全绿 + grep 门禁

**Interfaces:**
- Consumes: `Database` 新增访问器（Task 7）、`ifind_hub.get_hub().client`
- Produces: 三表访问全部经由 `Database`/组件，仓库内不再有对 `ths_concept_dict`/`concept_members`/`stock_concept_map` 的裸 SQL（kg_analysis 的 kg 表/daily_kline 查询除外——不在范围）

- [ ] **Step 1: api_server.py（历史看板 :511-532）**

把"概念名映射"块（:512-516）替换为：

```python
    # 概念名映射
    concept_names = db.get_concept_names()
```

把"成分股名称映射"块（:522-532，含函数内 `import sqlite3 as _sqlite3`）替换为：

```python
    # 成分股名称映射
    stock_names = db.get_latest_member_stock_names()
```

若 `sqlite3` 在 api_server.py 其余处无使用（`grep -n "sqlite3" api_server.py` 复核，含模块级 import），删除对应 import。

- [ ] **Step 2: realtime_engine.py（_ensure_maps :62-86）**

删除函数内 `import sqlite3` 与 `with sqlite3.connect(...)` 块（:74-86），替换为：

```python
        self._concept_names = self.db.get_concept_names()
        self._stock_names = self.db.get_latest_member_stock_names()
```

（`self._concept_names`/`self._stock_names` 赋值语义与原裸 SQL 一致：字典全量、最新快照首个出现优先。）

- [ ] **Step 3: sector_manage.py**

`:40` 的 `from ifind_client import IFindClient` 删除；`:54` 的 `client = IFindClient()` 改为：

```python
    from ifind_hub import get_hub
    client = get_hub().client
```

`_load_concept_names`（:136-144）整个函数删除，调用点 `:51` 改 `concept_names = db.get_concept_names()`。

- [ ] **Step 4: auction_engine.py（_ensure_stock_names :50-64）**

函数体替换为：

```python
    def _ensure_stock_names(self):
        """懒加载股票名称（从 concept_members 最新快照，custom_group 无 name 列）"""
        if self._stock_names is not None:
            return
        self._stock_names = self.db.get_latest_member_stock_names()
```

- [ ] **Step 5: kg_sources.py**

`_latest_snapshot_date`（:72-76）替换为：

```python
    def _latest_snapshot_date(self) -> str:
        return self.db.get_latest_member_date()
```

`IfindStockConceptAdapter.fetch_pairs` 内 `:91-95` 的 `from ifind_client import IFindClient` 与 `client = IFindClient()` 替换为：

```python
        from ifind_hub import get_hub
        client = get_hub().client
```

- [ ] **Step 6: theme_catalyst.py**

1. `theme_members_and_size`（:212-234）开头的生产库只读连接块替换为：

```python
def theme_members_and_size(day: str):
    """题材全成分（生产库最新快照，标注时点）+ 题材规模。"""
    from ifind_hub import get_hub
    snap, members_raw = get_hub().store.get_latest_members_snapshot()
    names = get_hub().store.get_concept_names()
    members = defaultdict(set)
    for cc, lst in members_raw.items():
        for sc, _ in lst:
            members[cc].add(sc)
    sizes = {cc: len(v) for cc, v in members.items()}
```

（其后 :227-234 的回测库历史规模覆盖逻辑保持不动。）
2. `build_market_historical` 内 `:702-707` 的生产库块替换为：

```python
    from ifind_hub import get_hub
    prod_names = get_hub().store.get_all_member_stock_names()
    codes = set(prod_names)
```

（后续 `btc` 回测库块与 `names` 的使用方式不变——核对函数后续是否引用 `prod` 连接对象，若 `names` 变量名不同则按实际上下文对齐。）
3. 该文件内其余 `IFindClient` 使用点（`get_mapping` 的 `batch_get_stock_concepts`、`resolve_pool` 的 `get_concept_members`、`market_snapshot` 的 `smart_pick_stocks`）：`grep -n "IFindClient\|ifind_client" theme_catalyst.py` 找到构造处，统一改 `from ifind_hub import get_hub` + `get_hub().client`。

- [ ] **Step 7: scripts/backfill_style_history.py（:24）**

`import ifind_client` 改：

```python
from ifind_hub import get_hub
ifind_client = get_hub().client
```

（先 `grep -n "ifind_client\." scripts/backfill_style_history.py` 核对用法是以模块属性访问还是直接函数调用，按实际调整别名保持其余行不动。）

- [ ] **Step 8: 删除 ifind_client.py + grep 门禁**

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  grep -rn "ifind_client" --include="*.py" . | grep -v tests/test_api.py ; \
  rm ifind_client.py && \
  echo "=== 三表裸 SQL 残留检查（应只剩 database.py 自有 get_stock_concepts_from_members 与 kg/daily_kline 相关） ===" && \
  grep -rn "ths_concept_dict\|concept_members\|stock_concept_map" --include="*.py" . | \
    grep -v "database.py\|ifind_hub.py\|kg_analysis.py\|docs/\|tests/"
```

Expected: 第一个 grep 仅剩 `tests/test_api.py`（连通性脚本，Step 9 处理）；删除后第二个 grep 无输出（theme_catalyst 的 BT_DB/TC_DB 查询不含三表名）。若出现遗漏调用点，逐个按同模式收编后重跑。

- [ ] **Step 9: 修正 tests/test_api.py 与最终验证**

`tests/test_api.py` 顶部 `from ifind_client import IFindClient` 改 `from ifind_hub import get_hub`，全文 `IFindClient()` 替换为 `get_hub().client`（该脚本本就是真实连通性脚本，Task 10 冒烟用）。然后：

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  PYTHONPATH=. $PY -m unittest discover -s tests -v && \
  PYTHONPATH=. $PY -c "import api_server, realtime_engine, sector_manage, auction_engine, kg_sources, theme_catalyst, scan_push, sync_pipeline, main; print('全部模块可导入')" && \
  PYTHONPATH=. $PY scripts/verify_sector_hub_equivalence.py verify
```
Expected: 测试全绿；模块导入成功；`[EQUIV] ✅ 端点响应与 DB 指纹与基线完全一致`。

- [ ] **Step 10: 提交**

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  git add api_server.py realtime_engine.py sector_manage.py auction_engine.py kg_sources.py theme_catalyst.py scripts/backfill_style_history.py tests/test_api.py && \
  git rm ifind_client.py && \
  git commit -m "refactor(sector-hub): 收编全部三表裸 SQL，删除 ifind_client.py，client 统一走组件"
```

---

### Task 10: 真实 API 冒烟 + 文档更新收尾

**Files:**
- Modify: `requirements.txt`、`install_service.sh`、`docs/ops/DEPLOYMENT.md`、`.gitignore`、`AGENTS.md`、`README.md`、`docs/architecture/ARCHITECTURE.md`、`docs/architecture/DESIGN-ifind-sector-hub.md`（访问器清单微调）

**Interfaces:** 无代码接口变化，纯文档/配置收尾。

- [ ] **Step 1: 真实 API 冒烟（唯一一次真实调用）**

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && PYTHONPATH=. $PY main.py test
```
Expected: 5 个接口连通 OK（token 经 FileTokenStore 自动引导/刷新）。若报 401 后自动恢复即验证了刷新链路；持续失败则人工检查 `data/ifind_sector_hub_token.json` 是否生成、`config_local.py` 的 REFRESH_TOKEN 是否有效。

- [ ] **Step 2: requirements.txt / install_service.sh / DEPLOYMENT.md / .gitignore**

- `requirements.txt` 在 kline-fetcher 注释块附近新增：

```
# 板块/概念数据层公共组件（本地包，需单独安装）：
#   pip install -e /root/Projects/ifind-sector-hub
ifind-sector-hub @ file:///root/Projects/ifind-sector-hub
```

- `install_service.sh` 与 `docs/ops/DEPLOYMENT.md`：核对依赖安装段，按现有风格各补 `pip install -e /root/Projects/ifind-sector-hub` 一行/一小节。
- `.gitignore` 增加三行：`data/ifind_sector_hub_token.json`、`data/ifind_sector_hub_token.json.lock`、`data/equivalence_baseline.json`。

- [ ] **Step 3: AGENTS.md 更新（保持"读完即懂"标准）**

1. 「🔑 运维知识」token 段改写：刷新机制改为组件 FileTokenStore（`data/ifind_sector_hub_token.json` + flock；`config_local.py` 仅首次 bootstrap；轮换自动落盘不再改写 config_local.py）；手动刷新命令改 `python -c "from ifind_hub import refresh_token_now; print(refresh_token_now())"`。
2. 「运行环境」表加一行：`ifind-sector-hub 本地包 pip install -e /root/Projects/ifind-sector-hub`。
3. 「代码地图」删 `ifind_client.py` 行，加 `ifind_hub.py` 行（monitor 组件接入点/单例）；`database.py`/`sync_pipeline.py` 职责描述补"板块三表委托 ifind-sector-hub 组件"；新增组件仓库指路（`/root/Projects/ifind-sector-hub`，含 README）。
4. 「数据库」节的 9 张业务表说明补注：三表 schema 权威来源同时是组件 `storage.py`。

- [ ] **Step 4: README.md / ARCHITECTURE.md / spec 微调**

- `README.md` 快速开始/依赖部分加组件安装说明。
- `docs/architecture/ARCHITECTURE.md`：相应章节（数据源/缓存）补"板块数据层已抽离为 ifind-sector-hub 组件，monitor 经 ifind_hub 单例接入"。
- `docs/architecture/DESIGN-ifind-sector-hub.md` §6.3："新增两个访问器"更正为实际交付的 **5 个**（`get_concept_names` / `get_latest_member_date` / `get_latest_member_stock_names` / `get_all_member_stock_names` / `get_latest_members_snapshot`），并注明 `refresh_observe_members` 组件侧名为 `refresh_dict_and_members`、返回键不含 `failed_concepts`。

- [ ] **Step 5: 全量回归 + 提交**

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution && \
  PYTHONPATH=. $PY -m unittest discover -s tests -v && \
  PYTHONPATH=. $PY scripts/verify_sector_hub_equivalence.py verify && \
  git add requirements.txt install_service.sh docs/ops/DEPLOYMENT.md .gitignore AGENTS.md README.md docs/architecture/ARCHITECTURE.md docs/architecture/DESIGN-ifind-sector-hub.md && \
  git commit -m "docs(sector-hub): 组件接入文档收尾——token 运维/依赖/架构说明/访问器清单"
```
Expected: 测试全绿、EQUIV ✅、提交成功。

---

## Self-Review 记录

- **Spec 覆盖**：spec §四边界（Task 1-6 组件侧 / Task 7-9 monitor 侧）、§六接口（6.1→Task 3、6.2→Task 2、6.3→Task 4、6.4→Task 5）、§七收编清单 9 点位（Task 9 Step 1-7 逐一对应 + main.py 于 Task 8）、§八红线（各 Task 测试与等价性脚本覆盖）、§九验收（Task 9 Step 9 + Task 10 Step 1/5）、§十一文件清单（全覆盖）。spec §七"后台刷新流不动"由 Task 8 仅改 `refresh_observe_members` 内部实现、`_refresh_state` 与 `clear_cache` 时机未触碰保证。
- **占位符**：无 TBD/TODO；所有代码块完整可执行（自检中修正过 3 处：FileTokenStore 初始化需从文件同步内存、storage 测试的快照语义断言、refresh-boards 冗余表达式——均已直接改进代码块）。
- **类型一致性**：`refresh_dict_and_members` 返回键（Task 5 定义 / Task 8 消费）、`migrate_hook` 签名（Task 4 定义 / Task 7 实现）、`get_latest_members_snapshot` 返回结构（Task 4 定义 / Task 8-9 消费）已逐一核对一致。
