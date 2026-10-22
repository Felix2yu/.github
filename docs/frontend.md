# 前端工具链契约

13 个 Go 仓库里有 10 个带前端，形态各不相同（Vite / SvelteKit / Nuxt）。
但有一件事是共通的：**CI 绿不等于产物对**。构建能不能过是一回事，
产物落在哪个目录、用什么版本编出来、能不能被 Go 的 `go:embed` 找到，是另一回事。

本文档把「Node 版本、包管理器、产物位置」三件事钉成契约，避免每个仓库各写一套。

> CI 侧的实现细节（reusable workflow 怎么读这些声明）见
> [README 第七节](../README.md#七已知坑)。本文是「仓库侧要交什么」的那半边。

---

## 一、三个文件，一个都不能少

| 文件 | 位置 | 内容 |
|---|---|---|
| `.nvmrc` | **仓库根**（不是前端目录） | 一个版本号，如 `26` |
| `packageManager` | **前端目录**的 `package.json` | `"pnpm@11.22.0"` |
| `pnpm-lock.yaml` | 与上面的 `package.json` 同级 | pnpm 生成，勿手改 |

**缺任何一项 CI 直接失败，不设兜底版本。** 这不是严厉，是因为放过它就要付更大的代价：

- 以前靠 lockfile 是否存在来「猜」包管理器，`bun.lock` 与 `package-lock.json` 并存时
  把 CI 猜偏了——用的是 npm 装 pnpm 的 lock，报出来的错误却完全不像装错了东西。
- 以前 Node 版本写在 workflow 里，于是「升 Node」要改 13 个仓库的 workflow。

现在版本由**各仓库自己的声明**决定，升级只要在仓库里改一行，不用动 13 个 workflow。

### 常见错误

**`.nvmrc` 放进了前端目录。** CI 的 `node-version-file` 是相对仓库根的，放错位置等于
没放——报错是「读不到文件」，容易被误判成内容写错了，于是有人去改文件内容。

**留下多个 lockfile。** `package-lock.json` / `yarn.lock` / `bun.lockb` 与
`pnpm-lock.yaml` 同时存在，就是给歧义留门。迁到 pnpm 之后要删掉旧的。

---

## 二、包管理只能是 pnpm

### 为什么是 pnpm 而不是 npm

不是偏好问题。npm 会把同一个依赖装成多份副本（「扁平化」之后依然可能重复），
最坏的情况不是占磁盘——是运行时存在两份不同版本的同一个库，谁先生效取决于
解析顺序，现象是「本地正常、CI 偶发报某个方法不存在」。

统一到 pnpm 之后的唯一额外成本是：**它的 peer 依赖检查更严格**。

### `overrides` 的坑

npm 风格写在 `package.json` 顶层的 `overrides` **不会被 pnpm 读取**。pnpm 认的是：

```yaml
# pnpm-workspace.yaml
overrides:
  "vulnerable-pkg@*": "1.2.3"
peerDependencyRules:
  allowedVersions:
    typescript: "^6"        # 与 @typescript/native（TS 7）并存
```

或者 `package.json` 里的 `pnpm.overrides`。

**这意味着迁移前它们很可能一直是空转的**——写了、CI 也绿了、但实际从未生效。
迁完要用 `pnpm why <包名>` 逐条核实是否真的置换了版本。

### `--legacy-peer-deps` 不存在

pnpm 没有对应物，该入参已废除。遇到 peer 冲突的正确处理顺序：

1. 升级那个 peer 依赖本身（多半是它落后了）
2. 确实无解的，写 `peerDependencyRules.allowedVersions` 并注明原因
3. 不要在两者之间用撸平依赖的方式绕过

### pnpm 10+ 不跑 install 脚本

这是 pnpm 的安全默认值。本仓群唯一带 install script 的包是 macOS-only 的 `fsevents`，
CI 跑 linux 不受影响；本地需要时在该仓 `pnpm-workspace.yaml` 写：

```yaml
onlyBuiltDependencies:
  - fsevents
```

---

## 三、Node / pnpm 版本策略

| 项 | 当前 | 升级触发条件 |
|---|---|---|
| Node | 全组织统一 `.nvmrc` = `26` | 独立一次 PR：先在一个 canary 仓库清 CI 缓存跑通 |
| pnpm | `pnpm@11.22.0`（`packageManager` 唯一来源） | 同上 |

**为什么不是最新版**：

- Node：8 个前端仓库已拉平到 26，与 Docker 里 `node:26-alpine` 对齐。三个版本要同步改：
  `.nvmrc` → Dockerfile → CI（CI 读 `.nvmrc`，所以只需前两个）。
- pnpm 11.22.0 而不是 12：现有 lockfile 全是 `lockfileVersion: 9.0`，由 pnpm 11 生成。
  升大版本等于同时赌 lock 格式与 `--frozen-lockfile` 的匹配，**必须独立成一次 PR**，
  先在 canary 仓库清缓存跑通再铺开。

> 本机装的 pnpm 比声明新时，pnpm 会自己按 `packageManager` 切到 11.22.0
> （需要联网解析一次）。这是它的 corepack 行为，不是报错。

**canary：司寤 `siwu`（2026-10-22 接入）声明 `pnpm@12.14.0`**，是当前唯一跑 12 的仓库。
实测 `pnpm-lock.yaml` 仍是 `lockfileVersion: 9.0`，`pnpm install --frozen-lockfile` 26ms 通过，
Vite 8 构建与 `pnpm run check` 全绿 —— 即「12 会改 lock 格式」这个假设在当前版本不成立。
铺开仍需独立一次 PR：12 新增的供应链策略校验会在安装时把「发布过新」的包写进
`minimumReleaseAgeExclude`，需要逐个仓库确认 diff 是否只包含这一项。

---

## 四、产物放哪

装配式 CI 要把前端产物搬到 Go 的 `go:embed` 位置，靠的是 `frontend-stage-command`。

> **本节原立场已限位（2026-10-16，见 [project-structure.md](project-structure.md)）**：
> 「各仓库的位置不一致是历史原因，不要为了统一而统一」对**已运行服务的运行时产物路径**
> （容器内 `/app/web/dist`、`QIANSI_WEB_DIR` 这类 env 契约）依然有效——那确实不该为
> 统一而统一；但**源码目录结构**（前端目录名、Go 模块位置、embed 位、entrypoint 位置）
> 自该日起按 project-structure.md 有意统一。理由：目录名分裂使 CI 输入、dependabot
> `directory`、check-repo-baseline 的 `REPOS` 三处重复维护同一份事实，且 internal/web
> 混居型（feixi）已违反「web/ 是前端工程」的心智模型。

各仓库当前的产物位置如下，**迁移批次与目标形态见 project-structure.md 第三节**：

| 仓库 | 前端目录 | 产物落到 |
|---|---|---|
| shenshi | `web` | `web/dist` → `internal/web/dist`（内嵌进二进制；2026-10-16 随模块回根从 `server/dist` 迁来） |
| qiansi | `web` | `web/dist` → 镜像 `/app/web/dist`（运行时读取，不 embed） |
| mujian | `frontend` | `frontend/dist` → `backend/dist` |
| huozhi | `frontend` | 同上 |
| diarum | `site` | `site/build` |
| bili-history | `frontend` | `frontend/.output/public`（Nuxt） |
| ntfy | `web` | `web/build` → `server/site`（含 `index.html → app.html` 改名） |

**Nuxt / SvelteKit 的产物目录与 Vite 不同**，这也是 `web-build-command` 存在的原因
（bili-history 是 `pnpm run generate`）——别假设都是 `pnpm run build && dist/`。

---

## 五、构建命令与 check

带类型的项目（SvelteKit）要把类型检查并入构建，否则 CI 里 build 绿但 `svelte-check`
有问题也没人知道：

```json
"scripts": {
  "check": "svelte-kit sync && svelte-check --tsconfig ./tsconfig.json",
  "build": "vite build"
}
```

CI 侧用 `web-build-command: pnpm run check && pnpm run build`（qingye 就是这样接的）。

---

## 六、lint 与格式化

每个前端工程都要有 `eslint.config.js`（flat config）与 `prettier.config.js`，
`package.json` 里两个脚本：

```json
"lint": "prettier --check . && eslint .",
"format": "prettier --write ."
```

`lint` 放 `prettier --check` 在前面：格式不一致直接失败，不用等 eslint 跑完。

### 规则基线

`js.configs.recommended` + `ts.configs.recommended` + 框架推荐集（`eslint-plugin-svelte`
或 `eslint-plugin-react-hooks`）全开，`eslint-config-prettier` 收尾关掉与格式化冲突的规则。

**只有两类存量风格规则降为 `warn`**，其余一律 `error`：

| 规则 | 为什么放宽 |
|---|---|
| `@typescript-eslint/no-explicit-any` | 存量里成片的 `API.get<any>` 与 echarts 配置对象，一次性改造等于重写前端 |
| `svelte/prefer-svelte-reactivity` | `URLSearchParams` / `Map` / `Set` 与响应式无关的场合占多数，逐处判定成本高于收益 |

正确性规则（`each` 块的 key、未使用变量、空块、`no-undef`）**必须真修**，不许降级——
`{#each}` 缺 key 会让 Svelte 在列表增删时复用错 DOM 节点，这是可观测的 bug。

### 缩进与 `.editorconfig` 对齐

`prettier.config.js` 用 `useTabs: false, tabWidth: 2`，与 `.editorconfig` 的
「TS/Svelte/CSS 统一 2 空格」保持一致。`huozhi` 是唯一的历史例外（`useTabs: true`），
改动它会产生全仓重排 diff，单独一轮处理。

### 首次接入的 diff 会很大

`prettier --write` 会重排全部源文件（引号、尾逗号、换行、Tailwind class 顺序）。
这是机械改动，review 时看 `git diff -w` 或者直接看配置，别逐行读。

---

## 七、自检

```bash
python3 scripts/check-repo-baseline.py          # 「前端三件套」列
```
