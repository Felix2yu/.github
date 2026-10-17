# 项目结构规范

22 个仓库摆在一起，前端目录有 `web/`、`frontend/`、`site/`，还有一个把 npm 工程塞进
Go 包里的 `internal/web/`；Go 模块有的在根、有的在 `server/`、有的在 `backend/`、
有的干脆平铺；Docker 文件大多在根，zhichang 的 compose 和 entrypoint 却躲在 `deploy/`，
entrypoint 本身还有五种位置、两种命名。这些分裂**不会让任何一次构建失败**——它们只会
让「改一个跨仓库约定」变成改三处：CI 的 `web-dir`/`go-dir` 输入、dependabot 的
`directory` 字段、`check-repo-baseline.py` 的 `REPOS` 表。同一份事实在三个地方各写
一遍，就一定会不一致；而不一致的那一份，平时看不出任何症状。

本文把目录结构钉成契约。与 [repo-baseline.md](repo-baseline.md)（仓库该有哪些文件）、
[docker.md](docker.md)（镜像怎么拼）、[frontend.md](frontend.md)（Node/pnpm/产物位置）
是同级的第四份跨仓库文档，管的是**这些文件放在哪、叫什么**。

> 与 frontend.md 第四节的关系：该节原立场「各仓库产物位置不一致是历史原因，不要为了
> 统一而统一」自本文起**限位**——它对**已运行服务的运行时产物路径**（容器内
> `/app/web/dist`、`QIANSI_WEB_DIR` 这类 env 契约）依然有效，那确实不该为统一而统一；
> 但**源码目录结构**（前端目录名、Go 模块位置、embed 位、entrypoint 位置）从此按本文
> 有意统一，理由即上一段。

---

## 一、标准目录树

```
<repo>/                        # 仓库根 = Go 模块根
├── go.mod / go.sum            # [必需·Go] module path 保持不变（迁移期缩小 diff）
├── cmd/
│   └── <app>/                 # [必需·Go] 唯一 main 包所在
│       ├── main.go            # [必需] flag 解析、装配、启动
│       └── version.go         # [可选] version 变量（release.md 的 -X 注入对象）
├── internal/                  # [必需·Go] 私有业务包
│   ├── api/ store/ config/ db/ …      # [条件] 常见分包，按需
│   └── web/                   # [条件] go:embed 位（仅「内嵌型」前端项目）
│       ├── embed.go           #   package web；//go:embed all:dist；FS()/HasBuild()
│       └── dist/.gitkeep      #   暂存区占位入库（dist/ 本体 gitignore）
├── web/                       # [条件] Node 前端工程（npm 型必有）
│   ├── src/ public/           # [必需]
│   ├── scripts/               # [可选] 前端侧辅助脚本（build-sw.mjs 型）
│   ├── package.json           # [必需] 含 "packageManager": "pnpm@11.22.0"
│   ├── pnpm-lock.yaml         # [必需] 与 package.json 同级，勿手改
│   ├── pnpm-workspace.yaml    # [可选] overrides / peerDependencyRules
│   ├── vite.config.ts / svelte.config.js / nuxt.config.ts   # [按框架]
│   └── dist/ | build/ | .output/public/  # [产物·gitignore] Vite/SvelteKit/Nuxt 各不同
├── static/ + templates/       # [条件] Go 模板型前端（无 Node 链），随 embed 包走
├── scripts/
│   ├── build.sh               # [推荐] 唯一构建实现（工具链定位+前端+内嵌+go build）
│   ├── docker-entrypoint.sh   # [条件] 需 PUID/PGID 降权时（docker.md 方案 C）
│   └── <工具脚本>             # [可选] smoke / check / icon 生成
├── bin/                       # [可选] go build 产物落地（gitignore；Dockerfile COPY 源）
├── dist/                      # [可选·条件] 运行时静态交付型的 CI 暂存区（gitignore）
├── data/                      # [可选] 本地开发数据（gitignore；容器内路径由 env 定）
├── docs/  migrations/  testdata/      # [可选] 设计文档 / SQL 迁移 / 测试固件
├── .github/
│   ├── workflows/ci.yml       # [必需·自研] 薄调用 reusable workflow，pin @v1
│   └── dependabot.yml         # [必需·自研] 整份覆盖自 preset
├── .nvmrc                     # [必需·条件] 有 Node 前端时；必须在仓库根
├── .gitignore  .dockerignore  # [必需]（后者不得排除 bin/ dist/ scripts/docker-entrypoint.sh）
├── Dockerfile                 # [可选] 装配式，留根
├── docker-compose.yml         # [可选] 留根；后缀统一 .yml
├── Makefile                   # [可选] 薄封装：build 目标调 scripts/build.sh
├── README.md                  # [必需] > 400 字节
├── LICENSE                    # [必需·自研] MIT
├── AGENTS.md  .env.example    # [可选]
```

### 五种型态的落法

| 型态 | 代表仓库 | web/ | embed 位 | 产物链 |
|---|---|---|---|---|
| Node 前端（Vite/React） | feixi、shenshi、qiansi、tuqie | 有 | `internal/web/dist` | stage command 拷 `web/dist → internal/web/dist` |
| SvelteKit | guanfu、zhichang、mujian、huozhi、qingye、diarum | 有 | 同上（产物是 `web/build`，别假设 dist） | 同上 |
| 纯静态 | qbhive（web/static）、chaxin（web/ 手搓） | 有（无 npm 三件套） | qbhive 无（运行时 COPY）；chaxin 有 | chaxin 保留 `web/build.sh`，CI 用 `frontend-stage-command: sh web/build.sh` |
| Go 模板 | liuxia、yuexi、whoami | 无 | `static/`+`templates/`（根或 internal 内） | 无 Node 链、无 .nvmrc |
| 无前端 CLI | bili-dl、docker-db-auto-backup | 无 | 无 | 仅 cmd/ + internal/ |

### 已登记的允许变体

不强制回改、避免无谓 diff，登记在此即合规：

| 变体 | 仓库 | 内容 |
|---|---|---|
| `web/embed.go` | tuqie、zhichang | embed 指令放前端目录内直读构建输出，免 stage command |
| 根 `package.json` 工具包 | mujian | 只放测试工具依赖（happy-dom/playwright-core），不承担前端构建 |
| 根 `dist/` CI 暂存区 | qiansi、qingye、mujian | 运行时静态交付型：Dockerfile `COPY dist`，env 指向 |
| 无 compose | diarum、yuexi、bili-dl、whoami | compose 是「可选」节点，单容器部署不强制 |

### Makefile 与 scripts/build.sh 的分工

`scripts/build.sh` 是**唯一构建实现**（工具链定位 → 前端构建 → 产物入 embed 位 →
`go build ./cmd/<app>`，骨架与五条硬约定见 [build-script.md](build-script.md)）；
Makefile 只做薄封装并保留 dev/test/docker/clean 便捷目标。根目录的 `build.sh` 已全部
归位 `scripts/`（tuqie、whoami 于 2026-10-17 完成），仅剩 fork 仓 `GoldPrice`（Swift）
与 `ntfyx` 的 `build-app.sh` 按上游跟踪不动。

---

## 二、目录与文件职责

| 节点 | 放什么 | 不放什么 | 谁消费它 |
|---|---|---|---|
| `go.mod`/`go.sum` | 模块声明 | 多模块、go.work | CI（go-version-file）、dependabot gomod 段 |
| `cmd/<app>/` | 唯一 main 包：flag 解析、装配、version 变量 | 业务逻辑、embed 指令（embed 必须在独立包，路径相对包目录） | `go build ./cmd/<app>`、release 的 `-X main.version` |
| `internal/` | 私有业务包（api/store/config/db…） | 可被外部 import 的库（那去 `pkg/`） | main 包、测试 |
| `internal/web/` | embed.go + dist/ 暂存 + .gitkeep 占位 | npm 源码、node_modules | main 包静态托管、frontend-stage-command |
| `web/` | npm 工程全部：src、public、三件套、框架配置、pnpm-workspace.yaml | Go 代码（除登记的 web/embed.go 变体）、.nvmrc | CI（web-dir）、dependabot npm 段、开发服务器 |
| `static/`+`templates/` | Go 模板型前端的模板与静态资源 | npm 工程 | 所在 Go 包的 embed |
| `scripts/build.sh` | 唯一构建实现 | 一次性脚本（用完删，别沉淀） | 人、Makefile、CI smoke job |
| `scripts/docker-entrypoint.sh` | PUID/PGID 降权 + chown 数据目录 + exec su-exec | 业务启动参数（归 Dockerfile CMD） | Dockerfile COPY、check-docker.py 的 nonroot 检测 |
| `bin/` | go build 产物 | 入库产物（gitignore） | Dockerfile `COPY bin/<app>` |
| `dist/`（根） | 运行时静态交付型的 CI 暂存区 | 入库产物 | Dockerfile `COPY dist` |
| `data/` | 本地开发数据库与上传 | 入库（gitignore） | 本地运行；容器内路径由 env/compose 决定 |
| `docs/` `migrations/` `testdata/` | 设计文档、SQL 迁移、测试固件 | 构建产物 | 人、测试、migrate 命令 |
| `.github/workflows/ci.yml` | 薄调用 reusable workflow | 内联构建逻辑 | GitHub Actions |
| `.github/dependabot.yml` | 依赖更新（gomod/npm/docker/actions 段） | 手改 preset 正文 | Dependabot、check-repo-baseline.py |
| `.nvmrc` | 一个 Node 大版本号（26） | pnpm 版本（归 packageManager） | CI `node-version-file`、build.sh |
| `.dockerignore` | 排除源码/文档/本地数据 | **不得排除 `bin/`、`dist/`、`scripts/docker-entrypoint.sh`** | docker build、check-docker.py |
| `Dockerfile` | 装配：FROM pin + COPY 预编译产物 + 非 root + 探活 + exec form | 编译步骤、工具链版本声明 | CI image job、本地 docker build |
| `docker-compose.yml` | 单机部署：镜像 tag、端口、env、卷、healthcheck | build context 指向别处 | 部署人 |
| `Makefile` | 薄封装 + dev/test/docker/clean 便捷目标 | 构建实现本体 | 人 |
| `README.md` | 一句话定位、功能、快速开始、配置表、部署 | changelog | 人、check-repo-baseline.py（>400 字节） |
| `AGENTS.md` | AI 代理项目指令：目录结构、包管理器禁令、开发命令 | 过时的路径引用（迁移必改） | AI 代理 |

---

## 三、迁移前后对照（2026-10-17，第 0–4 批已落地）

迁移已完成。下表是**当前**的真实布局；迁移前的形态差异记在表下的「原始分歧」一段，
留作回溯用。◆=仍待迁移，✓=已合规/已迁移，✖=fork 豁免（不参与本契约）。

| 项目 | Go 模块/入口 | 前端·形态 | Docker·entrypoint | 状态 |
|---|---|---|---|---|
| shenshi | 根 + `cmd/shenshi` | web/·Vite（embed 位 `internal/web/dist`） | 根全套；`scripts/docker-entrypoint.sh` | ✓ 第 1 批 |
| feixi | 根 + `cmd/feixi` | web/·Vite（embed 位 `internal/web/dist`） | 根全套；`scripts/docker-entrypoint.sh` | ✓ 第 1 批 |
| qingye | 根 + `cmd/qingye` | web/·SvelteKit（根 `dist/` 暂存，运行时 `WEB_DIR`） | 根全套；直起 | ✓ 第 3 批 |
| mujian | 根 + `cmd/mujian` | web/·SvelteKit（embed 位 `internal/web/dist` + 根 `dist/` 暂存） | 根全套；CMD `su` 降权 | ✓ 第 3 批 |
| huozhi | 根 + `cmd/huozhi-server` | web/·SvelteKit | 根全套；compose(.yml)；`scripts/docker-entrypoint.sh` | ✓ 第 2 批 |
| bili-history | 根 + `cmd/bili-history` | web/·Nuxt（generate→`.output/public`） | 根全套；`scripts/docker-entrypoint.sh` | ✓ 第 2 批 |
| diarum | 根 + `cmd/diarum` | web/·SvelteKit（embed 位 `internal/static/build`，登记变体） | 根 Dockerfile；无 compose；`scripts/docker-entrypoint.sh` | ✓ 第 2 批 |
| zhichang | 根 + `cmd/zhichangd` | web/·SvelteKit（`web/embed.go` 变体） | 根全套；`scripts/docker-entrypoint.sh` | ✓ 第 4 批 |
| liuxia | 根 + `cmd/liuxia` | 无（Go 模板，内嵌在 `internal/web`） | 根全套；`scripts/docker-entrypoint.sh` | ✓ 第 4 批 |
| qbhive | 根 + `cmd/server` | `web/static`·纯静态 | 根 Dockerfile + compose | ✓ 第 4 批 |
| tuqie | 根 + `cmd/tuqie` | web/·Vite（`web/embed.go` 变体） | 根全套；直起 | ✓（`scripts/build.sh`） |
| whoami | 根 + `cmd/whoami` | 无（`internal/web` Go 模板） | 根 Dockerfile + .dockerignore；无 compose | ✓ 第 4 批 |
| qiansi | 根 + `cmd/server` | web/·Vite | 根全套；distroless 无脚本 | 无 | ✓（根 dist/ 暂存是标准一部分） |
| guanfu | 根 + `cmd/guanfu` | web/·SvelteKit | 根全套；`scripts/docker-entrypoint.sh` | scripts/build.sh | ✓（**仓内参照实现**） |
| chaxin | 根 + `cmd/server` | web/·纯静态（build.sh 手搓） | 根全套；直起 | Makefile + web/build.sh | ✓（npm=False 已登记） |
| yuexi | 根平铺 | 无（Go 模板+tailwind CLI） | 根全套；直起 | 无 | ✓ |
| bili-dl | 根平铺 | 无 | 无 Dockerfile | 无 | ✓（CLI 型） |
| docker-db-auto-backup | 根全平铺 | 无 | 根全套；root_exempt 已登记 | 无 | ✓ |
| WebMonitor | Python backend/ | `frontend/`·Vite | Dockerfile.slim + frontend/Dockerfile + compose×2 | 自有 | 部分豁免（自有镜像流程，go-dir 传空）；frontend→web 可选 |
| ntfy | 上游平铺 | web/·上游 | 根（上游 Dockerfile） | 上游 Makefile | ✖ fork 豁免 |
| GoldPrice / ntfyx | Swift | 无 | 无 | 根 build.sh / build-app.sh | ✖ fork 豁免 |

**当前统计**：前端命名 web×14 / frontend×1（WebMonitor，待其批次）/ 无×7；
Go 布局 根+cmd+internal×15 / 平铺×3（yuexi、bili-dl、docker-db-auto-backup）/
Python backend×1 / 上游×1；compose 后缀 **.yaml 已清零**；entrypoint 已全部归到
`scripts/docker-entrypoint.sh`（或直起、或不需要）；缺 .dockerignore 仅剩 bili-dl
（CLI 型、无 Dockerfile，不适用）。

**原始分歧（迁移前）**：前端命名 web×9 / frontend×4 / site×1 / internal/web×1 / 无×7；
Go 布局 根+cmd+internal×8 / server×2 / backend×3 / 平铺×4 / 上游×1；compose .yml×15 /
.yaml×2；entrypoint 五种位置并存；缺 .dockerignore×4。

> **仍存的不一致**：`yuexi` 是根平铺（`main.go` 在根、内部包在 `internal/`）且登记为 ✓，
> 而同样「根平铺 + Go 模板」的 liuxia 在第 4 批已 cmd 化 —— 两者形态现在不一致。
> 按本文第 1 节「cmd/ 为必需」与第 5 节人工清单「main 在 cmd/<app>/」，yuexi 实际未达标，
> 待单独排期（另：`docker-db-auto-backup` 的根全平铺亦未 cmd 化，CLI 型暂按可接受处理）。

### 迁移批次路线图

| 批次 | 仓库 | 内容 |
|---|---|---|
| 第 0 批 | .github | 本文；frontend.md 第四节 supersede；repo-baseline.md fork 名单去 whoami；README 契约表/参数表/dependabot.md 同步 |
| 第 1 批（试点） | feixi、shenshi | 覆盖全部迁移机理；CI 绿后复盘 SOP |
| 第 2 批 | diarum、huozhi、bili-history | site→web+cmd 化；backend/frontend 双迁；.yaml→.yml；补 .dockerignore；entrypoint→scripts/ |
| 第 3 批 ✓ 2026-10-17 | mujian、qingye | mujian 双迁+CGO+根 package.json 登记+dev.sh；qingye server→根+cmd 化+Makefile npm→pnpm。另：qingye 是第二个必须改 import 的仓库（包平铺在 `server/` 而非 `server/internal/`，67 处） |
| 第 4 批 ✓ 2026-10-17 | zhichang、qbhive、liuxia、whoami | deploy/ 回收；补 .dockerignore；compose 改名+entrypoint 移位；whoami 轻量收尾。liuxia 是本批唯一需 cmd 化的根平铺仓；whoami 曾被误登记为 fork，已更正 |
| 剩余 | yuexi | 根平铺未 cmd 化（见上「仍存的不一致」） |
| 豁免 | ntfy、healthchecks、GoldPrice、ntfyx | fork/上游跟踪，不动 |
| 明确不做 | 全部 | module path 统一改名（独立后续 PR，按仓库逐个）；web/embed.go 变体回改；新增 check 门禁（规范稳定后评估） |

---

## 四、迁移 SOP（12 步，一个仓库一个 PR）

0. **前置**：`git status` 干净；记录迁移前 CI 绿 + 本地 build/test 基线；建分支
   `refactor/project-structure`。
1. **git mv 保历史**（禁新建+删除，要能 `git log --follow`）：`git mv frontend web` /
   `git mv site web`；feixi 型只迁 npm 工程文件（index.html、package.json、
   pnpm-lock.yaml、pnpm-workspace.yaml、tsconfig.json、vite.config.ts、src/、public/、
   brand/），保留 `internal/web/{embed.go,dist/}`。
2. **Go 模块搬迁**（server/backend → 根）：`git mv server/go.mod server/go.sum .`；
   `mkdir -p cmd/<app> && git mv server/main.go cmd/<app>/main.go`（version.go 随行，
   否则 `-X main.version` 注入失效）；`git mv server/internal internal`。
   **module path 不动**，import 零改动。
3. **embed 外移**（main 移入 cmd/ 后必做，embed 路径相对包目录）：新建
   `internal/web/embed.go`（范式：`//go:embed all:dist` + `FS()`/`HasBuild()`，
   抄 guanfu）+ `internal/web/dist/.gitkeep`（被 .gitignore 挡住则 `git add -f`）；
   main.go 删 embed import 与指令，`fs.Sub(embedded,"dist")` → `web.FS()`。
   坑：`all:` 前缀不能丢（`_app/` 类目录会被静默漏掉）。
   **新包必须自带测试**（`internal/web/embed_test.go`）：这个包是迁移凭空造出来的，
   原先内嵌逻辑写在 `main.go` 里本来就不在被覆盖的包内，一旦独立成包，
   codecov/patch（target 80%）必然判新增行未覆盖而打红 PR。
   范式：断言 `HasBuild()` 与 `FS()` 内容自洽（占位态 / 构建态都要成立）+ `FS()` 可遍历；
   Go 模板型（liuxia 系）则断言 `StaticFS` 非空、关键模板可读、`SWVersion` 形态。
4. **前端三件套**：package.json / pnpm-lock.yaml / pnpm-workspace.yaml / 框架配置 /
   src / public 一起 mv；`.nvmrc` 留根不动；node_modules、dist、build、.svelte-kit、
   .output 不迁。坑：**lockfile 必须与 package.json 同级**，分居两地 CI
   `--frozen-lockfile` 即失败。
5. **构建脚本**：scripts/build.sh 改三处路径（前端目录、embed 暂存目标、
   `go build ./cmd/<app>`）；Makefile web/vet/clean 目标同步；dev.sh 改
   BACKEND_DIR/FRONTEND_DIR；根 build.sh 移入 scripts/。
6. **Docker 四件套**：`git mv docker-entrypoint.sh scripts/docker-entrypoint.sh`
   （或 entrypoint.sh 同名迁移）；`git mv deploy/docker-compose.yml docker-compose.yml`；
   Dockerfile COPY 源改 `scripts/`（老式 COPY+RUN chmod 可顺手合一 `--chmod=755`）；
   compose `.yaml→.yml`；**.dockerignore 同步是最大的坑**——排除名单里的旧目录名
   （`internal/web/`、`frontend`、`server/dist/`）要改新名，且任何情况下不得排除
   `bin/`、`dist/`、`scripts/docker-entrypoint.sh`（否则 `COPY` 报的却是
   「file not found」这种误导性错误）；缺 .dockerignore 的补上。
7. **CI 输入**（ci.yml）：`go-dir: server|backend → .`；`web-dir: frontend|site|internal/web → web`；
   image/release 补 `main-package: ./cmd/<app>`；`frontend-stage-command` 拷贝目标改新
   embed 位；smoke 等自有 job 的 `go-version-file`/`cache-dependency-path` 别漏。
   只改值、不增删 input 名（传未声明 input 会 `startup_failure`，无 job 无日志）。
8. **dependabot**：gomod 段 `directory: /server|/backend → /`；npm 段
   `/frontend|/site|/internal/web → /web`；头部注释同步。
9. **文档**：README 的目录结构/快速开始/开发命令/构建节的旧路径；AGENTS.md 的
   目录结构+开发命令+包管理器三节；CHANGELOG 与 docs/review-*.md 历史快照**不改**。
10. **.github 仓库同步**（与业务仓同 PR 或紧随，保证 check 脚本永远与工作区现状一致）：
    `check-repo-baseline.py` REPOS 的 `web` 值逐行改；`check-docker.py` 的 compose
    路径随迁随改；`.github/README.md` 第八节参数表对应行；`docs/dependabot.md`
    第二节表格。
11. **本地验证**：构建全链 → `go vet ./... && go test ./...` → `go run ./cmd/<app>`
    起服务 curl 首页（embed 链真验证）→ `docker build` 干跑（装配式要求先有 bin/）
    → 三个 check 脚本全绿。
12. **push 与 CI**：push 后以 `git ls-remote` 核对远程 SHA 为准；CI 全 job 绿
    （含 image 双架构与 smoke）才算完成。

---

## 五、自检

结构契约目前**没有**专用脚本（门禁待规范落地稳定后评估，见第三节「明确不做」），
靠既有脚本 + 人工清单双保险：

```bash
# 既有脚本已经能抓到的部分
cd /Users/yufei/Git/.github
python3 scripts/check-repo-baseline.py          # 前端三件套位置（REPOS 的 web 值）
python3 scripts/check-docker.py                 # .dockerignore 不得排除 COPY 源
python3 scripts/check-reusable-inputs.py        # CI input 名契约

# 人工清单（每个迁完的仓库过一遍）
# - [ ] go.mod 在仓库根，main 在 cmd/<app>/，无第二个 main 包
# - [ ] 前端目录叫 web/，三件套齐全且同级
# - [ ] embed 位在 internal/web/dist，.gitkeep 占位已提交
# - [ ] Dockerfile/compose/.dockerignore 在根，entrypoint 在 scripts/docker-entrypoint.sh
# - [ ] README/AGENTS.md 无旧路径残留（grep -rn 'server/\|frontend/\|site/' 排除历史快照目录）
```
