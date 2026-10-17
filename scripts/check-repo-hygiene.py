#!/usr/bin/env python3
"""仓库卫生自检：Agent 生成目录、构建产物、build.sh 规范。

这三类问题共享同一个特征：**不做也不会报错**。
`.workbuddy/` 混进版本库不会让 CI 变红，只会让别人的 clone 里多出一堆自己用不上的
会话记忆；`coverage.out` 被跟踪后，每次本地跑测试都会产生一条「已修改」的假改动；
`build.sh` 各写一套方言，换台机器就得重读一遍——而且这些都没人会在 review 时拦。

判据见 docs/repo-baseline.md 第六节（忽略规则）与 docs/build-script.md（构建脚本）。

用法：
    python3 scripts/check-repo-hygiene.py             # 全部自研仓库
    python3 scripts/check-repo-hygiene.py shenshi     # 只看指定仓库
    python3 scripts/check-repo-hygiene.py --all       # 含 fork
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent

# 复用 check-repo-baseline.py 的仓库表（含 fork 标记），避免两处名单漂移。
# 文件名带连字符，不能用普通 import，走 spec 加载。
_spec = importlib.util.spec_from_file_location(
    "check_repo_baseline", pathlib.Path(__file__).with_name("check-repo-baseline.py")
)
_baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_baseline)
REPOS: dict[str, dict] = _baseline.REPOS

# AI Agent 工具落盘的目录名（目录级，带尾斜杠）。Trae 的实名是 `.trae`；
# `.trea` 只是防手滑拼错的兜底，真实磁盘上不存在。
AGENT_DIRS = [
    ".workbuddy",
    ".trae",
    ".trea",
    ".qoder",
    ".opencode",
    ".cursor",
    ".claude",
    ".codex",
    ".gemini",
    ".continue",
    ".windsurf",
    ".aider",
]
# .gitignore 里必须出现的四条（其余按需）：最常用 + 最容易漏 + 曾误入库过
REQUIRED_IGNORES = [".workbuddy/", ".trae/", ".qoder/", ".DS_Store"]
# 不该被跟踪的构建/包管理产物
TRACKED_ARTIFACT_PATTERNS = [
    ("coverage.out", "覆盖率产物，每次跑测试都变"),
    ("package-lock.json", "npm 锁文件，与 pnpm-lock.yaml 冲突"),
    ("yarn.lock", "yarn 锁文件，同上"),
    ("bun.lockb", "bun 锁文件，同上"),
]
# build.sh 的硬约定（见 docs/build-script.md 第二节）
BUILD_PATHS = ["scripts/build.sh", "web/build.sh"]
# 格式基线（见 docs/code-style.md）：前两个全仓都要，第三个只 Go 仓要
FORMAT_FILES = [".editorconfig", ".gitattributes"]
GO_FORMAT_FILE = ".golangci.yml"
# Makefile 必备目标（见 docs/makefile.md）。docker 可豁免（无 Dockerfile 的 CLI 仓）。
MAKE_TARGETS = ["build", "dev", "test", "lint", "fmt", "docker", "clean"]
MAKE_TARGETS_NO_DOCKER = [t for t in MAKE_TARGETS if t != "docker"]
# 本就没有 docker 目标的仓库：没有 Dockerfile，发布走 Release 二进制
NO_DOCKER = {"bili-dl"}

# 已登记例外：理由写在这里，脚本打印成「已登记」而不是沉默跳过。
EXEMPT: dict[str, str] = {
    "ntfy": (
        "上游跟踪 fork：完整卫生块不套用（每次 merge 上游都会冲突），"
        "只加了最小必要规则 `.workbuddy/`；build.sh 与 codecov 按上游形态保留"
    ),
}


def git(repo: pathlib.Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def check_agent_tracked(repo: pathlib.Path) -> list[str]:
    """被跟踪的 Agent 目录文件（ignore 对已入库文件无效，必须摘索引）。"""
    tracked = git(repo, "ls-files").splitlines()
    hits = []
    for line in tracked:
        top = line.split("/", 1)[0]
        if top in AGENT_DIRS:
            hits.append(line)
    return hits


def check_ignore_block(repo: pathlib.Path) -> list[str]:
    gi = repo / ".gitignore"
    if not gi.is_file():
        return ["缺 .gitignore"]
    text = gi.read_text(encoding="utf-8", errors="replace")
    problems = [f"缺规则 {pat}" for pat in REQUIRED_IGNORES if pat not in text]
    # 有 Makefile 且 BIN 指向 bin/ 的仓，没忽略的话构建一次就多出一堆未跟踪文件。
    # 用 `BIN := bin/...` 这一行作探针：RSSRob 的 `$(VENV)/bin/python` 也会含 "bin/"，
    # 直接搜子串会误判成构建产物。
    mf = repo / "Makefile"
    if mf.is_file() and re.search(r"(?m)^\s*BIN\s*:?=\s*bin/", mf.read_text(encoding="utf-8", errors="replace")) \
            and not re.search(r"(?m)^/?bin/?$", text):
        problems.append("缺规则 bin/（Makefile 的 build 产出 bin/<app>）")
    return problems


def check_artifacts(repo: pathlib.Path) -> list[str]:
    tracked = set(git(repo, "ls-files").splitlines())
    out = []
    for name, why in TRACKED_ARTIFACT_PATTERNS:
        for path in tracked:
            if path == name or path.endswith("/" + name):
                out.append(f"{path}（{why}）")
    return out


def check_build_script(repo: pathlib.Path) -> tuple[str, list[str]]:
    """返回 (是否适用, 问题列表)。"""
    script = next((repo / p for p in BUILD_PATHS if (repo / p).is_file()), None)
    if script is None:
        return False, []
    text = script.read_text(encoding="utf-8", errors="replace")
    rel = script.relative_to(repo)
    problems = []
    first = text.splitlines()[0].strip() if text.strip() else ""
    if first != "#!/usr/bin/env bash":
        problems.append(f"{rel}: 首行应为 #!/usr/bin/env bash（现为 {first or '空'}）")
    if "set -euo pipefail" not in text:
        problems.append(f"{rel}: 缺 set -euo pipefail")
    if 'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."' not in text:
        problems.append(f"{rel}: 缺 ROOT 定位（cd 到仓库根）")
    if 'cd "$ROOT"' not in text:
        problems.append(f"{rel}: 缺 cd \"$ROOT\"")
    for fn in ("step()", "die()"):
        if fn not in text:
            problems.append(f"{rel}: 缺 {fn} 日志函数")
    return True, problems


def check_format_baseline(repo: pathlib.Path) -> list[str]:
    """三份格式基线文件是否就位（内容一致性交给 sync-format-baseline.py --check）。"""
    problems = [f"缺 {name}" for name in FORMAT_FILES if not (repo / name).is_file()]
    if (repo / "go.mod").is_file() and not (repo / GO_FORMAT_FILE).is_file():
        problems.append(f"缺 {GO_FORMAT_FILE}（Go 仓）")
    return problems


def check_makefile(repo: pathlib.Path, name: str) -> list[str]:
    """七个必备目标是否齐备（内容不比对，各仓命令本来就不同）。"""
    mf = repo / "Makefile"
    if not mf.is_file():
        return ["缺 Makefile"]
    text = mf.read_text(encoding="utf-8", errors="replace")
    required = MAKE_TARGETS_NO_DOCKER if name in NO_DOCKER else MAKE_TARGETS
    missing = []
    for t in required:
        # 目标可能写成 `build:` 或 `build: deps`，也可能出现在合并目标里 `a b:`
        if not re.search(rf"(?m)^(?:[A-Za-z0-9_.%-]+\s+)*{re.escape(t)}\s*:", text):
            missing.append(t)
    if missing:
        return [f"缺目标：{'、'.join(missing)}"]
    if ".PHONY" not in text:
        return ["缺 .PHONY（目标名撞上同名目录时会被当成已最新而跳过）"]
    return []


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--all"]
    show_all = "--all" in sys.argv
    names = args or [n for n, cfg in REPOS.items() if show_all or not cfg.get("fork")]

    rows = []
    detail: dict[str, list[str]] = {}
    for name in names:
        cfg = REPOS.get(name)
        if cfg is None:
            print(f"未登记的仓库：{name}", file=sys.stderr)
            continue
        if name in EXEMPT:
            rows.append((name, "登记", "登记", "登记", "登记", "登记", "登记"))
            detail[name] = [f"[例外] {EXEMPT[name]}"]
            continue
        repo = WORKSPACE / name
        if not (repo / ".git").exists():
            continue
        agent = check_agent_tracked(repo)
        ignore = check_ignore_block(repo)
        arts = check_artifacts(repo)
        applies, build = check_build_script(repo)
        fmt = check_format_baseline(repo)
        make = check_makefile(repo, name)

        rows.append((
            name,
            "NG" if agent else "OK",
            "NG" if ignore else "OK",
            "NG" if arts else "OK",
            "NG" if fmt else "OK",
            "NG" if make else "OK",
            ("NG" if build else "OK") if applies else "--",
        ))
        msgs = []
        msgs += [f"[Agent] 已被跟踪：{p}（git rm -r --cached 摘除索引）" for p in agent[:6]]
        if len(agent) > 6:
            msgs.append(f"[Agent] 另有 {len(agent) - 6} 个文件同样被跟踪")
        msgs += [f"[ignore] {m}" for m in ignore]
        msgs += [f"[产物] 已被跟踪：{m}" for m in arts]
        msgs += [f"[格式] {m}（跑 scripts/sync-format-baseline.py 分发）" for m in fmt]
        msgs += [f"[Makefile] {m}（契约见 docs/makefile.md）" for m in make]
        msgs += [f"[build] {m}" for m in build]
        if msgs:
            detail[name] = msgs

    head = f"{'仓库':<24}{'Agent':<8}{'ignore':<10}{'产物':<8}{'格式':<8}{'Make':<8}{'build.sh':<10}"
    rule = "=" * 92
    print(rule)
    print("仓库卫生自检（契约见 docs/repo-baseline.md 第六节 / docs/build-script.md / docs/code-style.md / docs/makefile.md）")
    print(rule)
    print(head)
    print("-" * 92)
    for r in rows:
        print(f"{r[0]:<24}{r[1]:<8}{r[2]:<10}{r[3]:<8}{r[4]:<8}{r[5]:<8}{r[6]:<10}")

    if detail:
        print("\n明细")
        print("-" * 92)
        for name, msgs in detail.items():
            print(f"\n{name}")
            for m in msgs:
                print(f"    - {m}")

    ng = sum(1 for r in rows if "NG" in r[1:])
    print("-" * 92)
    print(f"共 {len(rows)} 个仓库，{len(rows) - ng} 个全绿，{ng} 个待修。")
    print("fork / 第三方仓库默认跳过（--all 可见）。")
    return 0 if ng == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
