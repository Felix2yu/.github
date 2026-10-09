#!/usr/bin/env python3
"""按 docs/repo-baseline.md，自检每个仓库的「基础设施四件套」是否到位。

为什么需要它：一个仓库能不能被别人接手、能不能被 CI 正确地跑，取决于四件
与业务代码无关的事——文档、依赖更新配置、CI 接入、前端工具链声明。它们全都
「不写也不会报错」：没有 dependabot.yml，依赖就永远不更新，直到某天 CVE 上门；
缺 .nvmrc，CI 就会自己猜一个 Node 版本，猜错了还是绿的；没有 LICENSE，别人想
复用也得先发邮件问你。这些都是典型的「静默缺陷」——不红、不崩、随时间腐朽。

脚本把它们变成一张表，新仓库接入时照着勾，旧仓库可以一眼看到欠了哪几项。

用法：
    python3 scripts/check-repo-baseline.py                # 全部仓库
    python3 scripts/check-repo-baseline.py shenshi        # 只看指定仓库
    python3 scripts/check-repo-baseline.py --all          # 含明显是 fork 的仓库
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent

# ---------------------------------------------------------------- 配置

# lang: go / python —— 决定是否要求 gomod / pip 的 dependabot entry
# web:  前端根目录，None = 无前端；npm=False 表示前端不走 npm（chaxin 用 shell 构建）
# exempt: 不检查的项（写明理由）
REPOS: dict[str, dict] = {
    "shenshi": dict(lang="go", web="web"),
    "qiansi": dict(lang="go", web="web"),
    "mujian": dict(lang="go", web="frontend"),
    "huozhi": dict(lang="go", web="frontend"),
    "qingye": dict(lang="go", web="web"),
    "diarum": dict(lang="go", web="site"),
    "bili-history": dict(lang="go", web="frontend"),
    "qbhive": dict(lang="go"),
    "chaxin": dict(lang="go", web="web", npm=False),
    "liuxia": dict(lang="go"),
    "yuexi": dict(lang="go"),
    "tuqie": dict(lang="go", web="web"),
    "bili-dl": dict(lang="go"),
    "docker-db-auto-backup": dict(lang="go"),
    "ntfy": dict(lang="go", web="web"),
    "WebMonitor": dict(lang="python", web="frontend"),
    "RSSRob": dict(lang="python"),
    # fork / 第三方：README 与 LICENSE 沿用上游，不参与本仓群契约
    "healthchecks": dict(lang="python", fork=True),
    "whoami": dict(lang="go", fork=True),
    "GoldPrice": dict(lang="python", fork=True),
    "ntfyx": dict(lang="go", fork=True),
}

DEPENDABOT_EXPECTED = {
    "go": "gomod",
    "python": "pip",
}
# 所有仓库都该有的：github-actions（reusable workflow 的引用不会被自动更新，
# 但 ci.yml 里其它 action 会——不配置就等于放任它们腐烂）
ALWAYS_EXPECTED = ["github-actions"]

PHP = None


def read(repo: str, rel: str) -> str | None:
    p = WORKSPACE / repo / rel
    return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else None


def exists(repo: str, rel: str) -> bool:
    return (WORKSPACE / repo / rel).exists()


# ---------------------------------------------------------------- 各项检查

def check_docs(repo: str, cfg: dict) -> tuple[bool, list[str]]:
    notes: list[str] = []
    if not exists(repo, "README.md"):
        notes.append("缺 README.md")
    if not cfg.get("fork") and not exists(repo, "LICENSE"):
        notes.append("缺 LICENSE（自研仓库请补 MIT 或你选定的协议）")
    if not notes:
        # README 里至少要有一段可用的「怎么跑起来」，否则等于没有
        body = read(repo, "README.md") or ""
        if len(body) < 400:
            notes.append(f"README 只有 {len(body)} 字节，可能没有安装/运行说明")
    return (not notes), notes


def check_dependabot(repo: str, cfg: dict) -> tuple[bool, list[str]]:
    text = read(repo, ".github/dependabot.yml")
    if text is None:
        return False, ["缺 .github/dependabot.yml（依赖不会自动更新）"]
    notes: list[str] = []
    ecos = set(re.findall(r'package-ecosystem\s*:\s*["\']?([a-z-]+)', text))
    dirs = set(re.findall(r'directory\s*:\s*["\']?([^"\'\n]+)', text))

    want = list(ALWAYS_EXPECTED)
    if cfg["lang"] in DEPENDABOT_EXPECTED:
        want.append(DEPENDABOT_EXPECTED[cfg["lang"]])
    if cfg.get("web") and cfg.get("npm", True):
        want.append("npm")
    if exists(repo, "Dockerfile") or exists(repo, "Dockerfile.slim"):
        want.append("docker")

    miss = [w for w in want if w not in ecos]
    if miss:
        notes.append(f"未覆盖的 ecosystem: {', '.join(miss)}")

    if "npm" in want and cfg.get("web"):
        if f"/{cfg['web']}" not in dirs:
            notes.append(f"npm entry 的 directory 未指向 /{cfg['web']}")

    if "open-pull-requests-limit" not in text:
        notes.append("未设 open-pull-requests-limit（默认 5，一周能攒满后静默停止提 PR）")
    if "groups" not in text:
        notes.append("未配 groups：patch/minor 会各自开一个 PR，一周能被刷屏")
    return (not notes), notes


def check_ci(repo: str, cfg: dict) -> tuple[bool, list[str]]:
    """CI 是否存在，以及 reusable workflow 的引用有没有 pin。"""
    wf_dir = WORKSPACE / repo / ".github/workflows"
    if not wf_dir.is_dir():
        return False, ["无 .github/workflows（未接入共享 CI）"]
    ymls = list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))
    if not ymls:
        return False, ["workflows 目录为空"]

    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in ymls)
    refs = re.findall(r"(Felix2yu/\.github/\.github/workflows/[\w-]+\.yml)@(\S+)", blob)
    notes: list[str] = []
    if not refs:
        return False, ["未调用 Felix2yu/.github 的 reusable workflow（仍是私有实现？）"]
    for name, ref in refs:
        ref = ref.strip().strip("'\"")
        if ref in ("main", "master"):
            notes.append(f"{name.split('/')[-1]} 引用了 @{ref} —— 移动分支，必须 pin 到 @v1 或 @vX.Y.Z")
    return (not notes), (notes or [])


def check_frontend(repo: str, cfg: dict) -> tuple[bool, list[str]] | None:
    web = cfg.get("web")
    if not web:
        return None
    notes: list[str] = []
    if not cfg.get("npm", True):
        return None      # 前端不走 npm（shell 手搓），三件套不适用

    if not exists(repo, ".nvmrc"):
        notes.append("缺仓库根 .nvmrc（CI 靠它定 Node 版本，没有就失败而非兜底）")
    pkg = read(repo, f"{web}/package.json") or ""
    m = re.search(r'"packageManager"\s*:\s*"([^"]+)"', pkg)
    if not m:
        notes.append(f"缺 {web}/package.json 的 packageManager 声明")
    elif not m.group(1).startswith("pnpm@"):
        notes.append(f"packageManager 为 {m.group(1)}，组织统一要求 pnpm@<版本>")
    if not exists(repo, f"{web}/pnpm-lock.yaml"):
        notes.append(f"缺 {web}/pnpm-lock.yaml（CI 用 --frozen-lockfile，缺会直接失败）")
    for stray in ("package-lock.json", "yarn.lock", "bun.lockb"):
        if exists(repo, f"{web}/{stray}"):
            notes.append(f"存在多余的 {web}/{stray}（会让包管理器识别产生歧义）")
    return (not notes), notes


def check_ignore(repo: str, cfg: dict) -> tuple[bool, list[str]]:
    text = read(repo, ".gitignore") or ""
    notes: list[str] = []
    if not text.strip():
        return False, ["缺 .gitignore"]
    web = cfg.get("web")
    if cfg.get("npm", True) and web and "node_modules" not in text:
        notes.append("未忽略 node_modules")
    if web and not re.search(r"\b(dist|build)\b", text):
        notes.append("未忽略前端产物 dist/ 或 build/")
    if cfg["lang"] == "go" and not re.search(r"\.exe$|/bin/|\bbinary\b", text, re.M):
        notes.append("未忽略 Go 编译产物（*.exe / bin/）")
    return (not notes), notes


# ---------------------------------------------------------------- 主流程

def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_all = "--all" in sys.argv
    pools = REPOS if not args else {k: v for k, v in REPOS.items() if k in args}
    if not show_all:
        pools = {k: v for k, v in pools.items() if not v.get("fork")}
    for u in [t for t in args if t not in REPOS]:
        print(f"[warn] 未登记的仓库 {u}，跳过")

    print("=" * 100)
    print("仓库基线自检（契约见 docs/repo-baseline.md）")
    print("=" * 100)
    print(f"{'仓库':<24}{'文档':<8}{'依赖更新':<10}{'CI接入':<10}{'前端三件套':<12}{'忽略规则':<10}")
    print("-" * 100)

    failed = 0
    details: list[tuple[str, list[str]]] = []
    for repo, cfg in pools.items():
        if not (WORKSPACE / repo).is_dir():
            print(f"{repo:<24}{'--':<8}{'--':<10}{'--':<10}{'--':<12}{'--':<10}")
            continue
        d_ok, d_notes = check_docs(repo, cfg)
        p_ok, p_notes = check_dependabot(repo, cfg)
        c_ok, c_notes = check_ci(repo, cfg)
        fe = check_frontend(repo, cfg)
        i_ok, i_notes = check_ignore(repo, cfg)

        fe_mark = "--" if fe is None else ("OK" if fe[0] else "NG")
        fe_notes = [] if fe is None else fe[1]

        ok = d_ok and p_ok and c_ok and i_ok and (fe is None or fe[0])
        failed += 0 if ok else 1
        print(f"{repo:<24}{'OK' if d_ok else 'NG':<8}{'OK' if p_ok else 'NG':<10}"
              f"{'OK' if c_ok else 'NG':<10}{fe_mark:<12}{'OK' if i_ok else 'NG':<10}")

        notes = ([f"[文档] {n}" for n in d_notes] + [f"[依赖更新] {n}" for n in p_notes]
                 + [f"[CI] {n}" for n in c_notes] + [f"[前端] {n}" for n in fe_notes]
                 + [f"[忽略] {n}" for n in i_notes])
        if notes:
            details.append((repo, notes))

    if details:
        print("\n明细")
        print("-" * 100)
        for repo, notes in details:
            print(f"\n{repo}")
            for n in notes:
                print(f"    - {n}")

    print("\n" + "-" * 100)
    print(f"共 {len(pools)} 个仓库，{len(pools) - failed} 个全绿，{failed} 个待修。")
    print("fork / 第三方仓库默认跳过（--all 可见）。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
