# 依赖更新规范

模板：[`preset/dependabot.yml`](../preset/dependabot.yml)（整份覆盖，别手改副本）
自检：`scripts/check-repo-baseline.py` 的「依赖更新」列

---

## 一、为什么要有这份东西

不写 `.github/dependabot.yml` 也不会有任何报错。依赖就安静地停在某个版本上，
半年后发现：

- `https://nvd.nist.gov` 上挂着 CVE，修复版本已经发了一年
- `go.sum` 里的包被上游标记为 deprecated
- 想升 `vite` 时发现从 7 到 8 中间隔了三个大版本，一次性跨不过去

**依赖更新不是一个决定，是一个节奏。** 每周一次、小步跨 version，
永远比半年一次大跳跃安全。而节奏的前提是配置。

---

## 二、配置的三处必改

复制模板后：

| 处 | 改成 |
|---|---|
| `gomod` 段 | 后端是 Python 换成 `pip`；纯前端删掉 |
| `npm` 段的 `directory` | 真实前端目录。本仓群已有：`/web`（shenshi/qiansi/qingye/ntfy）、`/frontend`（mujian/huozhi/bili-history/WebMonitor）、`/site`（diarum） |
| `docker` 段 | 没有 Dockerfile 的删掉 |

**`github-actions` 段一律保留。** 它管的是 `ci.yml` 里 `actions/checkout` 之类第三方
action 的版本；`@v1` 这种远程 reusable workflow 引用它管不了（得手动 bump，
见 [README 第六节](../README.md#六升级流程)），两者互不冲突。

---

## 三、为什么按 semver 级别分组

不分组时，一次例行更新是十几个 PR：`modernc.org/sqlite` 一个、`vite` 一个、
`eslint` 一个……每个单独看 CI、单独点合并，最后所有人都不看了。

分组后：

| PR | 内容 | 合并方式 |
|---|---|---|
| `…-minor-patch` | 全部 patch + minor | CI 绿即合。它们在同一个 PR 里**一起过了 CI**，等于交叉验证过 |
| `…-major` | 全部 major | 读 breaking changes；合并后本地跑一次 build |

> 代价：一个 major 组里若同时有三包，其中一包 breaking 会挡住另外两包。
> 这时单独处理那一包，别整组丢弃。

---

## 四、两个容易被忽略的字段

**`open-pull-requests-limit: 10`** —— 默认只有 5，达到上限后 Dependabot **静默
停止提新 PR**，不报错、不给任何提示。等发现时已经积压了三周的安全更新。

**`timezone: Asia/Shanghai`** —— `friday 00:00 UTC` 在东八区是**周五早上 8 点**，
到公司一并处理掉；不写时区会被当成 UTC，落在周六上午。

---

## 五、合并策略（本仓群统一）

| 项 | 值 | 理由 |
|---|---|---|
| 合并方式 | **Squash** | `Bump X from a to b` 这类单 commit 没有保留价值；压成一条让 main 保持「一个变更一个 commit」 |
| 自动合并 | **不开** | 必须过 CI，且合并者要看一眼标题里有没有 major 跳跃 |
| 清理节奏 | 每周五同一批 | 攒一周一次清完；天天清会把注意力切碎 |
| CI 红 | 先分清 flaky 还是真失败 | 真失败多半是 breaking change。不要重跑第三遍指望它绿 |

### 合并前的四步

1. **看 CI**——绿不绿不是「建议」，是一票否决。
2. **看是否 major**——`Bump X from 1.2.3 to 2.0.0` 就是，去读 changelog。
3. **看改了什么**——只动 lockfile 基本安全；连 `package.json` 的 `dependencies`
   一起改了，说明上游动了版本范围，要多留意。
4. **合完本地验一次**——尤其动到构建链的包（`vite` / `vite-plugin-pwa` / `esbuild` /
   `rolldown`）。**CI 绿只代表能编译，不代表产物还对**：PWA 的 `sw.js` 就是产物，
   这类升级后要跑 `python3 scripts/check-pwa.py <repo>` 复查一遍。

### 前端 peer 冲突

pnpm 严格得多，常见报错 `ERR_PNPM_PEER_DEP_ISSUES`。处理优先级：

1. 先升级那个 peer 依赖本身（多半是它落后了）
2. 确实无法同时满足的，在 `pnpm-workspace.yaml` 写
   `peerDependencyRules.allowedVersions`，**并注明原因**
3. `--legacy-peer-deps` 在 pnpm 里没有对应物，不要用撸平的方式绕过

---

## 六、锁定某个版本

```yaml
    ignore:
      - dependency-name: "eslint"
        update-types: ["version-update:semver-major"]
```

**必须写注释说明理由。** 三个月后没人记得为什么要锁它，`eslint` 已经在墙外落后三个
大版本，而删除这行的人也无法判断当时是不是有正当理由。

---

## 七、Dependabot 管不到的

| 项 | 原因 | 谁负责 |
|---|---|---|
| `Felix2yu/.github/...@v1` | 远程 reusable workflow 引用，不认 | 手动：打 `vX.Y.Z`、移 `v1`、逐仓验证 |
| pnpm 版本 | 在 `package.json` 的 `packageManager` 字段，不是依赖 | 手动改 + 重生成 lockfile |
| Node 版本 | 在 `.nvmrc` | 手动改，注意与 Dockerfile 的 `node:XX-alpine` 对齐 |
| Go 版本 | 在 `go.mod` 的 `go` 指令 | 会级联影响 CI，单独一次 PR |
