#!/usr/bin/env python3
"""按 docs/docker.md 与 docs/backup.md 的契约，自检工作区内各仓库的容器镜像。

为什么需要它：容器里的坑有一个共同点——本地跑得好好的，推上去才炸。
以 root 运行、`:latest` 裸标签、`.dockerignore` 排除了 CI 产物、`HEALTHCHECK`
探在需要鉴权的端点上——这些全都不会让 `docker build` 失败，CI 全程是绿的，
上线几周后才以「容器反复 unhealthy」「备份恢复后写不进数据库」的形式冒出来。
脚本把它们变成一条命令能看完的东西。

SQLite 自导出声明（docs/backup.md）同理：应用实现了 `backup` 子命令、compose 却
漏写 `BACKUP_SQLITE_CMD`，备份工具根本看不到这个容器——不报错、不告警，只是
每天少备份一个库。声明写错（裸名命令、EXT 与产物不符）则要到运行时才以
「产物丢弃、本轮 failed」的形式暴露。声明层面的对错在这里一次看完。

为什么豁免要显式登记：不是所有容器都该降权。要读宿主 docker.sock 做备份的
容器降权后连备份源都读不到——这种时候「符合规范」就是错的。与其让脚本报红
然后被人无视（或更糟：为迎合脚本改坏实现），不如把理由写进配置，
脚本打印成「已登记的例外」，让它成为一条被审阅过的决定。

用法：
    python3 scripts/check-docker.py              # 扫描全部已登记仓库
    python3 scripts/check-docker.py shenshi ntfy # 只看指定仓库
"""
from __future__ import annotations

import fnmatch
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent

# ---------------------------------------------------------------- 配置

# 仓库 -> Dockerfile（可多个）与 compose 文件。
# root_exempt：明确以 root 运行且理由成立的，写明原因后不计缺失。
# health_exempt：探活手段在编排层之外（如 k8s probe）的，写明原因。
REPOS: dict[str, dict] = {
    "shenshi": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "guanfu": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "feixi": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "mujian": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    # diarum 无 compose（登记的 docker-compose.yml 是历史漂移，读取失败会静默降级）
    "diarum": dict(df=["Dockerfile"], compose=None),
    "bili-history": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "liuxia": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "chaxin": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "qingye": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "huozhi": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "yuexi": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "tuqie": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "qbhive": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    # 2026-10-17 从 distroless/static 切到 alpine，探活改回 Dockerfile HEALTHCHECK，
    # 原先的 health_exempt 随之撤销（alpine 自带 wget）。
    "qiansi": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "RSSRob": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "WebMonitor": dict(df=["Dockerfile.slim", "frontend/Dockerfile"], compose="docker-compose.yml"),
    "yuexi-extra": dict(df=[], compose=None),  # 占位，见下方清理
    # 备份工具必须能读宿主任意容器卷，降权后读不到备份源 —— 这是它的功能前提，
    # 不是疏漏。同理它需要 docker.sock。
    "docker-db-auto-backup": dict(df=["Dockerfile"], compose="docker-compose.yml",
                                  root_exempt="需读宿主任意卷做备份 + 访问 docker.sock，降权后无法工作",
                                  health_exempt="调度型容器（按 SCHEDULE 触发备份，无常驻服务）；"
                                                "compose 里的 tail -f /dev/null 是上游 dev 测试床，不是部署形态"),
    # ntfy 的 Dockerfile 来自上游，Dockerfile-build 是构建期镜像。
    "ntfy": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "zhichang": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    # siwu 的 compose 里另有一个 browser profile（headless-shell），
    # 但 siwu 自身仍是装配式单二进制镜像，基线按主服务判。
    "siwu": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    "youlv": dict(df=["Dockerfile"], compose="docker-compose.yml"),
    # whoami 无 compose：单容器部署，装配式 Dockerfile 直起，属已登记变体
    "whoami": dict(df=["Dockerfile"], compose=None),
}
REPOS.pop("yuexi-extra", None)  # 保持配置表干净：没有 Dockerfile 的仓库不登记

# SQLite 应用自导出的接入方（契约见 docs/backup.md）：这类应用必须在部署声明里
# 写绝对路径的 BACKUP_SQLITE_CMD（/app 布局下裸名 exit 127），漏声明 = 静默漏备份。
SQLITE_APPS = {
    "shenshi", "feixi", "chaxin", "guanfu", "liuxia", "yuexi",
    "qiansi", "mujian", "diarum", "huozhi", "bili-history", "siwu",
    "youlv",
}

# 最后的 libc? 不需要 —— 只看最后一个 stage（运行时）的 FROM / USER
COPY_RE = re.compile(r"^\s*(?:COPY|ADD)\s+", re.I)
FROM_RE = re.compile(r"^\s*FROM\s+", re.I | re.M)

# BACKUP_SQLITE_* 声明：compose 里两种写法都认（映射式 `KEY: value` 与列表式 `KEY=value`）。
# 值只取 ASCII 命令字符（含 `<>`：README 占位符 `/app/<app> backup` 是散文而非声明，
# 截断它反而会误报「不含 backup」）——README 里 `BACKUP_SQLITE_EXT=zip`（否则工具按
# `.db` 魔数……）这类散文在反引号后还有整句中文，值匹配必须在反引号/中文处停住。
BACKUP_CMD_RE = re.compile(r"""BACKUP_SQLITE_CMD["']?\s*[:=]\s*["']?([A-Za-z0-9_./:=+\-<> ]+[A-Za-z0-9_./:=+\-<>])""")
# EXT 值不含空格，捕获首个 token 后看尾段：尾段还有 ASCII 词就说明整个值带空格/非法
BACKUP_EXT_RE = re.compile(r"""BACKUP_SQLITE_EXT["']?\s*[:=]\s*["']?([^\s"'`（，。、]+)([^\n]*)""")
SQLITE_LABEL_RE = re.compile(r"""backup\.provider["']?\s*[:=]\s*["']?sqlite""")
EXT_VALUE_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
EXT_TAIL_RE = re.compile(r"^\s+[A-Za-z0-9_./:=+\-]")
# 列表式 `- KEY="value"` 的引号会存进变量值（YAML 只剥外层包裹的引号），旧版工具把
# 整串当命令名 exit 127。只认 `=` 分隔：映射式 `KEY: "value"` 的引号是 YAML 语法，安全。
BACKUP_QUOTED_RE = re.compile(r"""(BACKUP_SQLITE_(?:CMD|EXT|USER))\s*=\s*["']""")


# ---------------------------------------------------------------- 工具

def read(repo: str, rel: str | None) -> str | None:
    if not rel:
        return None
    p = WORKSPACE / repo / rel
    return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else None


def strip_comments(text: str) -> list[str]:
    """去掉 Dockerfile 注释与续行，返回有效指令行（续行已合并）。"""
    out: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if buf:                       # 处于续行中
            buf = buf[:-1] + " " + line.strip()
            if buf.rstrip().endswith("\\"):
                continue
            out.append(buf)
            buf = ""
            continue
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.endswith("\\"):
            buf = s
            continue
        out.append(s)
    if buf:
        out.append(buf.rstrip("\\"))
    return out


def last_stage_lines(lines: list[str]) -> list[str]:
    """最后一个 FROM 之后的指令 —— 运行时行为由它决定，builder 里的 USER 不算。"""
    idx = [i for i, l in enumerate(lines) if FROM_RE.match(l)]
    return lines[idx[-1]:] if idx else lines


def copy_sources(lines: list[str]) -> list[str]:
    """提取需要构建上下文提供的 COPY/ADD 源路径。

    `--from=` 的多阶段复制源在别的 stage 的文件系统里，与 .dockerignore 无关，跳过。
    """
    srcs: list[str] = []
    for l in lines:
        if not COPY_RE.match(l):
            continue
        toks = l.split()[1:]
        if "--from" in " ".join(toks[:3]):     # 形如 COPY --from=builder /x /y
            continue
        toks = [t for t in toks if not t.startswith("--")]
        if toks:
            srcs.append(toks[0].strip('"').lstrip("./"))
    return [s for s in srcs if s and not s.startswith("$")]


def dockerignore_patterns(repo: str) -> list[str]:
    text = read(repo, ".dockerignore")
    if not text:
        return []
    pats: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("!"):        # 取反规则：本脚本不做精确求值，跳过以免误判为排除
            continue
        pats.append(s.rstrip("/"))
    return pats


def _match_level(level: str, pattern: str) -> bool:
    """单条 pattern 与单个路径层级的匹配，近似 Docker 的行为。"""
    p = pattern.lstrip("/")
    if fnmatch.fnmatch(level, p):
        return True
    if p.startswith("**/") and fnmatch.fnmatch(level, p[3:]):
        return True            # '**/node_modules' ≈ 任意深度下的该名字
    if p.endswith("/**"):
        return fnmatch.fnmatch(level, p[:-1]) or fnmatch.fnmatch(level, p[:-3])
    return False


def is_excluded(src: str, pats: list[str]) -> str | None:
    """判断 COPY 源是否被 dockerignore 排除，返回命中的 pattern。

    Docker 的匹配是「全路径逐级」的：对 `bin/app`，会依次拿 `bin`、`bin/app`
    去比对每条 pattern；某条 pattern 命中父目录，则子路径一并被排除。
    反过来，pattern `app` **不会**命中 `bin/app`（`*` 不跨越 `/`）——早期版本按
    文件名比对，把 diarum / qiansi 的 `bin/<binary>` 误判成被根部的二进制名排除。

    仍然刻意保守：`!` 例外规则与完整的 `**` 中间匹配不做精确求值，宁可漏报。
    """
    norm = src.strip("/")
    if not norm:
        return None
    parts = norm.split("/")
    levels = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    for p in pats:
        pn = p.strip().lstrip("/").rstrip("/")
        if not pn:
            continue
        for lv in levels:
            if _match_level(lv, pn):
                return p
    return None


# ---------------------------------------------------------------- 各项检查

def check_pins(lines: list[str]) -> tuple[bool, list[str]]:
    notes: list[str] = []
    ok = True
    for line in lines:
        if not FROM_RE.match(line):
            continue
        toks = line.split()
        if len(toks) < 2:
            continue
        img = toks[1]
        if "${" in img or "$" in img:          # ARG 形式：看不出真实 tag，交给人工
            notes.append(f"`{line[:60]}` 使用变量/ARG，无法确认 pin")
            continue
        if "@sha256:" in img:
            continue                            # digest pin，最严格
        if ":" not in img.rsplit("/", 1)[-1]:
            ok = False
            notes.append(f"`FROM {img}` 未 pin tag（等价于 :latest）")
        elif img.endswith(":latest"):
            ok = False
            notes.append(f"`FROM {img}` 显式写了 :latest")
    return ok, notes


def check_nonroot(repo: str, cfg: dict, lines: list[str], text: str,
                  compose: str) -> tuple[bool, list[str]]:
    if cfg.get("root_exempt"):
        return True, [f"以 root 运行：已登记例外 —— {cfg['root_exempt']}"]

    runtime = last_stage_lines(lines)
    runtime_txt = "\n".join(runtime)

    # A: distroless nonroot
    for line in runtime:
        if re.match(r"^\s*FROM\s+\S*distroless\S*nonroot", line, re.I):
            return True, ["非 root：distroless nonroot 变体（uid 65532）"]

    # A2: 公共运行时基镜。降权入口脚本（PUID/PGID + su-exec/gosu）随基镜一起来，
    #     Dockerfile 里不再出现 su-exec / USER / gosu，检测必须认得这一形态，
    #     否则会把「已用公共基镜」的仓库全部误报成「以 root 运行」。
    for line in runtime:
        if re.match(r"^\s*FROM\s+\S*ghcr\.io/felix2yu/base[:@]", line, re.I):
            return True, ["非 root：公共基镜 ghcr.io/felix2yu/base（内置 PUID/PGID 降权入口）"]

    # B: 静态 USER
    m = re.search(r"^\s*USER\s+(\S+)", runtime_txt, re.M | re.I)
    if m:
        who = m.group(1)
        if who.lower() in ("root", "0"):
            return False, ["显式 `USER root`，等于没降权"]
        return True, [f"非 root：`USER {who}`"]

    # C: 运行时降权。`su-exec` / `gosu` 是最常见的两种，nuxt 时代的写法还有
    #    `exec su -s /bin/sh <user> -c ...`（幕间在用），形式不同目的一样。
    m = re.search(r"exec\s+(?:su-exec|gosu)\b|exec\s+su\s+-s\s+\S+\s+(\w+)", runtime_txt)
    if m:
        return True, ["非 root：运行时 su-exec/gosu 降权"]
    if re.search(r"\b(su-exec|gosu)\b", runtime_txt):
        return True, ["非 root：镜像内置 su-exec/gosu，降权在入口命令里完成"]
    scripts = re.findall(r'(?:ENTRYPOINT|CMD)\s*\[?\s*"?([^"\]\s]*\.sh)', runtime_txt)
    for s in scripts:
        body = read(repo, s.lstrip("/")) or ""
        if re.search(r"\b(su-exec|gosu)\b", body):
            return True, [f"非 root：入口脚本 {s} 内 su-exec/gosu 降权"]

    if re.search(r'^\s*user\s*:', compose, re.M | re.I):
        return True, ["非 root：compose 里指定了 `user:`"]

    return False, ["以 root 运行（未见 USER / su-exec / distroless nonroot）"]


def check_health(cfg: dict, text: str, compose: str) -> tuple[bool, list[str]]:
    if "HEALTHCHECK" in text:
        return True, ["探活：Dockerfile HEALTHCHECK"]
    if re.search(r'^\s*healthcheck\s*:', compose, re.M | re.I):
        return True, ["探活：compose healthcheck（distroless 无 shell 时的等价做法）"]
    if cfg.get("health_exempt"):
        return True, [f"探活：已登记例外 —— {cfg['health_exempt']}"]
    return False, ["无探活手段（Dockerfile 无 HEALTHCHECK，compose 也无 healthcheck）"]


def check_context(repo: str, cfg: dict, lines: list[str]) -> tuple[bool, list[str]]:
    """装配式的前提：CI 产物要能进上下文。"""
    problems = []
    # .dockerignore 若是指向 .gitignore 的符号链接，两边的排除语义就绑在一起了：
    # .gitignore 要排除 bin/（产物），.dockerignore 必须保留它（COPY 源）。
    dot = WORKSPACE / repo / ".dockerignore"
    if dot.is_symlink():
        problems.append(
            f".dockerignore 是符号链接（-> {dot.resolve().name}），与 .gitignore 共用排除清单；"
            "两者语义相反（产物 vs COPY 源），必须拆成独立文件")
    pats = dockerignore_patterns(repo)
    if pats:
        srcs = copy_sources(last_stage_lines(lines))
        for s in srcs:
            p = is_excluded(s, pats)
            if p:
                problems.append(f"COPY 源 `{s}` 被 .dockerignore 的 `{p}` 排除")
    if problems:
        return False, problems
    return True, []


def check_tz(text: str) -> tuple[bool, list[str]]:
    """软检查：tzdata 缺失只在容器里暴露，给提示但不判失败。"""
    # 公共基镜自带 tzdata 与 ENV TZ，应用 Dockerfile 里自然看不到这两样，不算缺失。
    if re.search(r"^FROM\s+\S*ghcr\.io/felix2yu/base[:@]", text, re.M | re.I):
        return True, []
    has_tzdata = bool(re.search(r"tzdata|tzdata-env", text, re.I))
    has_tz = bool(re.search(r"ENV\s+.*\bTZ=", text))
    if has_tzdata and has_tz:
        return True, []
    return True, ["软提示：未见 tzdata 或 `ENV TZ=`，涉及本地自然日的业务逻辑会偏 UTC"]


def check_execform(lines: list[str]) -> tuple[bool, list[str]]:
    notes: list[str] = []
    for line in last_stage_lines(lines):
        if re.match(r"^\s*(ENTRYPOINT|CMD)\s+", line, re.I):
            rest = line.split(None, 1)[1]
            if not rest.lstrip().startswith("["):
                notes.append(f"`{line.split()[0]}` 用了 shell form，信号传不到进程（应用 exec form）")
    return (not notes), notes


def _active_lines(text: str) -> str:
    """去掉整行注释——compose 里注释掉的示例不当作声明，但反过来说，
    声明被注释掉（如 huozhi 的可选 EXT=zip）也算「没声明」，这正是期望。"""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def check_backup_sqlite(repo: str, compose: str) -> tuple[bool | None, list[str]]:
    """SQLite 自导出声明（docs/backup.md）。

    返回 None 表示本仓与 SQLite 备份无关（表里打 `--`），True/False 才计入统计。
    compose 缺失或没声明时回退 README——diarum / yuexi 的声明在 docker run 示例里；
    回退只对已登记的接入方做，避免把工具仓 README 里的契约散文当成声明去校验。
    """
    notes: list[str] = []
    ok = True
    sources: list[tuple[str, str]] = []
    if compose:
        sources.append(("compose", _active_lines(compose)))

    seen_cmd = any(BACKUP_CMD_RE.search(t) for _, t in sources)
    if not seen_cmd and repo in SQLITE_APPS:
        readme = read(repo, "README.md") or ""
        if readme:
            sources.append(("README", _active_lines(readme)))

    cmds: list[tuple[str, str]] = []
    exts: list[tuple[str, str, str]] = []
    quoted: list[str] = []
    labeled = False
    for origin, text in sources:
        cmds += [(origin, m.group(1).strip()) for m in BACKUP_CMD_RE.finditer(text)]
        exts += [(origin, m.group(1), m.group(2)) for m in BACKUP_EXT_RE.finditer(text)]
        labeled |= bool(SQLITE_LABEL_RE.search(text))
        if origin == "compose":
            # 行内注释里的反例（「不要写成 KEY="value"」）不是声明，先去掉尾注释
            for line in text.splitlines():
                m = BACKUP_QUOTED_RE.search(re.sub(r"\s+#.*$", "", line))
                if m:
                    quoted.append(m.group(1))

    if not cmds and not labeled and repo not in SQLITE_APPS:
        return None, []
    if not cmds:
        ok = False
        if labeled:
            notes.append("打了 backup.provider=sqlite 标签但无 BACKUP_SQLITE_CMD——"
                         "标签只解锁镜像名推导，/app 布局下推导出的裸名必然 127，备份仍会失败")
        else:
            notes.append("未见 BACKUP_SQLITE_CMD 声明——备份工具看不到这个容器，会静默漏备份"
                         "（/app 布局下镜像名推导不成立，必须显式声明绝对路径命令）")

    for origin, cmd in cmds:
        toks = cmd.split()
        if not toks:
            continue
        if not toks[0].startswith("/"):
            ok = False
            notes.append(f"[{origin}] BACKUP_SQLITE_CMD `{cmd}` 非绝对路径——"
                         "/app 不在 PATH，sh -c 解析不到（裸名实测 exit 127）")
        if "backup" not in toks:
            ok = False
            notes.append(f"[{origin}] BACKUP_SQLITE_CMD `{cmd}` 不含 `backup` 子命令——"
                         "与应用侧约定不符，工具按约定执行会直接失败")
        if "--format" in toks:
            i = toks.index("--format")
            if i + 1 < len(toks) and toks[i + 1] == "zip" and not any(e == "zip" for _, e, _ in exts):
                ok = False
                notes.append(f"[{origin}] 命令产出 zip（`--format zip`）但未声明 "
                             "BACKUP_SQLITE_EXT=zip——工具按 db 魔数校验 zip 产物，"
                             "校验失败、产物丢弃")

    if quoted:
        ok = False
        notes.append("compose 列表式 " + "、".join(sorted({f"`{v}=…`" for v in quoted}))
                     + " 的引号会存进变量值——改裸值 `- KEY=value` 或映射式 `KEY: value`"
                     "（工具现会剥最外层引号兜底，但契约要求裸值，旧版本会整串当命令名 127）")

    for origin, ext, tail in exts:
        if EXT_TAIL_RE.match(tail):
            # 值带空格（`EXT=my backup`）：首 token 看着合法，整个值其实非法
            ok = False
            notes.append(f"[{origin}] BACKUP_SQLITE_EXT 值含空白（`{ext}…`），"
                         "非法（只允许字母数字、≤16 位）")
        elif not EXT_VALUE_RE.match(ext):
            ok = False
            notes.append(f"[{origin}] BACKUP_SQLITE_EXT `{ext}` 非法（只允许字母数字、≤16 位）")
        elif ext not in ("db", "zip"):
            notes.append(f"[{origin}] 软提示：BACKUP_SQLITE_EXT={ext} 不是 db/zip，"
                         "工具按该扩展名查魔数，确认产物格式相符")

    return ok, notes


# ---------------------------------------------------------------- 主流程

def main() -> int:
    targets = sys.argv[1:] or list(REPOS)
    for u in [t for t in targets if t not in REPOS]:
        print(f"[warn] 未登记的仓库 {u}，跳过（如需纳入请加进 REPOS）")

    rows: list[tuple[str, str, str, str, str, str, str, list[str]]] = []
    for repo in targets:
        if repo not in REPOS:
            continue
        cfg = REPOS[repo]
        base = WORKSPACE / repo
        if not base.is_dir():
            rows.append((repo, "--", "--", "--", "--", "--", "--", ["工作区内不存在该目录"]))
            continue

        texts = [(d, read(repo, d) or "") for d in cfg["df"]]
        compose = read(repo, cfg["compose"]) or ""
        user_notes: list[str] = []
        pin_ok = form_ok = ctx_ok = True
        health_ok = False
        pin_notes: list[str] = []
        ctx_notes: list[str] = []
        form_notes: list[str] = []
        health_notes: list[str] = []

        for d, text in texts:
            if not text:
                user_notes.append(f"未找到 {d}")
                continue
            lines = strip_comments(text)
            p, pn = check_pins(lines)
            pin_ok &= p
            pin_notes += [f"[{d}] {n}" for n in pn]
            c, cn = check_context(repo, cfg, lines)
            ctx_ok &= c
            ctx_notes += [f"[{d}] {n}" for n in cn]
            f, fn = check_execform(lines)
            form_ok &= f
            form_notes += [f"[{d}] {n}" for n in fn]
            h, hn = check_health(cfg, text, compose)
            health_ok |= h
            health_notes += [f"[{d}] {n}" for n in hn]
            _, un = check_nonroot(repo, cfg, lines, text, compose)
            user_notes += [f"[{d}] {n}" for n in un]
            _, tzn = check_tz(text)
            health_notes += [f"[{d}] {n}" for n in tzn]

        root_ok = not any("root 运行（" in n for n in user_notes)
        b_ok, b_notes = check_backup_sqlite(repo, compose)

        notes = pin_notes + ctx_notes + form_notes + user_notes + health_notes + b_notes
        rows.append((repo, mark(pin_ok), mark(ctx_ok), mark(root_ok),
                     mark(health_ok), mark(form_ok),
                     "--" if b_ok is None else mark(b_ok), notes))

    print("=" * 114)
    print("容器镜像自检（契约见 docs/docker.md / docs/backup.md）")
    print("=" * 114)
    print(f"{'仓库':<24}{'FROM pin':<10}{'上下文':<10}{'非root':<10}{'探活':<8}{'exec form':<10}{'备份声明':<10}")
    print("-" * 114)
    for repo, p, c, u, h, f, b, _ in rows:
        print(f"{repo:<24}{p:<10}{c:<10}{u:<10}{h:<8}{f:<10}{b:<10}")

    print("\n明细")
    print("-" * 114)
    for repo, p, c, u, h, f, b, notes in rows:
        print(f"\n{repo}")
        for n in notes:
            print(f"    - {n}")

    total = len(rows)
    failed = sum(1 for r in rows if "NG" in r[1:7])
    print("\n" + "-" * 114)
    print(f"共 {total} 个仓库，{total - failed} 个全绿，{failed} 个待修。")
    print("「非root」列带「已登记例外」的属有意保留 root，不是待修项；「备份声明」列 `--` 表示与 SQLite 备份无关。")
    return 1 if failed else 0


def mark(ok: bool) -> str:
    return "OK" if ok else "NG"


if __name__ == "__main__":
    sys.exit(main())
