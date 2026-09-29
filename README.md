# Felix2yu/.github — 统一 CI/CD 工作流

13 个自研 Go 仓库共用的 GitHub Actions 规范。构建、版本发布、镜像构建、Codecov 上报
四处逻辑各只有一份实现；各仓库只保留一个薄调用文件传参数。

```
Felix2yu/.github/.github/workflows/
├── reusable-test.yml      测试 + 覆盖率 → Codecov
├── reusable-image.yml     预编译 + 双架构镜像 + manifest 合并
├── reusable-release.yml   5 平台二进制 + 版本注入 + 校验和
└── self-test.yml          本仓库门禁（actionlint）
```

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
    if: github.event_name != 'release'
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

**三条必须遵守的规则**：

1. **引用必须 pin tag**，不能写 `@main`。`v1` 是移动 tag，`v1.0.0` 起是不可变 release tag。
2. **权限只能向下收紧**——被调 workflow 里写了 `packages: write` 不等于真的拿到。
   调用方 job 必须自己声明，否则 GHCR push 会 403。
3. **不要写 `secrets: inherit`**。`GITHUB_TOKEN` 由平台自动授予被调 workflow；
   只有 `CODECOV_TOKEN` 需要显式传。

---

## 二、参数契约

### reusable-test.yml

| input | 类型 | 默认 | 说明 |
|---|---|---|---|
| `go-dir` | string | `.` | `go.mod` 所在目录 |
| `test-packages` | string | `./...` | `go test` 目标包；main 包带 `//go:embed` 时改 `./internal/...` |
| `test-flags` | string | `-covermode=atomic` | 追加参数，如 `-race` |
| `test-cgo` | string | `''`（沿用 runner 的 1） | 显式覆盖 `CGO_ENABLED`。`-race` 与 CGO 依赖都要求 1——测试产物不发布、静态与否无意义，所以默认不关掉它；确需关闭填 `0` |
| `vet` | boolean | `true` | |
| `pre-test-command` | string | `''` | 在 `go-dir` 内执行，生成 `go:embed` 依赖物 |
| `web-dir` | string | `''` | 空 = 无前端 |
| `node-version-file` | string | `.nvmrc` | |
| `node-version` | string | `22` | 无 `.nvmrc` 时的兜底 |
| `package-manager` | string | `auto` | `auto \| npm \| pnpm \| bun` |
| `web-install-flags` | string | `''` | 如 qiansi 的 `--legacy-peer-deps` |
| `web-build-command` | string | `''` | 非空则完全接管前端构建 |
| `web-test-command` | string | `''` | 如 huozhi 的 `npm run test:coverage` |
| `coverage-file` | string | `coverage.out` | 相对 `go-dir` |
| `frontend-coverage-file` | string | `''` | 相对仓库根 |
| `codecov-flag-go` / `codecov-flag-web` | string | `backend` / `frontend` | |
| `codecov-disable-search` | boolean | `false` | qiansi = true |
| `runs-on` | string | `ubuntu-24.04` | |

secrets：`CODECOV_TOKEN`（`required: false`）

### reusable-image.yml

| input | 类型 | 默认 | 说明 |
|---|---|---|---|
| `go-dir` | string | `.` | |
| `main-package` | string | `.` | 相对 `go-dir` |
| `binary-name` | string | **必填** | |
| `image-name` | string | **必填** | 如 `ghcr.io/felix2yu/shenshi` |
| `cgo` | string | `off` | `off \| musl \| glibc` |
| `dockerfile` | string | `Dockerfile` | |
| `context` | string | `.` | |
| `prebuilt-dest` | string | `bin` | 二进制落到 Docker 上下文的位置 |
| `build-ldflags` | string | `-s -w` | |
| `web-dir` / `node-*` / `package-manager` / `web-install-flags` / `web-build-command` | | | 同 test |
| `frontend-stage-command` | string | `''` | 仓库根执行：把前端产物摆到 `go:embed` 位置和/或 `dist/` |
| `runner-amd64` / `runner-arm64` | string | `ubuntu-24.04` / `ubuntu-24.04-arm` | |
| `build-args` | string | `''` | |
| `push-images` | boolean | `true` | |

### reusable-release.yml

| input | 类型 | 默认 | 说明 |
|---|---|---|---|
| `go-dir` / `main-package` / `binary-name` | | | 同上 |
| `cgo` | string | `off` | `off` 走单 runner 交叉编译；非 `off` 走各平台原生 runner |
| `version-var` | string | `main.version` | 被注入的符号；diiarum = `main.Version` |
| `release-platforms` | string | `linux/amd64,linux/arm64,darwin/amd64,darwin/arm64,windows/amd64` | |
| `build-ldflags` | string | `-s -w` | |
| `web-dir` / node 组 / `frontend-stage-command` | | | 同 image |
| `pre-build-command` | string | `''` | 编译前在仓库根执行 |
| `extra-files` | string | `''` | 额外挂到 release 的文件 |

---

## 三、镜像 tag 策略（统一）

`docker/metadata-action` 统一产出，不再手写拼装：

| 场景 | 产出的 tag |
|---|---|
| 推到默认分支 | `latest`、`sha-<40hex>` |
| 推 `v1.2.3` tag | `v1.2.3`、`1.2`、`sha-<40hex>`、`latest` |
| PR | 只构建不推送 |

多架构 manifest 由 `docker buildx imagetools create` 合并 amd64 + arm64。

---

## 四、CGO 三态

| 值 | 适用 | 做法 |
|---|---|---|
| `off` | 10 个仓库 | `CGO_ENABLED=0` 静态链接，单 runner 交叉编译 |
| `musl` | bili-history | 装 `musl-tools`，`CC=musl-gcc CGO_ENABLED=1` 静态链接 |
| `glibc` | **幕间（mujian）** | 在 `golang:1.27-bookworm`（glibc 2.36）容器内编译 + `readelf` 校验 glibc 下限 |

**为什么幕间要在容器里编**：CGO 把二进制绑死在编译机的 glibc 上。若直接在 runner 上编，
glibc 下限就成了 GitHub runner 轮换的属性——`ubuntu-latest` 从 24.04（glibc 2.39）滚到
26.04（2.42）时不会改任何一行 workflow，却会让已发布的镜像在启动时报
`GLIBC_2.43 not found`，而 CI 全程是绿的。放进固定 glibc 的容器，下限变回仓库自己的属性。

配套的 `Verify glibc floor` 步骤从 Dockerfile 读运行镜像版本自动比对，
升基镜像时会自动放宽这条检查。

---

## 五、版本号：不需要手写

| 版本 | 来源 |
|---|---|
| Go | 读 `<go-dir>/go.mod` |
| Node | 读 `.nvmrc`；无该文件时用 `node-version` 兜底 |
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

1. 在本仓库改 workflow → `self-test.yml`（actionlint）必须绿
2. 若改了 input 契约：新增可选参数（向后兼容）或同步更新全部 13 个调用方
3. 打不可变 tag：`git tag v1.1.0 && git push origin v1.1.0`
4. 移动 `v1` 到新提交：`git tag -f v1 && git push -f origin v1`
5. 批量升级调用方时开 PR，逐个验证（Dependabot **不会**更新 reusable workflow 的 ref）

---

## 七、已知坑

- `.github` 仓库的路径是双重的：`Felix2yu/.github/.github/workflows/x.yml@v1`
- `github.repository` 在 reusable workflow 里指向**本仓库**而非调用方——所以 `image-name` 必填
- `needs` 一个被 `if` 跳过的 job 会连带跳过下游，`publish` job 已用 `if: ${{ !cancelled() }}` 兜底
- fork / Dependabot PR 的 token 只读、secrets 为空，login / push / codecov 都需 `if` 守卫
- `download-artifact@v8` 必须配 `digest-mismatch: warn`（upload 用 v7，无 digest 元数据）
- GHA 缓存 scope 带 `binary-name` 维度，避免跨仓库撞车；仓库上限 10GB
- runner 必须写死版本（如 `ubuntu-24.04`），用 `ubuntu-latest` 会在大版本轮换时静默改 glibc

---

## 八、已接入仓库参数表

套用时**需要按仓库调整的只有 6 类键**：`go-dir`、`main-package`、`binary-name`、
`image-name`、`web-dir` + `frontend-stage-command`、`test-packages`。

| 仓库 | go-dir | main-package | binary-name | image-name | web-dir | cgo | 需额外设置 |
|---|---|---|---|---|---|---|---|
| shenshi | `server` | `.` | `shenshi` | `ghcr.io/felix2yu/shenshi` | `web` | off | `frontend-stage-command` 拷 `web/dist` → `server/dist`（go:embed）；`test-packages: ./internal/...` |
| qiansi | `.` | `./cmd/server` | `qiansi` | `ghcr.io/felix2yu/qiansi` | `web` | off | `web-install-flags: --legacy-peer-deps`；`codecov-disable-search: true`；前端另打 `web-dist.zip` |
| mujian | `backend` | `.` | `mujian` | `ghcr.io/felix2yu/mujian` | `frontend` | **glibc** | `test-cgo: '1'`；`frontend-stage-command` 拷 `frontend/dist` → `backend/dist` |
| huozhi | `backend` | `./cmd/huozhi-server` | `huozhi-server` | `ghcr.io/felix2yu/huozhi` | `frontend` | **musl** | `test-cgo: '1'`；`web-test-command: npm run test:coverage` + `frontend-coverage-file` |
| qingye | `server` | `.` | `qingye` | `ghcr.io/felix2yu/qingye` | `web` | **musl** | `test-cgo: '1'` |
| diarum | `.` | `.` | `diarum` | `ghcr.io/felix2yu/diarum` | `site` | off | `version-var: main.Version`（本仓库变量是大写） |
| bili-history | `backend` | `./cmd` | `bili-history` | `ghcr.io/felix2yu/bili-history` | `frontend` | **musl** | `web-build-command: pnpm run generate`；`test-packages: ./config/... ./utils/... ./models/...` |
| bili-dl | `.` | `.` | `bili-dl` | —（无 Dockerfile，删掉 image job） | — | off | — |
| qbhive | `.` | `./cmd/server` | `qbhive` | `ghcr.io/felix2yu/qbhive` | — | off | — |
| yuexi | `.` | `.` | `yuexi` | `ghcr.io/felix2yu/yuexi` | — | off | `test-flags: -race -covermode=atomic` |
| chaxin | `.` | `./cmd/server` | `chaxin` | `ghcr.io/felix2yu/chaxin` | — | off | 前端非 npm：`frontend-stage-command: sh web/build.sh` |
| liuxia | `.` | `.` | `liuxia` | `ghcr.io/felix2yu/liuxia` | — | off | `test-flags: -race -covermode=atomic` |
| docker-db-auto-backup | `.` | `.` | `db-auto-backup` | `ghcr.io/felix2yu/docker-db-auto-backup` | — | off | 保留 lint / e2e 两个仓库自有 job |

**版本号不需要手写**：Go 版本读 `go.mod`、Node 版本读 `.nvmrc`、发布版本来自 git tag、
镜像 tag 由 metadata-action 推导。
