# -*- coding: utf-8 -*-
"""分层约束测试。

两道防线：
1. LayeringContractTests —— import-linter（.importlinter.ini）：约束 api/database
   两个包的边界。限制：grimp 依赖图只收录包，平铺模块（config/各引擎/main）
   不在图内，契约对它们空转。
2. LayeringAstTests —— 纯 stdlib AST 检查：覆盖平铺模块的分层规则
   （引擎不碰接口层 / config 是叶子 / 计算层纯净 / api_server 只做组装）。
   只检查模块级 import（函数内懒加载是既定设计：路由懒加载引擎、
   api 懒加载 main 的 import_groups）。

分层定义（上层可依赖下层，禁止反向）：
    main / api_server（组装入口）
      → api（接口层）
        → 引擎层（realtime_engine / auction_engine / sync_pipeline / sector_manage /
                  kg_* / theme_catalyst / scan_push / trade_calendar / ifind_hub / ...）
          → 计算层（core_calculator / stock_scorer）
          → 数据层（database）
            → config（叶子）
"""

import ast
import os
import shutil
import subprocess
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CALC = {"core_calculator", "stock_scorer"}
ENGINES = {
    "ifind_hub", "intraday_fetcher", "trade_calendar", "sync_pipeline",
    "realtime_engine", "auction_engine", "sector_manage", "kg_sources",
    "kg_builder", "kg_analysis", "theme_catalyst", "scan_push",
    "open_scan_engine", "probe_auction", "opening_strength",
}
PROJECT_MODULES = (
    {"config", "database", "api", "api_server", "main"} | CALC | ENGINES
)
ALL_EXCEPT = lambda *allowed: PROJECT_MODULES - set(allowed)

# 源 → 禁止依赖的项目模块集合
FORBIDDEN = {
    "config": ALL_EXCEPT(),
    "database": ALL_EXCEPT("config"),
    "api_server": ALL_EXCEPT("api"),
    "api": ALL_EXCEPT("database", "api", *CALC, *ENGINES, "config"),
    **{m: ALL_EXCEPT("config", "database", *CALC, *ENGINES) for m in CALC},
    **{m: ALL_EXCEPT("config", "database", *CALC, *ENGINES) for m in ENGINES},
    # main 是组装根，不受限
}


def _iter_source_files():
    """项目自身源码：根目录 *.py 与领域包（排除 tests/scripts 等）。"""
    for f in os.listdir(PROJECT_ROOT):
        if f.endswith(".py"):
            yield os.path.join(PROJECT_ROOT, f), f[:-3]
    for pkg in ("api", "database", "opening_strength"):
        base = os.path.join(PROJECT_ROOT, pkg)
        for dirpath, _dirnames, filenames in os.walk(base):
            for fn in filenames:
                if fn.endswith(".py"):
                    full = os.path.join(dirpath, fn)
                    yield full, pkg


def _module_level_project_imports(path):
    """模块级 import 中引用的项目内模块（相对导入归本包，不算外部依赖）。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    mods = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods & PROJECT_MODULES


class LayeringAstTests(unittest.TestCase):
    """平铺模块 + 包的分层规则（AST，仅模块级 import）。"""

    def test_no_layering_violations(self):
        violations = []
        for path, layer in _iter_source_files():
            forbidden = FORBIDDEN.get(layer)
            if not forbidden:
                continue
            imported = _module_level_project_imports(path)
            bad = imported & forbidden
            if bad:
                violations.append(f"{os.path.relpath(path, PROJECT_ROOT)} -> {sorted(bad)}")
        self.assertEqual(
            violations, [],
            "分层约束被违反（上层不得依赖更上层）：\n" + "\n".join(violations),
        )

    def test_opening_strength_never_imports_realtime_catalyst_or_api(self):
        """Premarket isolation also covers lazy imports inside functions."""
        forbidden = {"realtime_engine", "theme_catalyst", "api", "api_server"}
        violations = []
        for path, layer in _iter_source_files():
            if layer != "opening_strength":
                continue
            with open(path, encoding="utf-8") as source:
                tree = ast.parse(source.read())
            for node in ast.walk(tree):
                imported = set()
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported = {node.module.split(".")[0]}
                if imported & forbidden:
                    violations.append(
                        f"{os.path.relpath(path, PROJECT_ROOT)}:{node.lineno} "
                        f"-> {sorted(imported & forbidden)}")
        self.assertEqual(violations, [], "盘前领域不得依赖实时或接口层：\n" + "\n".join(violations))


try:
    import importlinter  # noqa: F401
    HAS_LINTER = True
except ImportError:
    HAS_LINTER = False


@unittest.skipUnless(HAS_LINTER, "import-linter 未安装（pip install import-linter）")
class LayeringContractTests(unittest.TestCase):
    """import-linter 契约（api/database 包边界，规则见 .importlinter.ini）。"""

    def test_import_contracts_hold(self):
        # 优先用当前解释器同目录的 lint-imports（conda 环境 pip 装的入口），退回 PATH
        import sys
        candidate = os.path.join(os.path.dirname(sys.executable), "lint-imports")
        lint = candidate if os.path.exists(candidate) else shutil.which("lint-imports")
        self.assertIsNotNone(lint, "找不到 lint-imports 可执行文件")
        result = subprocess.run(
            [lint, "--config", os.path.join(PROJECT_ROOT, ".importlinter.ini")],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"import-linter 契约被违反：\n{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
