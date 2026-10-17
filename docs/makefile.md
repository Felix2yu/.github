# Makefile 目标约定

二十个仓库各自的构建方式不同（Go + Vite、Go + SvelteKit、Go + Nuxt、纯 Go CLI、
纯前端、Python），但**命令名**不该跟着不同。约定七个必备目标，做到换一个仓库
不用重读 Makefile 就知道敲什么。

```
make build     # 产出可运行的东西
make dev       # 本地开发（热更新或直接跑）
make test      # 测试
make lint      # 静态检查
make fmt       # 格式化
make docker    # 构建镜像
make clean     # 清产物
```

各仓可以保留自有目标（`seed`、`migrate`、`check`、`web`、`run`…），但**这七个不许改名字、
不许缺**。缺哪个就在该仓的 README 里说明原因（例如 CLI 仓没有镜像）。

---

## 一、七个目标的确切语义

### `build`

产出可直接运行的产物：二进制落 `bin/<app>`，前端产物摆进 `//go:embed` 的目录。
一个 `make build` 之后，`./bin/<app>` 必须能直接起服务，不依赖再跑别的命令。

**装配式镜像的前提就是它**：Dockerfile 只 `COPY bin/<app>`，不在镜像里编译。

不许塞进来的东西：数据库迁移（`migrate`）、种子数据（`seed`）、部署（`up`）、
发布（`release`）。这些都有自己的目标名。

### `dev`

本地开发入口。前端仓分两种形态：

- **两个终端**（SvelteKit / Vite）：`dev` 起后端，前端另开终端跑 `pnpm dev`。
  这类仓的 `dev` 允许只是一个 `go run`，但**必须在 README 写清楚前端怎么起**。
- **单进程**（纯 Go、Go 模板型）：`dev` 直接 `go run` 带开发参数。

有 `dev.sh` 的仓（mujian、liuxia 一类），`dev` 应当转调它，而不是复制一份逻辑。

### `test`

`go test ./...` 起步。前端有测试的仓（svelte-check、vitest）一并纳入。
**测试必须能在没构建前端的机器上跑通** —— 这是 CI 的硬要求，也是新克隆仓库的第一条命令。
前端的 `//go:embed` 目录里要留 `.gitkeep` 占位，否则 `go test ./...` 直接编译失败。

### `lint`

静态检查，**必须能绿**。按语言叠加：

```makefile
lint:
	gofmt -l . 有输出就退出 1
	go vet ./...
	cd web && pnpm run check     # SvelteKit 的类型检查
```

`gofmt -l` 只列出未格式化文件、不写盘，属于检查；改写是 `fmt` 的事。

**`golangci-lint` 不并入 `lint`，单独开 `lint-strict`**。原因是存量告警还没清零
（19 个 Go 仓合计约 574 条，主力是 errcheck），并进 `lint` 会让 `make lint` 在每个仓
都红，红久了就没人跑。`lint-strict` 定位是「逐步收敛用的」，找不装就跳过：

```makefile
lint-strict: lint
	@if command -v golangci-lint >/dev/null 2>&1; then golangci-lint run; else echo "  （未安装，跳过）"; fi
```

告警清零后，把 `lint-strict` 的内容并回 `lint` 并接进 CI。

### `fmt`

改写文件（`lint` 只读，`fmt` 写）。Go 用 `gofmt -w`，前端有 prettier 的用 `pnpm exec prettier --write .`。
`fmt` 之后 `git status` 应当只有源码改动，不能碰到生成物。

### `docker`

**先 build 再 build 镜像**，顺序不能反：

```makefile
docker: build
	docker build -t <app>:dev .
```

装配式镜像里没有编译器，跳过 `build` 直接 `docker build` 会因为 `COPY bin/<app>` 找不到文件而失败。

### `clean`

删产物，**不要删到被跟踪的文件**。两类坑：

- `rm -rf internal/web/dist` 会顺手删掉 tracked 的 `.gitkeep`，工作区从此永久挂着一条删除记录。
  用 `rm -rf internal/web/dist/*`（不匹配点开头）或删完补 `touch`。
- `rm -rf data` 在开发机上会真的删掉本地库。想清就显式写 `data/`，并在 README 提醒。

---

## 二、型态对照

| 型态 | 仓库 | `docker` | 前端相关目标 |
|---|---|---|---|
| Go + Vite（嵌 `internal/web/dist`） | shenshi、guanfu、tuqie、feixi、qingye、qiansi、mujian | 有 | `build` 内联前端构建 |
| Go + SvelteKit（嵌 `internal/web/dist` 或根 `dist/`） | huozhi、chaxin、diarum | 有 | 同上 |
| Go 模板型（嵌 `static/` + `templates/`） | liuxia、qbhive | 有 | 无前端构建 |
| Go 纯服务 | yuexi、whoami、feixi | 有 | 无 |
| Go CLI | bili-dl | **无**（无 Dockerfile） | 无 |
| Go 调度任务 | docker-db-auto-backup | 有（compose 为上游 dev 测试床） | 无 |
| 纯前端 | WebMonitor | 有 | 全部目标由 pnpm 承担 |
| Python | RSSRob | 有 | 无 |

`bili-dl` 是唯一没有 `docker` 目标的仓库——它没有 Dockerfile，发布走 GitHub Release 的二进制。

---

## 三、写法细节

**必须 `.PHONY`**：目标名和目录同名时（`web`、`dist`、`data`）make 会认为「已是最新」直接跳过。
`web` 目标撞上 `web/` 目录是高频事故。

**`BIN` / `MAIN` 用变量**，路径集中一处：

```makefile
BIN  := bin/shenshi
MAIN := ./cmd/shenshi
```

**依赖版本不写死在 Makefile 里**。Go 版本看 `go.mod`，Node 版本看 `.nvmrc`，
把版本号抄进 Makefile 就等于制造第二个真相源。

**前端产物拷贝要 `rm -rf` 目标目录再 `cp -r 源/.`**。`cp -r src dst` 在 `dst` 已存在时
会嵌成 `dst/src/`，而 `go:embed` 读到的仍是上一次的旧外壳——新构建的前端根本没进二进制，
症状是「改了前端但页面没变」。

---

## 四、检查

`python3 scripts/check-repo-hygiene.py` 的「Makefile」列验证七个目标是否齐备；
缺 `docker` 的仓需在 `EXEMPT` 里登记理由。
