#!/usr/bin/env python3
"""校验调用方 ci.yml 传给 reusable workflow 的 input 名是否都已声明。

为什么需要它：GitHub 对「传了被调 workflow 未声明的 input」直接报 startup_failure，
没有 job、没有日志，只看得到 conclusion。而 actionlint 只能校验本仓库内的
表达式类型，推不出远端 reusable workflow 的 input 契约。
"""
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHARED = ROOT / ".github" / "workflows"
# 共享仓库在 <workspace>/.github，调用方仓库与它平级
WORKSPACE = ROOT.parent
# 允许指定若干调用方仓库；默认校验全部已接入的仓库
targets = sys.argv[1:] or sorted(
    p.parent.parent.parent.name for p in WORKSPACE.glob("*/.github/workflows/ci.yml")
)

fails = 0
for repo in targets:
    caller_path = WORKSPACE / repo / ".github" / "workflows" / "ci.yml"
    if not caller_path.exists():
        continue
    caller = yaml.safe_load(caller_path.read_text())
    for job_name, job in (caller.get("jobs") or {}).items():
        uses = job.get("uses") or ""
        m = re.match(r"Felix2yu/\.github/\.github/workflows/(.+?\.yml)@", uses)
        if not m:
            continue
        wf_name = m.group(1)
        wf_path = SHARED / wf_name
        if not wf_path.exists():
            print(f"[{repo}:{job_name}] 引用的 {wf_name} 在共享仓库中不存在")
            fails += 1
            continue
        wf = yaml.safe_load(wf_path.read_text())
        declared = set(
            ((wf.get(True) or wf.get("on") or {}).get("workflow_call", {}).get("inputs") or {})
        )
        used = set((job.get("with") or {}))
        unknown = used - declared
        unused_hint = declared - used
        status = "OK " if not unknown else "FAIL"
        if unknown:
            fails += 1
        print(f"[{status}] {repo}:{job_name} → {wf_name}  声明 {len(declared)} / 传入 {len(used)}")
        if unknown:
            print(f"         未声明的 input（会导致 startup_failure）: {sorted(unknown)}")
        # 未传的 input 只要在被调workflow 里声明过（就必然有 default），否则 GitHub 本身就会校验失败
        if unused_hint:
            print(f"         未传（走默认）: {sorted(unused_hint)}")

print("\n全部通过" if not fails else f"\n{fails} 处不一致")
sys.exit(1 if fails else 0)