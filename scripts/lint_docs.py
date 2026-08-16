# -*- coding: utf-8 -*-
"""
文档 lint 工具：检查 docs/ 目录下 Markdown 文档的结构与引用质量。

用法:
    python scripts/lint_docs.py                 # 检查 docs/**.md + README/AGENTS 链接
    python scripts/lint_docs.py --docs docs     # 只检查指定目录（默认 docs）
    python scripts/lint_docs.py -v              # 显示全部通过项

检查规则:
    ERROR（导致退出码 1）
      E101 docs 内文档缺 YAML front matter 或缺 title
      E102 title 为空或超过 40 字符（导航显示不全）
      E103 相对链接/图片目标文件不存在（http/#锚点/mailto 跳过）
      E104 Liquid 语法冲突：{{ 或 {%（Jekyll 构建会失败）
      E105 front matter 的 parent 在全部页面 title 中不存在
    WARN（提示，不影响退出码）
      W201 标题层级跳跃（如 H1 后直接 H3）
      W202 无 H1（just-the-docs 用 front matter title 渲染页标题，可接受）
      W203 连续空行超过 2 行
    根文件（README.md / AGENTS.md）：只做链接与 Liquid 检查（无 front matter 要求）
"""

import argparse
import os
import re
import sys
from typing import Dict, List, Tuple

DEFAULT_DOCS_DIR = "docs"
ROOT_FILES = ["README.md", "AGENTS.md"]

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s")
LIQUID_RE = re.compile(r"\{\{|\{%")

TITLE_MAX = 40


def parse_front_matter(lines: List[str]) -> Tuple[Dict[str, str], int]:
    """
    解析 YAML front matter（仅支持扁平 key: value）。

    :return: (dict, 结束行号)；无 front matter 返回 ({}, 0)
    """
    if not lines or lines[0].strip() != "---":
        return {}, 0
    fm: Dict[str, str] = {}
    for i in range(1, min(len(lines), 50)):
        s = lines[i].strip()
        if s == "---":
            return fm, i + 1
        if ":" in s and not s.startswith(("#", "-")):
            k, _, v = s.partition(":")
            fm[k.strip()] = v.strip().strip("'\"")
    return {}, 0  # 没有闭合 ---，视为无 front matter


def lint_file(path: str, require_fm: bool, titles: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """
    检查单个 md 文件。

    :param path: 文件路径
    :param require_fm: 是否要求 front matter（docs 内 True，根文件 False）
    :param titles: {title: path} 全部页面标题（parent 校验用）
    :return: (errors, warnings)
    """
    errors, warnings = [], []
    rel = path.replace(os.sep, "/")
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        return [f"E000 无法读取: {e}"], []

    fm, body_start = parse_front_matter(lines)
    body = lines[body_start:]
    title = fm.get("title", "")

    # --- E101/E102 front matter ---
    if require_fm:
        if not fm:
            errors.append("E101 缺少 front matter（需以 --- 开头并含 title）")
        elif not title:
            errors.append("E101 front matter 缺少 title")
    if title:
        if len(title) > TITLE_MAX:
            errors.append(f"E102 title 过长（{len(title)}>{TITLE_MAX}）: {title}")
        # E105 parent 存在性
        parent = fm.get("parent", "")
        if parent and parent not in titles:
            errors.append(f"E105 parent '{parent}' 不存在于任何页面 title")

    # --- E104 Liquid ---
    for i, ln in enumerate(body, start=1):
        if LIQUID_RE.search(ln):
            errors.append(f"E104 Liquid 冲突 第{i}行: {ln.strip()[:60]}")

    # --- E103 相对链接 ---
    file_dir = os.path.dirname(path)
    for i, ln in enumerate(body, start=1):
        for _, target in LINK_RE.findall(ln):
            if target.startswith(("http://", "https://", "mailto:", "#", "data:")):
                continue
            t = target.split("#")[0].strip()
            if not t:
                continue
            full = os.path.normpath(os.path.join(file_dir, t))
            if not os.path.exists(full):
                errors.append(f"E103 链接失效 第{i}行: {target}")

    # --- W201 层级跳跃 ---
    prev_level, in_fence = 0, False
    has_h1 = False
    for ln in body:
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(ln)
        if not m:
            continue
        level = len(m.group(1))
        if level == 1:
            has_h1 = True
        if prev_level and level > prev_level + 1:
            warnings.append(f"W201 层级跳跃 H{prev_level}→H{level}: {ln.strip()[:40]}")
        prev_level = level
    if require_fm and not has_h1:
        warnings.append("W202 无 H1（页面标题由 front matter title 渲染）")

    # --- W203 连续空行 ---
    blank = 0
    for ln in body:
        blank = blank + 1 if not ln.strip() else 0
        if blank > 2:
            warnings.append("W203 存在超过 2 行的连续空行")
            break

    return errors, warnings


def collect_titles(docs_dir: str) -> Dict[str, str]:
    """收集全部 docs 内页面的 title → 相对路径。"""
    titles = {}
    for root, _, files in os.walk(docs_dir):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(root, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    fm, _ = parse_front_matter(f.read().splitlines())
                t = fm.get("title", "")
                if t:
                    titles[t] = os.path.relpath(p, ".").replace(os.sep, "/")
            except (OSError, UnicodeDecodeError):
                pass
    return titles


def main():
    ap = argparse.ArgumentParser(description="docs Markdown lint")
    ap.add_argument("--docs", default=DEFAULT_DOCS_DIR, help="docs 目录（默认 docs）")
    ap.add_argument("-v", "--verbose", action="store_true", help="显示通过项")
    args = ap.parse_args()

    if not os.path.isdir(args.docs):
        print(f"[lint] 目录不存在: {args.docs}")
        return 2

    titles = collect_titles(args.docs)

    doc_files = []
    for root, _, files in os.walk(args.docs):
        for fn in sorted(files):
            if fn.endswith(".md"):
                doc_files.append(os.path.join(root, fn))
    doc_files.sort()

    total_e = total_w = 0
    print(f"[lint] 检查 {len(doc_files)} 个 docs 文档 + {len(ROOT_FILES)} 个根文件\n")

    for p in doc_files:
        rel = p.replace(os.sep, "/")
        errs, warns = lint_file(p, require_fm=True, titles=titles)
        total_e += len(errs)
        total_w += len(warns)
        status = "✅" if not errs else "❌"
        print(f"  {status} {rel}" + (f"  ({len(errs)}E/{len(warns)}W)" if (errs or warns) else ""))
        for e in errs:
            print(f"       {e}")
        if args.verbose:
            for w in warns:
                print(f"       {w}")

    print()
    for rf in ROOT_FILES:
        if not os.path.exists(rf):
            continue
        errs, warns = lint_file(rf, require_fm=False, titles=titles)
        total_e += len(errs)
        total_w += len(warns)
        status = "✅" if not errs else "❌"
        print(f"  {status} {rf}" + (f"  ({len(errs)}E/{len(warns)}W)" if (errs or warns) else ""))
        for e in errs:
            print(f"       {e}")
        if args.verbose:
            for w in warns:
                print(f"       {w}")

    print(f"\n[lint] 完成：{total_e} errors / {total_w} warnings")
    return 1 if total_e else 0


if __name__ == "__main__":
    sys.exit(main())
