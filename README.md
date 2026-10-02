# Felix2yu/.github — 统一 CI/CD 工作流

13 个自研 Go 仓库共用的 GitHub Actions 规范。构建、版本发布、镜像构建、Codecov 上报
四处逻辑各只有一份实现；各仓库只保留一个薄调用文件传参数。

前端一律用 **pnpm**：工具与版本不靠 workflow 猜，而是读各仓库自己的声明——
Node 版本读 `.nvmrc`，包管理器读 `package.json` 的 `packageManager` 字段，
两者缺任何一项 CI 直接失败（不设兜底值）。

```
Felix2yu/.github/
├── .github/workflows/
│   ├── reusable-test.yml      测试 + 覆盖率 → Codecov
│   ├── reusable-image.yml     预编译 + 双架构镜像 + manifest 合并
│   ├── reusable-release.yml   5 平台二进制 + 版本注入 + 校验和
│   └── self-test.yml          本仓库门禁（actionlint + 重复块一致性检查）
└── scripts/
    ├── check-pm-blocks.sh         前端解析/安装重复块逐字节一致性
    └── check-reusable-inputs.py   调用方 input 契约校验（本地跑）
```

> 前端解析与安装两块代码在三个 reusable workflow 里有 4 份物理副本（release 有
> `static` 与 `glibc` 两个 job），由 `scripts/check-pm-blocks.sh` 逐字节钉住。
> 本地改完先 `bash scripts/check-pm-blocks.sh`。

---

## 一、接入

在目标仓库建 `.github/workflows/ci.yml`：

```yaml
name: CI

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    branches: [main]
  release:
    types: [published]
  schedule:
    - cron: '0 3 * * 1'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  test:
    uses: Felix2yu/.github/.github/workflows/reusable-test.yml@v1
    secrets:
      CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}
    with:
      go-dir: server
      test-packages: ./internal/...

  image:
    needs: test
    permissions:
      contents: read
      packages: write
    uses: Felix2yu/.github/.github/workflows/reusable-image.yml@v1
    with:
      go-dir: server
      binary-name: shenshi
      image-name: ghcr.io/felix2yu/shenshi

  release:
    if: github.event_name == 'release'
    permissions:
      contents: write
    uses: Felix2yu/.github/.github/workflows/reusable-release.yml@v1
    with:
      go-dir: server
      binary-name: shenshi
```

**四条必须遵守的规则**：

1. **引用必须 pin tag**，不能写 `@main`。`v1` 是移动 tag，`v1.0.0` 起是不可变 release tag。
2. **权限只能向下收紧**——被调 workflow 里写了 `packages: write` 不等于真的拿到。
   调用方 job 必须自己声明，否则 GHCR push 会 403。
3. **不要写 `secrets: inherit`**。`GITHUB_TOKEN` 由平台自动授予被调 workflow；
   只有 `CODECOV_TOKEN` 需要显式传。
4. **有前端就交三件套**：`<web-dir>/package.json` 里的 `packageManager` 字段、
   `<web-dir>/pnpm-lock.yaml`、仓库根的 `.nvmrc`。缺任何一项 CI 直接失败——
   这是有意的：以前靠 lockfile 是否存在来猜包管理器，`bun.lock` 与
   `package-lock.json` 并存时就把 CI 猜偏过（见第七节）。

---

## 二、参数契约

### reusable-test.yml

| input | 类型 | 默认 | 说明 |
|---|---|---|---|
| `go-dir` | string | `.` | `go.mod` 所在目录。**留空 = 本仓库没有 Go 后端**（如 WebMonitor 的 Python 后端），此时跳过 setup-go / vet / test / Go 覆盖率，只跑前端部分。默认值保持 `.` 是有意的：宁可让漏传的 Go 仓库报错，也不要静默不测。**仅 `reusable-test.yml` 支持空值**，image / release 仍要求有 Go 目录 |
| `test-packages` | string | `./...` | `go test` 目标包；main 包带 `//go:embed` 时改 `./internal/...` |
| `test-flags` | string | `-covermode=atomic` | 追加参数，如 `-race` |
| `test-cgo` | string | `''`（沿用 runner 的 1） | 显式覆盖 `CGO_ENABLED`。`-race` 与 CGO 依赖都要求 1——测试产物不发布、静态与否无意义，所以默认不关掉它；确需关闭填 `0` |
| `build-tags` | string | `''` | 传给 `go vet` / `go test` 的 `-tags`。ntfy = `sqlite_omit_load_extension osusergo netgo` |
| `docs-command` | string | `''` | 仓库根执行，`go vet` 之前。生成 npm 之外的前置产物（如 ntfy 的 mkdocs 文档 → `server/docs`）。命令须保证无缓存时也能跑通 |
| `docs-cache-path` / `docs-cache-key-files` | string | `''` / `requirements.txt` | 对该路径做 `actions/cache`，纯加速用 |
| `docs-python` | string | `python3` | 注入给 `docs-command` 的 `$PYTHON` |
| `vet` | boolean | `true` | |
| `pre-test-command` | string | `''` | 在 `go-dir` 内执行，生成 `go:embed` 依赖物 |
| `web-dir` | string | `''` | 空 = 无前端 |
| `node-version-file` | string | `.nvmrc` | 相对仓库根。读不到就失败，**不设兜底版本** |
| `web-build-command` | string | `''` | 非空则替换默认的 `pnpm run build`；依赖仍由本流程统一 `pnpm install --frozen-lockfile` 安装 |
| `web-test-command` | string | `''` | 如 huozhi 的 `pnpm run test:coverage` |
| `coverage-file` | string | `coverage.out` | 相对 `go-dir` |
| `frontend-coverage-file` | string | `''` | 相对仓库根 |
| `codecov-flag-go` / `codecov-flag-web` | string | `backend` / `frontend` | |
| `codecov-disable-search` | boolean | `false` | qiansi = true |
| `runs-on` | string | `ubuntu-24.04` | |

secrets：`CODECOV_TOKEN`（`required: false`）

> **包管理器不是入参**：读 `<web-dir>/package.json` 的 `packageManager`，只接受 `pnpm@<版本>`。
> 写成 `npm@` / `bun@` 或缺字段都会让 job 直接失败，并打印可复制的修复行。

### reusable-image.yml

| input | 类型 | 默认 | 说明 |
|---|---|---|---|
| `go-dir` | string | `.` | |
| `main-package` | string | `.` | 相对 `go-dir` |
| `binary-name` | string | **必填** | |
| `image-name` | string | **必填** | 如 `ghcr.io/felix2yu/shenshi` |
| `cgo` | string | `off` | `off \| glibc \| static` |
| `build-tags` | string | `''` | 传给 `go build` 的 `-tags` |
| `static-ldflags` | string | `-linkmode=external -extldflags=-static` | `cgo=static` 时追加的链接选项 |
| `static-verify-command` | string | `''` | 非空则以它替代默认的「无 INTERP 段」校验 |
| `dockerfile` | string | `Dockerfile` | |
| `context` | string | `.` | |
| `prebuilt-dest` | string | `bin` | 二进制落到 Docker 上下文的位置 |
| `build-ldflags` | string | `-s -w` | |
| `web-dir` / `node-version-file` / `web-build-command` / `frontend-stage-command` | | | 同 test |
| `frontend-stage-command` | string | `''` | 仓库根执行：把前端产物摆到 `go:embed` 位置和/或 `dist/` |
| `runner-amd64` / `runner-arm64` | string | `ubuntu-24.04` / `ubuntu-24.04-arm` | |
| `build-args` | string | `''` | |
| `push-images` | boolean | `true` | |

### reusable-release.yml

| input | 类型 | 默认 | 说明 |
|---|---|---|---|
| `go-dir` / `main-package` / `binary-name` | | | 同上 |
| `cgo` | string | `off` | `off` 走单 runner 交叉编译；非 `off` 走各平台原生 runner |
| `build-tags` | string | `''` | CGO 平台（linux）的 `-tags` |
| `cgo-off-platforms` | string | `''` | 逗号分隔的 goos，强制 `CGO_ENABLED=0`。ntfy = `darwin,windows` |
| `build-tags-cgo-off` | string | `''` | 上述平台追加的 `-tags`。ntfy = `noserver` |
| `static-ldflags` | string | `-linkmode=external -extldflags=-static` | `cgo=static` 时追加的链接选项 |
| `extra-version-vars` | string | `''` | 额外 `-X` 注入。ntfy = `main.commit={{sha8}} main.date={{date}}`（占位符在 shell 里展开） |
| `docs-*` 组 | | | 同 image |
| `version-var` | string | `main.version` | 被注入的符号；diarum = `main.Version` |
| `release-platforms` | string | `linux/amd64,linux/arm64,darwin/amd64,darwin/arm64,windows/amd64` | |
| `build-ldflags` | string | `-s -w` | |
| `web-dir` / `node-version-file` / `web-build-command` / `frontend-stage-command` | | | 同 image |
| `pre-build-command` | string | `''` | 编译前在仓库根执行 |
| `extra-files` | string | `''` | 额外挂到 release 的文件 |

---

## 三、镜像 tag 策略（统一）

`docker/metadata-action` 统一产出，不再手写拼装：

| 场景 | 产出的 tag |
|---|---|
| 推到默认分支 | `latest`、`sha-<40hex>` |
| 推 `v1.2.3` tag | `v1.2.3`、`1.2`、`sha-<40hex>`、`latest` |
| `release published` | 同「推 tag」——版本化镜像在发布事件也会构建（与 tag push 同一 ref 下幂等，不会产生重复镜像） |
| PR | 只构建不推送 |

多架构 manifest 由 `docker buildx imagetools create` 合并 amd64 + arm64。

> 消费端的 `image` job **不再**用 `if: github.event_name != 'release'` 排除发布事件。
> 这样「发布」既出二进制（release job）也出版本化镜像（image job），二者同时跑；
> 新 tag 同时触发 push 与 release 两条流水线时，因 `concurrency.group` 共用 `refs/tags/vX` 会自动串行，不会并发写 GHCR。

---

## 四、CGO 形态（off / glibc / static）

| 值 | 适用 | 做法 |
|---|---|---|
| `off` | 12 个仓库 | `CGO_ENABLED=0` 静态链接，单runner 交叉编译 |
| `glibc` | **幕间（mujian）** | 在 `golang:1.27-bookworm`（glibc 2.36）容器内编译 + `readelf` 校验 glibc 下限 |
| `static` | **ntfy** | 在 `golang:1.27-alpine` 容器内 `apk add build-base` 后编译，追加 `-extldflags=-static`；再用 `readelf` 断言**无 INTERP 段** |

**为什么幕间要在容器里编**：CGO 把二进制绑死在编译机的 glibc 上。若直接在 runner 上编，
glibc 下限就成了 GitHub runner 轮换的属性——`ubuntu-latest` 从 24.04（glibc 2.39）滚到
26.04（2.42）时不会改任何一行 workflow，却会让已发布的镜像在启动时报
`GLIBC_2.43 not found`，而 CI 全程是绿的。放进固定 glibc 的容器，下限变回仓库自己的属性。

配套的 `Verify glibc floor` 步骤从 Dockerfile 读运行镜像版本自动比对，
升基镜像时会自动放宽这条检查。

**为什么 ntfy 走 `static`**：它的运行镜像是 alpine（musl），而 go-sqlite3 必须开 CGO。
`CGO_ENABLED=1` 默认产出的是 glibc 动态链接二进制，扔进 alpine 会在启动时
`Error loading shared library libc.musl-x86_64.so.1`，且 CI 全程是绿的。
所以既要在 alpine 里编（避免 glibc 依赖），又要 `-extldflags=-static`（连 musl 也不依赖），
最后用「无 INTERP 段」把这件事钉死——`readelf -l | grep INTERP` 是唯一能证明「真静态」的手段。

> `golang:*-alpine` **不含 gcc**，必须先 `apk add --no-cache build-base`，
> 否则报 `C compiler "gcc" not found`。这一步已内置在 workflow 里。

---

## 五、版本号：不需要手写

| 版本 | 来源 |
|---|---|
| Go | 读 `<go-dir>/go.mod` |
| Node | 读仓库根的 `.nvmrc`；缺文件直接失败，**没有兜底版本** |
| pnpm | 读 `<web-dir>/package.json` 的 `packageManager`；`pnpm/action-setup` 自己解析 |
| 发布版本 | git tag → `-X main.version=<tag>` 注入二进制 |
| 镜像 tag | metadata-action 从 ref / semver / sha 自动推导 |

**各仓库需要加的代码**（main 包新增 `version.go`）：

```go
package main

// version 由 CI 注入：-ldflags "-X main.version=v1.2.3"；本地 go build 时为 "dev"。
var version = "dev"
```

该变量**必须被真正引用**（接 `-version` flag 或启动日志即可），否则链接器会优化掉符号，
`-X` 静默失效。

---

## 六、升级流程

1. 在本仓库改 workflow → `self-test.yml`（actionlint + `scripts/check-pm-blocks.sh`）必须绿
2. 跑 `scripts/check-reusable-inputs.py`（需 `pip install pyyaml`）——
   它比对全部调用方 `ci.yml` 传的 input 与本仓库声明的是否一一对应。
   **这一步不能省**：传了未声明的 input，GitHub 只给 `startup_failure`，
   没有 job、没有日志，actionlint 也推不出远端契约。
3. 若改了 input 契约：新增可选参数（向后兼容）或同步更新全部 13 个调用方。
   **删参数尤其危险**——调用方多传未声明的 input 会让 job 直接 `startup_failure`。
4. 打不可变 tag：`git tag v1.1.0 && git push origin v1.1.0`
5. 移动 `v1` 到新提交：`git tag -f v1 && git push -f origin v1`
6. 批量升级调用方时开 PR，逐个验证（Dependabot **不会**更新 reusable workflow 的 ref）

---

## 七、已知坑

- `.github` 仓库的路径是双重的：`Felix2yu/.github/.github/workflows/x.yml@v1`
- `github.repository` 在 reusable workflow 里指向**本仓库**而非调用方——所以 `image-name` 必填
- `needs` 一个被 `if` 跳过的 job 会连带跳过下游，`publish` job 已用 `if: ${{ !cancelled() }}` 兜底
- fork / Dependabot PR 的 token 只读、secrets 为空，login / push / codecov 都需 `if` 守卫
- `download-artifact@v8` 必须配 `digest-mismatch: warn`（upload 用 v7，无 digest 元数据）
- GHA 缓存 scope 带 `binary-name` 维度，避免跨仓库撞车；仓库上限 10GB
- runner 必须写死版本（如 `ubuntu-24.04`），用 `ubuntu-latest` 会在大版本轮换时静默改 glibc
- **调用方传了被调 workflow 未声明的 input 会直接 `startup_failure`**：没有 job、没有日志，
  只能看到 conclusion。排查时第一件事是比对两边 input 名是否一一对应
- **装配式 Dockerfile 要求 `.dockerignore` 不得排除 `bin/` 与 `dist/`**：
  否则 `COPY bin/<binary>` 报 `CopyIgnoredFile: ... excluded by .dockerignore`。
  同时本地 `docker build` / `docker compose build` 前必须先编译出二进制
- 若仓库原有「校验 Dockerfile 基镜像版本 == .nvmrc / go.mod」这类脚本，
  改为装配式后 Dockerfile 不再声明工具链版本，该校验失去对象，需一并调整
- **包管理器只用 pnpm，且只读声明、不探 lockfile**。解析块在 4 处（test / image / release×2）
  物理存在，由 `scripts/check-pm-blocks.sh` 逐字节校验——改一处必须四处一起改。
- **npm 风格写在 package.json 顶层的 `overrides` 不会被 pnpm 读取**（pnpm 认 `pnpm-workspace.yaml`
  的 `overrides:`，或 package.json 的 `pnpm.overrides`）。迁移前它很可能一直是空转的，
  迁完用 `pnpm why <包名>` 逐条核实是否真的生效。
- **`--legacy-peer-deps` 在 pnpm 没有对应物**，该入参已废除。peer 冲突写进各仓
  `pnpm-workspace.yaml` 的 `peerDependencyRules.allowedVersions`
  （例：qiansi 的 `typescript ^6` 与 `@typescript/native`＝TS 7 并存）。
- **pnpm 10+ 默认不执行依赖的构建脚本**。本仓群唯一带 install script 的包是 macOS-only 的
  `fsevents`，CI 跑 linux 不受影响；本地需要时在该仓 `pnpm-workspace.yaml` 写
  `onlyBuiltDependencies: [fsevents]`。
- `binaries-matrix` job 会落在 macos / windows runner 上，因此解析与安装块**必须写 `shell: bash`**
  （默认 pwsh 下 `case`、`[ -f ]` 直接语法失败），并用 `sed` 而非 `jq` 取 `packageManager`
  ——windows runner 不保证有 jq，而此刻 `setup-node` 还没跑。

---

## 八、已接入仓库参数表

套用时**需要按仓库调整的只有 6 类键**：`go-dir`、`main-package`、`binary-name`、
`image-name`、`web-dir` + `frontend-stage-command`、`test-packages`。

带前端的仓库另外要各交一份三件套（见第一节规则 4），`packageManager` **全组织统一**：
`"packageManager": "pnpm@11.22.0"`——它是 pnpm 版本的唯一来源，workflow 里不再有 `version: 11`。
Node 同样拉平到一个版本：8 个前端仓库的仓库根 `.nvmrc` 现在都是 `26`，
与 `qingye/Dockerfile`、`WebMonitor/Dockerfile.slim` 的 `node:26-alpine` 对齐；
新增仓库必须交 `.nvmrc`，否则 `setup-node` 的 `node-version-file` 直接读不到而失败（这是设计，不是 bug）。

> 选 11.22.0 而不是更新的 12：现有 `pnpm-lock.yaml` 全是 `lockfileVersion: 9.0`，
> 由 pnpm 11 生成、CI 也一直在装 11。升 pnpm 大版本等于同时赌一次 lock 格式与
> `--frozen-lockfile` 的匹配，应作为独立一次 PR：先在 canary 仓库清缓存跑通再铺开。
> 本机装的 pnpm 若比声明新，pnpm 会自己按 `packageManager` 切到 11.22.0（需要联网解析一次）。

| 仓库 | go-dir | main-package | binary-name | image-name | web-dir | cgo | 需额外设置 |
|---|---|---|---|---|---|---|---|
| shenshi | `server` | `.` | `shenshi` | `ghcr.io/felix2yu/shenshi` | `web` | off | `frontend-stage-command` 拷 `web/dist` → `server/dist`（go:embed）；`test-packages: ./internal/...` |
| qiansi | `.` | `./cmd/server` | `qiansi` | `ghcr.io/felix2yu/qiansi` | `web` | off | `codecov-disable-search: true`；前端另打 `web-dist.zip`；TS 6/7 并存的 peer 规则写在 `web/pnpm-workspace.yaml` |
| mujian | `backend` | `.` | `mujian` | `ghcr.io/felix2yu/mujian` | `frontend` | **glibc** | `test-cgo: '1'`；`frontend-stage-command` 拷 `frontend/dist` → `backend/dist` |
| huozhi | `backend` | `./cmd/huozhi-server` | `huozhi-server` | `ghcr.io/felix2yu/huozhi` | `frontend` | off | `web-test-command: pnpm run test:coverage` + `frontend-coverage-file` |
| qingye | `server` | `.` | `qingye` | `ghcr.io/felix2yu/qingye` | `web` | off | `web-build-command: pnpm run check && pnpm run build` + `web-test-command: pnpm run test`（原来自有的 web-check job 已删，SvelteKit 的 check 走这里） |
| diarum | `.` | `.` | `diarum` | `ghcr.io/felix2yu/diarum` | `site` | off | `version-var: main.Version`（本仓库变量是大写） |
| bili-history | `backend` | `./cmd` | `bili-history` | `ghcr.io/felix2yu/bili-history` | `frontend` | off | `web-build-command: pnpm run generate`；`test-packages: ./config/... ./utils/... ./models/... ./database/...` |
| bili-dl | `.` | `.` | `bili-dl` | —（无 Dockerfile，删掉 image job） | — | off | — |
| WebMonitor | `''`（后端是 Python） | — | — | —（自有 Docker 流程） | `frontend` | — | 只套用 `reusable-test.yml`：`go-dir: ''` 让 Go 步骤全部跳过；镜像仍由 `Dockerfile.slim` / `frontend/Dockerfile` 自己构建，它们的 Node 阶段用 `node -p "...packageManager.slice(5)"` 解析 pnpm 版本 |
| qbhive | `.` | `./cmd/server` | `qbhive` | `ghcr.io/felix2yu/qbhive` | — | off | — |
| yuexi | `.` | `.` | `yuexi` | `ghcr.io/felix2yu/yuexi` | — | off | `test-flags: -race -covermode=atomic` |
| chaxin | `.` | `./cmd/server` | `chaxin` | `ghcr.io/felix2yu/chaxin` | — | off | 前端非 npm：`frontend-stage-command: sh web/build.sh` |
| liuxia | `.` | `.` | `liuxia` | `ghcr.io/felix2yu/liuxia` | — | off | `test-flags: -race -covermode=atomic` |
| docker-db-auto-backup | `.` | `.` | `db-auto-backup` | `ghcr.io/felix2yu/docker-db-auto-backup` | — | off | 保留 lint / e2e 两个仓库自有 job |
| ntfy | `.` | `.` | `ntfy` | `ghcr.io/felix2yu/ntfy` | `web` | **static** | 见下方专项说明 |

### ntfy 的特殊参数（唯一用到全部新input 的仓库）

| input | 值 | 为什么 |
|---|---|---|
| `build-tags` | `sqlite_omit_load_extension osusergo netgo` | 去掉 `sqlite3_load_extension`、切 osusergo / netgo，与上游 goreleaser 一致 |
| `cgo` | `static` | go-sqlite3 必须 CGO，运行镜像是 alpine →必须完全静态 |
| `prebuilt-dest` | `.` | 上游 `Dockerfile` 是 `COPY ntfy /usr/bin`，二进制须落仓库根；改 Dockerfile 会打断 `.goreleaser.yml` 的 `dockers` 段（本地 `make cli` 仍在用） |
| `docs-command` | mkdocs 三行 | `server/server.go` 有 `//go:embed docs`，而 `server/docs` 在 `.gitignore` 里。**干净 clone 上 `go build ./...` 直接失败**，必须先`mkdocs build` |
| `frontend-stage-command` | 搬 `web/build` → `server/site` | 对应 `//go:embed site`。含 `index.html → app.html` 改名与删 `config.js`，与上游 Makefile `web-build` 逐步对齐 |
| `cgo-off-platforms` / `build-tags-cgo-off` | `darwin,windows` / `noserver` | 这两个平台不需要 sqlite，用纯 Go + `noserver`（纯客户端），与上游 goreleaser 分档一致 |
| `extra-version-vars` | `main.commit` / `main.date` | ntfy 的 `--version` 展示三段信息，共享流程只注入 `main.version` |

> **`docs-command` 不是可选装饰**：ntfy 是本仓群里唯一有「npm 之外的前置产物」的仓库。
> `server/site` 与 `server/docs` 都不在版本库里，CI 必须自己生成，
> 否则 `go vet ./...` / `go build` 在第一步就报 `pattern docs: no matching files found`。

**版本号不需要手写**：Go 版本读 `go.mod`、Node 版本读 `.nvmrc`、发布版本来自 git tag、
镜像 tag 由 metadata-action 推导。
