# 发布规范

发行环节最容易犯的错误是「版本号在两个地方不一样」。所以要把它变成**只有一个来源**：
git tag。

```
git tag v1.2.3  →  reusable-release.yml  →  -X main.version=v1.2.3  →  二进制 --version
                →  docker/metadata-action →  镜像 tag v1.2.3 / 1.2 / latest
```

版本号不写在任何文件里。手写过一次的人都知道第二次会发生什么：忘了改，
发布了才发现二进制里是上个版本，只能再发一个 `v1.2.4` 去纠正。

---

## 一、注入版本号

main 包新增 `version.go`：

```go
package main

// version 由 CI 注入：-ldflags "-X main.version=v1.2.3"；本地 go build 时为 "dev"。
var version = "dev"
```

**这个变量必须被真正引用**（接一个 `-version` flag 或写在启动日志里）。
否则链接器会把它当未使用符号优化掉，`-X` 静默失效——
不报错、不警告，二进制 `--version` 永远打印 `dev`。这是最容易忽略的一处。

| 仓库 | 变量名 | 原因 |
|---|---|---|
| 全部（默认） | `main.version` | 共享 workflow 的 `version-var` 默认值 |
| diarum | `main.Version` | 该仓库变量是大写，用 `version-var: main.Version` 覆盖 |
| ntfy | 额外 `main.commit` / `main.date` | `--version` 展示三段信息，靠 `extra-version-vars` 追加 |

---

## 二、打 tag 的流程

```bash
# 1. 改完代码并提交到 main
git switch main && git pull

# 2. 打不可变 tag
git tag -a v1.2.3 -m "v1.2.3: 一句话说明这次改了什么"
git push origin v1.2.3
```

打 tag 会同时触发三条流水线（`ci.yml` 里 `push.tags` + `release`）：

| 作业 | 产出 |
|---|---|
| `test` | 覆盖率 → Codecov |
| `image` | 版本化镜像 `v1.2.3` / `1.2` / `sha-<40hex>` / `latest` |
| `release` | 5 平台二进制 + 校验和，挂到 GitHub Release |

> `release` 作业要求 **GitHub Release 先发布**（`on.release: published`）。
> 只 push tag 不会出二进制——历史上踩过一次：tag 推上去了、镜像也出来了，
> 就是没有 Release 里的二进制，因为人是 yaoyao 在 UI 上点「Publish release」。

**所以完整顺序是**：push tag → 等 image 绿 → 在 GitHub 上基于这个 tag 创建 Release
（可以写 release notes）→ release 作业才会跑。

### 版本别敷衍

| 改动性质 | 版本 |
|---|---|
| 修 bug、patch | `v1.2.4` |
| 加功能、向后兼容 | `v1.3.0` |
| 破坏性变更 / 需要迁移数据 | `v2.0.0` |

自托管项目没有外部消费者，但**你自己是消费者**：半年后回头看 `docker pull v1.2.3`
能不能直接用，取决于当时有没有诚实标注这是 breaking change（尤其数据迁移）。

---

## 三、CHANGELOG

不是所有仓库都需要，但**有用户可见变更的仓库建议有**。格式用 Keep a Changelog
极简版：

```markdown
# Changelog

## [1.3.0] - 2026-10-10

### Added
- 任务支持按截止日期排序

### Fixed
- 修复 iOS 上主屏图标为黑底（maskable 图标改为独立位图）

### Changed
- **破坏性**：配置文件 `config.yml` 的 `db.path` 改为 `database.path`
```

粗体标 breaking，这是唯一让它在三个月后还能被注意到的办法。

---

## 四、发布后的验证

发完不是结束。按顺序核三件事：

```bash
# 1. 二进制版本号正确（不是 dev）
./bin/app --version

# 2. 镜像 tag 齐全
docker pull ghcr.io/felix2yu/<repo>:v1.2.3

# 3. 容器能起来 —— -rm 一次性跑，退出码 0 才算数
docker run --rm ghcr.io/felix2yu/<repo>:v1.2.3 --version
```

**第 3 步最容易漏**：CGO 项目在本地编出来跑得好好的，进了容器却报
`GLIBC_2.43 not found` 或 `Error loading shared library libc.musl-x86_64.so.1`——因为编译机与运行镜像的 libc
不是同一套。共享 CI 用 `cgo: glibc` / `static` 两种形态分别处理这件事，
详见 [README 第四节](../README.md#四cgo-形态off--glibc--static)。

---

## 五、回收旧版本

| 项 | 策略 |
|---|---|
| 镜像 | 保留最近 10 个 tag 即可，手工清 `sha-*` 归档（GHCR 控制台或 API） |
| Release | 不要删——有人挂着直接下载链接 |
| tag | **永远不要删也不要移动**。它是版本号唯一来源，移动等于让已发布的版本串了 |
