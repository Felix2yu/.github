# 构建脚本规范（build.sh）

构建脚本是「不写也能跑」的那类东西：直接敲 `go build` 也行、`pnpm build` 也行，
于是每个仓库都长出一套自己的方言 —— 有的 `#!/bin/sh` 有的 `#!/usr/bin/env bash`，
有的 `set -e` 有的 `set -euo pipefail`，有的写死 `NODE_BIN=/Users/xxx/.workbuddy/...`，
有的把产物丢在仓库根、有的丢 `bin/`。换台机器、或者过三个月，就得重读一遍才知道怎么跑。

本文只定 **5 条硬约定**，其余留给项目自由。检查由 `scripts/check-build-scripts.py` 负责。

---

## 一、骨架（照抄这一段）

```bash
#!/usr/bin/env bash
# <项目名>构建脚本 —— <一句话：从什么构建出什么>
#
# 产物：bin/<app>
# 前置：<Go / Node + pnpm / 无>
#
# 用法：
#   ./scripts/build.sh
#   GO_BIN=/path/to/go NODE_BIN=/path/to/node ./scripts/build.sh
#
# 约定见 Felix2yu/.github/docs/build-script.md
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m错误：\033[0m %s\n' "$1" >&2; exit 1; }
```

脚本位置固定为 `scripts/build.sh`（`web/build.sh` 例外，见第三节）。
位置变了要同步改 `README`、`Makefile`、`ci.yml` 里所有引用点。

---

## 二、五条硬约定

| # | 约定 | 判据 | 为什么 |
|---|---|---|---|
| 1 | **`#!/usr/bin/env bash`** | 首行精确匹配 | `sh` 在 macOS 是 bash 3.2、在 Linux 是 dash，`pipefail`/`[[ ]]`/数组行为全不同 |
| 2 | **`set -euo pipefail`** | 三者都在 | `set -e` 不管管道：`pnpm build \| tee log` 失败会被吞掉 |
| 3 | **`ROOT` + `cd "$ROOT"`** | 出现 `ROOT=` 与 `cd "$ROOT"` | 所有 `go build ./cmd/<app>`、`web/` 相对路径都以仓库根为基准；否则在 `scripts/` 下执行必挂 |
| 4 | **统一 `step` / `die`** | 两个函数都存在 | 进度行与错误行格式一致，CI 日志可 grep |
| 5 | **产物落 `bin/`** | `-o "$ROOT/bin/<app>"` + 先 `mkdir -p` | 装配式镜像的 Dockerfile 从 `bin/` COPY（见 [docker.md](docker.md)） |

---

## 三、工具链解析

**原则：脚本不写死版本号，也不写死绝对路径。**

| 变量 | 约定 |
|---|---|
| `GO_BIN` | 默认 `command -v go`；回退 `/opt/homebrew/bin/go`、`/usr/local/go/bin/go`（GUI 启动的终端常常没配 PATH） |
| `NODE_BIN` | 默认按 `.nvmrc` 的大版本找（PATH → nvm → Homebrew → `/usr/local`）；**不接受低于 `.nvmrc` 的大版本**，高于则告警放行 |
| `PNPM_BIN` | 默认 `command -v pnpm`；版本由前端目录 `package.json` 的 `packageManager` 决定，脚本不自选版本 |

三个都支持环境变量覆盖，且覆盖值同样要过校验（`[ -x "$X" ]`）—— 显式指定了错的
工具链却静默继续，比找不到更糟。

`NODE_BIN` 一旦确定，立刻 `export PATH="$(dirname "$NODE_BIN"):$PATH"`，
避免「node 来自一处、pnpm 来自另一处」导致的原生模块 ABI 不匹配。

`web/build.sh`（纯静态前端，如 chaxin）没有 Go 与 Node，可不遵守本节，
但仍须满足第二节第 1、2、4 条；调用点写成 `bash web/build.sh`（不要写 `sh`）。

---

## 四、前端产物内嵌

SvelteKit / Vite 项目把构建输出拷进 `internal/web/dist` 供 `go:embed`：

```bash
step "[2/3] 同步前端产物到 internal/web/dist"
rm -rf internal/web/dist
mkdir -p internal/web/dist
cp -R web/dist/. internal/web/dist/     # 注意是 /. 不是目录本身
touch internal/web/dist/.gitkeep        # rm -rf 会连 tracked 的 .gitkeep 一起删
```

三处易错：`cp -R web/dist internal/web/dist` 会多一层 `dist/`；漏掉 `touch .gitkeep`
会让工作区永久挂着一条删除记录；Vite 默认输出 `dist/`，SvelteKit adapter-static 默认是
`build/`，抄之前先确认。

---

## 五、验证

```bash
bash -n scripts/build.sh                 # 语法
./scripts/build.sh                       # 真跑；产物 file/bin 大小符合预期
bash -c 'cd /tmp && /path/to/scripts/build.sh'   # 从任意目录调用仍成功（验证约定 3）
```

CI 层面：`shenshi` 的 `ci.yml` 直接 `./scripts/build.sh` 作为 UI 冒烟的前置步骤，
这是唯一「脚本本身被 CI 门禁覆盖」的仓库；其余仓库的 mirror 构建走 reusable workflow，
脚本只服务本地开发 —— 因此**改完必须本地真跑一次**，别指望 CI 替你发现。

---

## 六、豁免

`GoldPrice`（Swift）、`ntfyx`（`build-app.sh`）是 fork / 上游跟踪仓，不套用本文。
