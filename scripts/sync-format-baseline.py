#!/usr/bin/env python3
"""把 templates/ 下的三份基线文件分发到工作区各仓。

契约见 docs/code-style.md。三份文件内容逐字节相同，各仓不自持副本，
要改就改本仓 templates/ 再跑这个脚本。

用法：
    python3 scripts/sync-format-baseline.py            # 写入/覆盖
    python3 scripts/sync-format-baseline.py --check    # 只比对，不一致则退出码 1
    python3 scripts/sync-format-baseline.py shenshi    # 只处理指定仓库
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # Felix2yu/.github
WORKSPACE = ROOT.parent                 # 工作区根
TEMPLATES = ROOT / "templates"

# 目标文件名 -> 模板文件名
TARGETS = {
    ".editorconfig": "editorconfig",
    ".gitattributes": "gitattributes",
}
GO_TARGET = (".golangci.yml", "golangci.yml")


def load_registry() -> dict[str, dict]:
    """复用 check-repo-baseline.py 里的仓库清单，避免两处维护。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "baseline", HERE / "check-repo-baseline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.REPOS


def write(repo: Path, name: str, content: str, check: bool) -> tuple[str, str]:
    """返回 (状态, 说明)。状态：ok / drift / created"""
    path = repo / name
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return "ok", name
        if check:
            return "drift", name
        path.write_text(content, encoding="utf-8")
        return "drift", name
    if check:
        return "missing", name
    path.write_text(content, encoding="utf-8")
    return "created", name


def main() -> int:
    check = "--check" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    repos = load_registry()

    templates = {
        name: (TEMPLATES / fname).read_text(encoding="utf-8")
        for name, fname in TARGETS.items()
    }
    go_template = (TEMPLATES / GO_TARGET[1]).read_text(encoding="utf-8")

    changed: list[str] = []
    for name, cfg in sorted(repos.items()):
        if only and name not in only:
            continue
        if cfg.get("fork"):
            continue
        repo = WORKSPACE / name
        if not (repo / ".git").exists():
            continue

        files = dict(templates)
        if (repo / "go.mod").exists():
            files[GO_TARGET[0]] = go_template

        for fname, content in files.items():
            status, _ = write(repo, fname, content, check)
            if status != "ok":
                changed.append(f"{name}/{fname} ({status})")

    if changed:
        verb = "不一致" if check else "已写入"
        print(f"{verb} {len(changed)} 处：")
        for line in changed:
            print(f"  {line}")
        return 1 if check else 0
    print("全部与模板一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
