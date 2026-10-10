#!/usr/bin/env python3
"""按 docs/cpu-baseline.md，自检各仓库 linux/amd64 产物的 CPU 指令集基线。

为什么需要它：ntfy 曾发布过一份「在没AVX 的服务器上启动即 SIGILL」的产物。根因
不是代码，而是构建环境——GitHub 的 ubuntu-26.04 runner 的 apt 源与系统 glibc 是
amd64v3（x86-64-v3 / AVX2）变体，CGO 静态链接会把带 AVX2 的 crt1.o / libc.a 直接
烧进二进制，编译与测试全程绿灯，直到在老 CPU 上第一次运行才崩（exit 132，且早于
任何日志输出）。

这类缺陷的特征和 PWA 图标、dependabot 限流一样：构建不会报错、测试不会发现，
只在别人的机器上炸。所以只能靠「扫产物」把它变成红灯。

本脚本做两件事：

1. 配置面（默认，无需编译）：核对每个仓的 cgo 策略。CGO 产物必须走固定基线
   容器（golang:*-trixie / *-bookworm，gcc -march=x86-64），而不是裸 runner；
   CGO_ENABLED=0 的纯 Go 路径则天然免疫。
2. 产物面（--binary <路径> 或 --build）：真的 objdump 一遍产物，统计 ymm/zmm
   指令数与 INTERP 段，并按「门控型 / 无门控型」区分。

关于判定阈值的两个要点（都是实测得来的，不是拍的）：

- Go 标准库自带大量 AVX2/AVX-512 汇编（runtime.memmove、crypto/sha256.blockAVX2、
  internal/runtime/gc/scan.*AVX512 等），但它们全部经internal/cpu.X86 的
  HasAVX2/HasAVX512 门控，无 AVX 的 CPU 上不会进入。因此纯 Go 产物的 ymm 计数是
  一个**固定的基线值**，不等于「有风险」。实测 12 个 CGO_ENABLED=0 的仓里，8 个
  落在 3523~4043 / 496。

- 真正危险的是**无门控**的AVX：编译工具链选错（-march=native / amd64v3 基线的
  glibc），或第三方预编译静态库只编了一份 AVX2 实现而没有 SSE 对照版与 CPUID
  分发。mujian 经avif-go 链入的 libsvtav1.a/libdav1d.a 就是后者——含 17万+ ymm
  指令，好在其中有 4580 对AVX2/SSE4.1 成对实现 + svt_aom_get_cpu_flags 分发，
  属于门控型；但这类依赖一旦上游换了构建参数就会静默退化成无门控，所以要持续扫。

因此脚本不把「有 ymm」当失败，而是看**是否超出该仓已登记的基线**：超出说明产物
的指令集来源变了（新依赖 / 新工具链），必须人工确认门控是否还在。

用法：
    python3 scripts/check-cpu-baseline.py              # 配置面 + 已登记基线
    python3 scripts/check-cpu-baseline.py shenshi       # 只看指定仓库
    python3 scripts/check-cpu-baseline.py --build       # 现场交叉编译再扫（较慢）
    python3 scripts/check-cpu-baseline.py --bin-dir dist # 扫已有产物目录
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
GO = shutil.which("go") or "/opt/homebrew/bin/go"

# ---------------------------------------------------------------- 配置

# go_dir:  含 go.mod 的目录（相对仓库根）；main_pkg: 主包路径
# cgo:     该仓在 reusable-release.yml / reusable-image.yml 里的 cgo 策略
# baseline: 该仓已实测的 ymm/zmm 计数（纯 Go 路径下由 Go 标准库决定，与依赖无关）
#
# baseline 登记规则：
#   - CGO_ENABLED=0：全工作区共享同一份 Go 标准库基线，实测稳定在 3523~4043 / 496；
#     差异来自各仓自身依赖里的 SIMD 实现（如 tuqie 的 gen2brain AVX-512）。
#   - CGO 路径：基线由第三方静态库主导，量级完全不同，单独登记。
REPOS: dict[str, dict] = {
    "qiansi": dict(go_dir=".", main_pkg="./cmd/server", cgo="off", baseline=(4043, 496)),
    "shenshi": dict(go_dir="server", main_pkg=".", cgo="off", baseline=(4043, 496)),
    "liuxia": dict(go_dir=".", main_pkg=".", cgo="off", baseline=(4043, 496)),
    "yuexi": dict(go_dir=".", main_pkg=".", cgo="off", baseline=(4043, 496)),
    "chaxin": dict(go_dir=".", main_pkg="./cmd/server", cgo="off", baseline=(4043, 496)),
    "diarum": dict(go_dir=".", main_pkg=".", cgo="off", baseline=(3574, 496)),
    "bili-dl": dict(go_dir=".", main_pkg=".", cgo="off", baseline=(3523, 496)),
    "qbhive": dict(go_dir=".", main_pkg="./cmd/server", cgo="off", baseline=(6737, 496)),
    "qingye": dict(go_dir="server", main_pkg=".", cgo="off", baseline=(6737, 496)),
    "tuqie": dict(
        go_dir=".", main_pkg="./cmd/tuqie", cgo="off", tags="-tags nodynamic",
        baseline=(6965, 823),
        note="gen2brain 的 h265/jxl 自带 Go 汇编 AVX-512 实现（经 if hasAVX512 门控）",
    ),
    # ntfy：mattn/go-sqlite3 需要 CGO，走完全静态 + golang:1.26-trixie 固定基线容器。
    "ntfy": dict(
        go_dir=".", main_pkg=".", cgo="static",
        tags="-tags sqlite_omit_load_extension,osusergo,netgo",
        note="CGO 静态链接。裸 runner 的 glibc 是 amd64v3 变体，会把 AVX2 烧进产物",
    ),
    # mujian：CGO glibc + avif-go 的预编译静态库（libsvtav1/libdav1d 含 AVX2/AVX-512）。
    # 基线取自已发布的 v1.1.0 linux/amd64 产物（INTERP 无 = glibc 动态依赖在运行时解析，
    # 指令集不烧进产物）。门控证据：svt_aom_get_cpu_flags / dav1d_get_cpu_flags_x86。
    "mujian": dict(
        go_dir="backend", main_pkg=".", cgo="glibc",
        baseline=(228561, 85771),
        note="avif-go 链入预编译静态库，指令集来源在依赖而不在本仓工具链",
    ),
}

# CGO 路径必须使用的固定基线容器。它们的 gcc -march=x86-64，不引入 AVX；
# AVX 只存在于 IFUNC 多版本 memcpy/strlen，运行时 CPUID 探测后才进入。
BASELINE_CONTAINERS = ("golang:1.26-trixie", "golang:1.27-bookworm")

# 裸 runner 直编 CGO 时的危险基线（amd64v3 = x86-64-v3，AVX2）。
AMD64V3 = re.compile(r"amd64v3|x86-64-v3", re.I)


# ---------------------------------------------------------------- 工具


def read(rel: str) -> str:
    p = WORKSPACE / rel
    return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""


def ci_cgo_strategy(repo: str) -> tuple[str, str]:
    """从仓库自己的 ci.yml 里读 cgo 策略（release 与 test job 可能不同，取并集）。"""
    text = read(f"{repo}/.github/workflows/ci.yml")
    vals = set(re.findall(r"^\s*cgo:\s*'?([a-z]+)'?", text, re.M))
    return ",".join(sorted(vals)) or "off", text


def reusable_cgo_blocks() -> str:
    """reusable-release / reusable-image 里所有 CGO 构建分支的文本。"""
    parts = []
    for f in ("reusable-release.yml", "reusable-image.yml"):
        parts.append(read(f".github/.github/workflows/{f}"))
    return "\n".join(parts)


def objdump(path: pathlib.Path) -> dict:
    """统计产物的 ymm / zmm 指令数与 INTERP 段。"""
    if shutil.which("objdump") is None:
        return {"error": "本机缺 objdump（binutils），无法扫描产物"}
    res: dict = {}
    try:
        dis = subprocess.run(
            ["objdump", "-d", str(path)],
            capture_output=True, text=True, timeout=600,
        ).stdout
        res["ymm"] = sum(1 for line in dis.splitlines() if "ymm" in line)
        res["zmm"] = sum(1 for line in dis.splitlines() if "zmm" in line)
    except subprocess.TimeoutExpired:
        res["error"] = "objdump -d 超时（产物过大？）"
        return res
    try:
        ph = subprocess.run(
            ["objdump", "-l", str(path)],
            capture_output=True, text=True, timeout=300,
        ).stdout
        res["interp"] = 1 if "INTERP" in ph else 0
    except subprocess.TimeoutExpired:
        res["interp"] = -1
    # 有门控的证据：Go 标准库与常见 SIMD 库在无 AVX CPU 上靠 CPUID 分发，
    # 二进制里会留下特性名/分发函数名。
    raw = path.read_bytes()
    try:
        blob = raw.decode("latin-1")
    except Exception:
        blob = ""
    res["gated_hint"] = sum(
        blob.count(k) for k in (
            "HasAVX2", "HasAVX512", "useAVX2", "useAVX",
            "blockAVX2", "get_cpu_flags", "hasAVX2",
        )
    )
    return res


def build(repo: str, cfg: dict, outdir: pathlib.Path) -> pathlib.Path | None:
    """按 CI 的方式交叉编译 linux/amd64。CGO 仓本地编不了（缺交叉工具链）。"""
    if cfg["cgo"] != "off":
        return None
    wd = WORKSPACE / repo / cfg["go_dir"]
    if not (wd / "go.mod").is_file():
        return None
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{repo}-linux-amd64"
    cmd = [GO, "build", "-trimpath"]
    if cfg.get("tags"):
        cmd += cfg["tags"].split()
    cmd += ["-o", str(out), cfg["main_pkg"]]
    env = {"CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "amd64"}
    proc = subprocess.run(
        cmd, cwd=wd, capture_output=True, text=True, timeout=1800,
        env={**__import__("os").environ, **env},
    )
    if proc.returncode != 0 or not out.is_file():
        return None
    return out


# ---------------------------------------------------------------- 检查项


def check_config(repo: str, cfg: dict, reusable: str) -> list[str]:
    """配置面：CGO 仓必须走固定基线容器，且不得出现 amd64v3 基线。"""
    notes: list[str] = []
    strategy, own_ci = ci_cgo_strategy(repo)
    declared = cfg["cgo"]
    if strategy != declared and declared not in strategy.split(","):
        notes.append(
            f"[配置] ci.yml 声明 cgo={strategy}，与本脚本登记的 {declared} 不一致，请核对"
        )
    if declared == "off":
        if "CGO_ENABLED=1" in own_ci:
            notes.append("[配置] 声明 cgo=off，但 ci.yml 里出现 CGO_ENABLED=1")
        return notes

    # CGO 路径：确认 reusable 里的 CGO 分支用的是固定基线容器
    branches = [b for b in reusable.split("Build (") if "CGO_ENABLED=1" in b]
    if not branches:
        notes.append("[配置] reusable workflow 里找不到 CGO_ENABLED=1 的构建分支，需人工确认")
        return notes
    for b in branches:
        if not any(c in b for c in BASELINE_CONTAINERS):
            first = next(
                (l.strip() for l in b.splitlines() if "go build" in l or "docker run" in l), ""
            )
            notes.append(
                f"[配置] CGO 分支未在固定基线容器里编译（期望 {' / '.join(BASELINE_CONTAINERS)}）：{first}"
            )
    if AMD64V3.search(reusable):
        for line in reusable.splitlines():
            if AMD64V3.search(line) and not line.lstrip().startswith("#"):
                notes.append(f"[配置] 非注释行出现 amd64v3 基线：{line.strip()}")
                break
    return notes


def check_binary(repo: str, cfg: dict, res: dict, declared: str) -> list[str]:
    """产物面：与登记基线比对。超出基线 = 指令集来源变了，必须人工确认门控。"""
    notes: list[str] = []
    if res.get("error"):
        return [f"[产物] {res['error']}"]
    base = cfg.get("baseline")
    if base and res["ymm"] > base[0] * 1.05:
        notes.append(
            f"[产物] ymm={res['ymm']} 超出登记基线 {base[0]}（+{(res['ymm']/base[0]-1)*100:.0f}%）："
            f"指令集来源已变（多为新依赖），必须确认新增部分是 CPUID 门控的"
        )
    if base and res["zmm"] > base[1] * 1.10:
        notes.append(
            f"[产物] zmm={res['zmm']} 超出登记基线 {base[1]}：可能新增 AVX-512 实现，确认门控"
        )
    if declared == "off" and res["interp"] == 1:
        notes.append(
            "[产物] cgo=off 却带 INTERP 段：多半是依赖里 //go:linkname 拽进了 libc，"
            "这类符号可能来自宿主机 glibc（若 runner 是 amd64v3 则含 AVX）"
        )
    if res["ymm"] > 20000 and res["gated_hint"] == 0:
        notes.append(
            f"[产物] ymm={res['ymm']} 且找不到任何 CPUID 门控符号：高度怀疑无门控 AVX，"
            "在老 CPU 上会 SIGILL"
        )
    # CGO 静态产物：指令集被构建期固化，运行时无法退化，最需要盯紧门控。
    if declared == "static" and res["ymm"] > 0 and res["gated_hint"] == 0:
        notes.append(
            "[产物] CGO 静态产物含 ymm 且无门控符号：静态链接会把构建机指令集固化进产物，"
            "必须确认编译容器是固定基线（golang:*-trixie / *-bookworm）"
        )
    return notes


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", help="只看指定仓库")
    ap.add_argument("--build", action="store_true", help="现场交叉编译再扫（较慢）")
    ap.add_argument("--bin-dir", help="扫已有产物目录（文件名形如 <repo>-linux-amd64）")
    args = ap.parse_args()

    repos = [args.repo] if args.repo else list(REPOS)
    unknown = [r for r in repos if r not in REPOS]
    if unknown:
        print(f"未知仓库：{unknown}；已登记：{sorted(REPOS)}")
        return 2

    reusable = reusable_cargo = reusable_cgo_blocks()
    bin_dir = pathlib.Path(args.bin_dir) if args.bin_dir else None
    outdir = pathlib.Path("/tmp/cpu-baseline-build")
    rows: list[dict] = []
    bad = 0

    for repo in repos:
        cfg = REPOS[repo]
        declared = cfg["cgo"]
        notes = check_config(repo, cfg, reusable)
        row = {
            "repo": repo,
            "cgo": declared,
            "ymm": None,
            "zmm": None,
            "interp": None,
            "notes": list(notes),
        }

        binary: pathlib.Path | None = None
        if bin_dir and (bin_dir / f"{repo}-linux-amd64").is_file():
            binary = bin_dir / f"{repo}-linux-amd64"
        elif args.build:
            binary = build(repo, cfg, outdir)
            if binary is None and declared == "off":
                row["notes"].append("[产物] 本地交叉编译失败（go-dir / main_pkg 可能已变）")

        if binary:
            res = objdump(binary)
            row["ymm"], row["zmm"], row["interp"] = (
                res.get("ymm"), res.get("zmm"), res.get("interp"),
            )
            row["notes"] += check_binary(repo, cfg, res, declared)
        elif not args.build and not bin_dir:
            row["notes"].append("[产物] 未扫描（加 --build 或 --bin-dir）")

        rows.append(row)
        if row["notes"]:
            bad += 1

    # 报告
    w = max(len(r["repo"]) for r in rows)
    print(f"{'仓库':<{w}}  {'cgo':<7} {'ymm':>8} {'zmm':>6} {'INTERP':>6}")
    print("-" * (w + 34))
    for r in rows:
        ymm = r["ymm"] if r["ymm"] is not None else "-"
        zmm = r["zmm"] if r["zmm"] is not None else "-"
        itp = {1: "有", 0: "无", None: "-", -1: "?"}[r["interp"]]
        print(f"{r['repo']:<{w}}  {r['cgo']:<7} {ymm:>8} {zmm:>6} {itp:>6}")
        for n in r["notes"]:
            print(f"    {n}")

    print()
    print(f"共 {len(rows)} 个仓库，{len(rows) - bad} 个无提示")
    print()
    print("说明：cgo=off 的 ymm 非 0 是正常的——Go 标准库自带 AVX2/AVX-512 汇编")
    print("（runtime.memmove、crypto/sha256.blockAVX512 等），但全部经")
    print("internal/cpu.X86 的 HasAVX2/HasAVX512 门控，无 AVX 的 CPU 不会进入。")
    print("真正会 SIGILL 的是「无门控 AVX」，本脚本按超出基线来提示，而非按 ymm>0。")
    return 1 if any(
        n.startswith("[产物]") and "未扫描" not in n for r in rows for n in r["notes"]
    ) else 0


if __name__ == "__main__":
    sys.exit(main())