#!/usr/bin/env python3
"""按 docs/PWA.md 的契约，自检工作区内各 Web 应用的 PWA 合规情况。

为什么需要它：契约写进文档就只是「应该」，没人会逐项去对。图标漏一枚、
maskable 用了 SVG、manifest 少个 id —— 这些都不是构建错误，CI 不报、
浏览器也不报错（至多 DevTools 一行警告），等到真机安装才发现主屏图标是
黑底或默认图标，那时版本已经发出去了。脚本把它变成一条命令能看完的东西。

为什么 Pillow 是可选的：位图深度校验（满幅无透明、安全圆）只有 Pillow 能做，
但它不该成为跑一次自检的门槛 —— 缺了它依然检查文件存在性、head、manifest、SW，
只是跳过位图校验并在结尾提示安装。不能因为一个可选依赖让整个自检不可用。

用法：
    python3 scripts/check-pwa.py              # 扫描全部已知仓库
    python3 scripts/check-pwa.py shenshi ntfy # 只看指定仓库
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 共享仓库位于 <workspace>/.github，各业务仓库与它平级
WORKSPACE = ROOT.parent

try:
    from PIL import Image

    HAVE_PIL = True
except ImportError:  # 可选依赖，缺失时降级而非中断
    HAVE_PIL = False

# ---------------------------------------------------------------- 配置

# 仓库 -> 检查入口。mf/sw 既可以是真 JSON（精确解析），也可以是源码（宽松正则，
# 因为 Go 结构体与 TS 对象里字段是散着写的）。html 同理，nuxt 是对象语法。
REPOS: dict[str, dict] = {
    "shenshi": dict(html="web/index.html", static="web/public",
                    mf="web/public/manifest.webmanifest", sw="web/public/sw.js"),
    "mujian": dict(html="frontend/src/app.html", static="frontend/static",
                   mf="frontend/static/manifest.webmanifest", sw="frontend/static/sw.js"),
    "diarum": dict(html="site/src/app.html", static="site/static",
                   mf="site/static/manifest.webmanifest", sw=None),
    # 图切的 SW 不用「换缓存名」那一套：它按当前构建 HTML 里出现的 /assets 名单精确
    # 增删（syncBuildFiles），比计数上限更严格，也正是固定缓存名能成立的前提。
    # 这两项对它不适用，登记为等价替代，不算缺失。
    "tuqie": dict(html="web/index.html", static="web/public",
                  mf="web/public/manifest.json", sw="web/public/sw.js",
                  sw_exempt=["缓存名未版本化", "条目上限"]),
    # manifest 由 Go 动态生成：字段声明在 types.go，取值与图标路径在 server_web.go
    "ntfy": dict(html="web/index.html", static="web/public/static/images",
                 mf=["server/types.go", "server/server_web.go"], sw=None),
    # head 写在 nuxt.config.ts 的 app.head（TS 对象语法）；manifest link 由 @vite-pwa/nuxt 注入
    "bili-history": dict(html="frontend/nuxt.config.ts", static="frontend/public",
                         mf="frontend/nuxt.config.ts", sw=None,
                         head_injected=["manifest link"]),
    "qingye": dict(html="web/src/app.html", static="web/static",
                   mf="web/vite.config.ts", sw=None),
    "liuxia": dict(html="templates/index.html", static="static",
                   mf="web.go", sw="web.go"),
    "yuexi": dict(html="internal/handler/template/layout.html", static=None,
                  mf="internal/handler/static/manifest.json",
                  sw="internal/handler/static/sw.js",
                  icons_dynamic="main.go"),  # 图标由 Go 运行时绘制，无磁盘文件
    "huozhi": dict(html="frontend/src/app.html", static="frontend/static",
                   mf="frontend/vite.config.ts", sw=None),
    "qiansi": dict(html="web/index.html", static="web/public",
                   mf="web/vite.config.ts", sw=None),
    "chaxin": dict(html="web/index.html", static="web",
                   mf="web/manifest.webmanifest", sw="web/sw.js"),
}

ASSETS = ["favicon.ico", "favicon.svg", "icon-192.png", "icon-512.png",
          "icon-maskable-192.png", "icon-maskable-512.png", "apple-touch-icon.png"]
REQUIRED_ASSETS = ["favicon.ico", "favicon.svg", "icon-192.png", "icon-512.png",
                   "icon-maskable-512.png", "apple-touch-icon.png"]

# head 检查项：名称 -> 正则（同时兼容 HTML 属性语法 rel="x" 与 TS 对象语法 rel: 'x'）
HEAD_ITEMS: list[tuple[str, str]] = [
    ("manifest link", r'rel\s*[:=]\s*["\']manifest["\']'),
    ("theme-color", r'name\s*[:=]\s*["\']theme-color["\']'),
    ("mobile-web-app-capable", r'name\s*[:=]\s*["\']mobile-web-app-capable["\']'),
    ("apple-mobile-web-app-capable", r'name\s*[:=]\s*["\']apple-mobile-web-app-capable["\']'),
    ("apple-mobile-web-app-status-bar-style", r'name\s*[:=]\s*["\']apple-mobile-web-app-status-bar-style["\']'),
    ("apple-mobile-web-app-title", r'name\s*[:=]\s*["\']apple-mobile-web-app-title["\']'),
    ("format-detection", r'name\s*[:=]\s*["\']format-detection["\']'),
    ("icon .ico", r'rel\s*[:=]\s*["\']icon["\'][^>]*\.ico|favicon\.ico'),
    ("icon .svg", r'rel\s*[:=]\s*["\']icon["\'][^>]*\.svg|favicon\.svg'),
    ("icon 192", r'rel\s*[:=]\s*["\']icon["\'][^>]*icon-192\.png|icons/icon-192\.png'),
    ("apple-touch-icon", r'rel\s*[:=]\s*["\']apple-touch-icon["\']'),
]

# manifest 必需字段（宽松匹配：JSON 键、TS 对象键、Go 结构体字段都算）
MF_FIELDS = ["id", "short_name", "description", "lang", "dir", "start_url",
             "scope", "display", "background_color", "theme_color"]

# 构建产物 index.html 的常见位置（manifest link 等可能只存在于产物中）
DIST_HTML = ["web/dist/index.html", "frontend/dist/index.html", "dist/index.html",
             "frontend/build/index.html", "site/build/index.html", "build/index.html",
             "frontend/.output/public/index.html"]

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".svelte-kit", ".output", "target"}


# ---------------------------------------------------------------- 工具

def read(repo: str, rel: str | None) -> str | None:
    if not rel:
        return None
    p = WORKSPACE / repo / rel
    return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else None


def read_all(repo: str, rel: str | list[str] | None) -> str | None:
    """manifest 的字段可能散落在多个文件（如 Go 的结构体声明与赋值处）。"""
    if not rel:
        return None
    parts = [read(repo, r) for r in ([rel] if isinstance(rel, str) else rel)]
    parts = [p for p in parts if p]
    return "\n".join(parts) if parts else None


def find_asset(repo: str, static: str, name: str) -> pathlib.Path | None:
    base = WORKSPACE / repo / static
    if not base.is_dir():
        return None
    for p in base.rglob(name):
        if not any(d in SKIP_DIRS for d in p.parts):
            return p
    return None


def mark(ok: bool) -> str:
    return "OK" if ok else "NG"


# ---------------------------------------------------------------- 各项检查

def check_assets(repo: str, cfg: dict) -> tuple[bool, list[str]]:
    """图标资产：存在性 + 位图深度校验（后者需 Pillow）。"""
    notes: list[str] = []
    static = cfg.get("static")

    if cfg.get("icons_dynamic"):
        src = read(repo, cfg["icons_dynamic"]) or ""
        miss = [a for a in REQUIRED_ASSETS if a.replace(".png", "") not in src
                and a not in src]
        if miss:
            notes.append(f"动态图标路由缺: {', '.join(miss)}（在 {cfg['icons_dynamic']} 中未出现）")
        return (not miss), notes

    if not static:
        return False, ["未配置静态资源根目录"]

    missing = [a for a in REQUIRED_ASSETS if find_asset(repo, static, a) is None]
    if missing:
        notes.append(f"缺图标: {', '.join(missing)}")
    optional_missing = [a for a in ASSETS
                        if a not in REQUIRED_ASSETS and find_asset(repo, static, a) is None]
    if optional_missing:
        notes.append(f"缺推荐图标: {', '.join(optional_missing)}")

    if not HAVE_PIL:
        return (not missing), notes

    # maskable：必须位图、满幅无透明、中心有主体
    for name in ("icon-maskable-512.png", "icon-maskable-192.png"):
        p = find_asset(repo, static, name)
        if p is None:
            continue
        im = Image.open(p).convert("RGBA")
        if im.size != (int(name.split("-")[-1].split(".")[0]),) * 2:
            notes.append(f"{name} 尺寸为 {im.size[0]}x{im.size[1]}，与文件名不符")
        alpha = im.split()[3]
        if alpha.getextrema()[0] < 255:
            notes.append(f"{name} 存在透明像素（maskable 必须满幅不透明）")
        w, h = im.size
        px = im.load()
        corner = px[2, 2][:3]
        # 中心 60% 区域应存在与底色明显不同的像素，否则主体没落在中央
        cx0, cy0, cx1, cy1 = int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)
        diff = 0
        for y in range(cy0, cy1, 4):
            for x in range(cx0, cx1, 4):
                r, g, b, _ = px[x, y]
                if abs(r - corner[0]) > 24 or abs(g - corner[1]) > 24 or abs(b - corner[2]) > 24:
                    diff += 1
        if diff < 10:
            notes.append(f"{name} 中心区域与底色几乎无差异，主体可能没落在中央")

    # apple-touch-icon：必须 PNG，透明底在 iOS 上会渲染成黑底
    p = find_asset(repo, static, "apple-touch-icon.png")
    if p is not None:
        im = Image.open(p).convert("RGBA")
        if im.size[0] != 180:
            notes.append(f"apple-touch-icon.png 为 {im.size[0]}x{im.size[1]}，契约要求 180x180")
        if im.split()[3].getextrema()[0] < 255:
            notes.append("apple-touch-icon.png 带透明像素（iOS 会渲染成黑底）")

    return (not missing), notes


def check_head(repo: str, cfg: dict) -> tuple[bool, list[str]]:
    """head 契约：源码与构建产物取并集 —— 注入型声明只存在于产物里。"""
    src = read(repo, cfg.get("html")) or ""
    dist = ""
    for cand in DIST_HTML:
        d = read(repo, cand)
        if d:
            dist = d
            break
    blob = src + "\n" + dist
    if not blob.strip():
        return False, [f"未找到 {cfg.get('html')} 及任何构建产物 index.html"]

    injected = set(cfg.get("head_injected") or [])
    miss = [n for n, pat in HEAD_ITEMS
            if n not in injected and not re.search(pat, blob, re.I)]
    if miss:
        return False, [f"head 缺: {', '.join(miss)}"]
    return True, []


def check_manifest(repo: str, cfg: dict) -> tuple[bool, list[str]]:
    rel = cfg.get("mf")
    text = read_all(repo, rel)
    if text is None:
        return False, [f"未找到 manifest 源 {rel}"]
    notes: list[str] = []
    is_json = isinstance(rel, str) and rel.endswith((".json", ".webmanifest"))

    if is_json:
        try:
            d = json.loads(text)
        except json.JSONDecodeError as e:
            return False, [f"{rel} 不是合法 JSON: {e}"]
        miss = [f for f in MF_FIELDS if f not in d]
        if miss:
            notes.append(f"字段缺: {', '.join(miss)}")
        icons = d.get("icons") or []
        has_any = any(i.get("purpose", "any") == "any" for i in icons)
        has_mask = any("maskable" in (i.get("purpose") or "") for i in icons)
        both = any("maskable" in (i.get("purpose") or "") and "any" in (i.get("purpose") or "")
                   for i in icons)
    else:
        # Go 结构体（json tag）/ TS 对象：字段散着写，只判存在性
        miss = [f for f in MF_FIELDS
                if not re.search(rf'["\']?{re.escape(f)}["\']?\s*[:=]', text)
                and not re.search(rf'\b{re.escape(f)}\b\s+string', text)
                and not re.search(rf'json\s*:\s*["\'][^"\']*\b{re.escape(f)}\b', text)]
        if miss:
            notes.append(f"字段缺: {', '.join(miss)}")
        has_any = bool(re.search(r'icon-192|icon-512|purpose:\s*"any"', text))
        has_mask = "maskable" in text
        both = bool(re.search(r'any\s+maskable|"any maskable"', text))

    if not has_any:
        notes.append("缺 purpose:any 图标")
    if not has_mask:
        notes.append("缺 purpose:maskable 图标")
    if both:
        notes.append("存在 any/maskable 混写条目（应拆成独立条目）")

    return (not miss and has_any and has_mask and not both), notes


def check_sw(repo: str, cfg: dict) -> tuple[bool, list[str]]:
    text = read(repo, cfg.get("sw"))
    if text is None:
        # 无手写 sw.js 的仓库走插件生成，只能确认声明存在
        pkg = read(repo, "web/package.json") or read(repo, "frontend/package.json") \
            or read(repo, "site/package.json") or read(repo, "package.json") or ""
        declared = bool(re.search(r'vite-plugin-pwa|@vite-pwa/', pkg))
        notes = [] if declared else ["既无手写 sw.js，也未在 package.json 声明 vite-plugin-pwa"]
        return declared, notes

    notes: list[str] = []
    # 版本化的形式很多：占位符替换、模板字面量、或 '前缀' + 变量 拼接。
    # 只要缓存名最终由构建期内容决定就算数，不强制某一种写法。
    versioned = bool(re.search(
        r'__BUILD_VERSION__'
        r'|\$\{[^}]*(?:VERSION|BUILD|HASH)[^}]*\}'
        r'|\bCACHE_VERSION\b|\bswVersion\b'
        r'|["\']\s*\+\s*\w*(?:VERSION|BUILD|HASH)\w*',
        text))
    if not versioned:
        notes.append("缓存名未版本化（未见 __BUILD_VERSION__ / CACHE_VERSION / 版本变量拼接）")
    if "activate" not in text or "caches.delete" not in text:
        notes.append("activate 阶段未清理旧缓存")
    if not re.search(r'MAX_\w*ENTRIES|maxEntries', text):
        notes.append("API/离线缓存未见条目上限")
    # 兜底既可以是独立离线页，也可以是回落到首页壳（caches.match('/')）
    if not re.search(r'offline|navigateFallback|caches\.match\(\s*["\']/["\']\s*\)', text, re.I):
        notes.append("未见离线兜底页或首页壳回落")

    exempt = cfg.get("sw_exempt") or []
    kept = [n for n in notes if not any(k in n for k in exempt)]
    for n in notes:
        if n not in kept:
            notes[notes.index(n)] = f"{n} —— 已登记为等价替代（{cfg.get('sw_note', '见 REPOS 注释')}），不计缺失"
    return (not kept), notes


# ---------------------------------------------------------------- 主流程

def main() -> int:
    targets = sys.argv[1:] or list(REPOS)
    unknown = [t for t in targets if t not in REPOS]
    for u in unknown:
        print(f"[warn] 未登记的仓库 {u}，跳过（如需纳入请加进 REPOS）")

    rows: list[tuple[str, str, str, str, str, list[str]]] = []
    for repo in targets:
        if repo not in REPOS:
            continue
        cfg = REPOS[repo]
        if not (WORKSPACE / repo).is_dir():
            rows.append((repo, "--", "--", "--", "--", ["工作区内不存在该目录"]))
            continue
        a_ok, a_notes = check_assets(repo, cfg)
        h_ok, h_notes = check_head(repo, cfg)
        m_ok, m_notes = check_manifest(repo, cfg)
        s_ok, s_notes = check_sw(repo, cfg)
        notes = [f"[资产] {n}" for n in a_notes] + [f"[head] {n}" for n in h_notes] \
            + [f"[manifest] {n}" for n in m_notes] + [f"[SW] {n}" for n in s_notes]
        rows.append((repo, mark(a_ok), mark(h_ok), mark(m_ok), mark(s_ok), notes))

    print("=" * 96)
    print("PWA 契约自检（契约见 docs/PWA.md）")
    print("=" * 96)
    print(f"{'仓库':<14}{'资产':<8}{'head':<8}{'manifest':<10}{'SW':<8}")
    print("-" * 96)
    for repo, a, h, m, s, _ in rows:
        print(f"{repo:<14}{a:<8}{h:<8}{m:<10}{s:<8}")

    detailed = [(r, n) for r, a, h, m, s, n in rows if n]
    if detailed:
        print("\n明细（缺项 / 已登记的等价替代）")
        print("-" * 96)
        for repo, notes in detailed:
            print(f"\n{repo}")
            for n in notes:
                print(f"    - {n}")

    total = len(rows)
    failed = sum(1 for r in rows if "NG" in r[1:5])
    print("\n" + "-" * 96)
    print(f"共 {total} 个仓库，{total - failed} 个全绿，{failed} 个待修。")
    if not HAVE_PIL:
        print("提示：未安装 Pillow，已跳过位图深度校验（满幅透明 / 主体居中）。"
              "装了更准：pip install pillow")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
