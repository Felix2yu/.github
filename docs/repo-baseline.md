# 仓库基线规范

业务代码写得再好，仓库本身可以更替握不了——缺 `.gitignore` 会提交 `node_modules`、
缺 dependabot 配置依赖就永远停更、缺 CI 接入就没人验证分支能不能编译、缺 LICENSE
别人想复用还得先发封邮件。这些全部「不做也不会报错」，属于典型的静默缺陷。

本文把「每个自研仓库都该有的东西」列成一张表，`scripts/check-repo-baseline.py`
负责检查。新仓库接入时照着勾，老仓库欠了什么一眼能看到。

---

## 一、清单总表

| 类别 | 项 | 判据 | 缺失后果 |
|---|---|---|---|
| 文档 | `README.md` | 存在且 > 400 字节（避免只有仓库名） | 三个月后自己都忘了怎么跑起来 |
| 文档 | `LICENSE` | 自研仓库必须有（fork 沿用上游） | 别人不能合法复用 |
| 依赖更新 | `.github/dependabot.yml` | 见 [dependabot.md](dependabot.md) | 依赖停更、CVE 无人知 |
| CI | `.github/workflows/ci.yml` | 调用共享 reusable workflow 且 pin `@v1` 或 `@vX.Y.Z` | 分支能不能编译没人知道 |
| 前端三件套 | `.nvmrc` | 仓库根 | CI 无版本来源，直接失败而非兜底 |
| 前端三件套 | `packageManager` | `pnpm@<版本>`，写在**前端目录**的 `package.json` | 同上 |
| 前端三件套 | `pnpm-lock.yaml` | 与 `package.json` 同级 | `--frozen-lockfile` 直接失败 |
| 忽略规则 | `.gitignore` | 覆盖 `node_modules`、`dist`/`build`、Go 产物 | 把依赖和产物提交进仓库 |
| 忽略规则 | `.dockerignore` | 不得排除 CI 产物（见 [docker.md](docker.md)） | `CopyIgnoredFile` |

---

## 二、README 该写什么

不需要长篇文档，但至少能回答四个问题：

```markdown
# 项目名 —— 一句话说清它解决什么问题

## 功能
（三五条，不是 changelog）

## 快速开始
docker compose up -d        # 或本地怎么跑

## 配置
| 环境变量 | 默认 | 说明 |

## 部署
（镜像名、数据目录、需要挂载什么）
```

**反面教材**：一份只有标题和徽章的 README。徽章告诉你 CI 是绿的，但没告诉你服务监听
哪个端口、数据落在哪、升级时该注意什么——真正要用它的时候还是得读源码。

---

## 三、LICENSE

自研仓库统一用 **MIT**：

```
MIT License

Copyright (c) 2026 Felix2yu
```

与 mujian / qiansi 现有的一致——复制它们的 LICENSE 即可，不要手写协议正文
（手写容易漏掉最后的「AS IS」免责段落，那一段才是真正起作用的）。

**fork 的第三方仓库沿用上游，不要覆盖**：ntfy / healthchecks 是 Apache 2.0、
docker-db-auto-backup 是 BSD 3-Clause，改掉会在下次 merge 上游时变成永久冲突。

> 顺带一提：`CC BY-NC` 这类知识共享协议**不适合用在源代码上**（它没有专利条款，
> 且 NC 条款会禁止商业使用，包括「公司内网跑一个」这种）。看到某个仓库用了它，
> 值得回头确认是不是当初随手选的。

---

## 四、CI 接入

见 [README 第一节](../README.md#一接入)。脚本检查两件事：有没有调用共享 reusable
workflow，以及有没有写 `@main`。

> `@main` 是移动分支：本仓库一改，全部 13 个调用方同时跟着变，**没有任何一个仓库
> 做过验证**。正确的做法是 pin `@v1`（移动 tag）或 `@v1.0.0`（不可变 release tag），
> 升级时走 README 第六节的流程逐个验证。

---

## 五、前端三件套

`.nvmrc` + `packageManager` + `pnpm-lock.yaml`，缺任何一项 CI 直接失败，
**不设兜底版本**。这是有意的（详见 [frontend.md](frontend.md)）。

三个文件的位置也要注意：

```
仓库根/.nvmrc                 ← Node 版本，在根
/web/package.json             ← packageManager，在前端目录
/web/pnpm-lock.yaml           ← lockfile，跟 package.json 同级
```

`.nvmrc` 放前端目录里是常见错误——CI 读的是 `node-version-file: .nvmrc`（相对仓库根），
放错位置等于没放，而且报错信息是「读不到文件」，容易误以为是没写。

另外：`package-lock.json` / `yarn.lock` / `bun.lockb` **不要留在仓库里**。
它们会让包管理器识别产生歧义，历史上已经因此把 CI 猜错过一次。

---

## 六、fork / 第三方仓库

`healthchecks`、`whoami`、`GoldPrice`、`ntfyx` 这类以跟踪上游为主的仓库，
README / LICENSE 沿用上游即可，不参与本仓群契约。脚本默认跳过它们（`--all` 可见）。

**判断标准**：如果你打算定期 merge 上游，`upstream/main` 改到的文件就是你的禁区；
如果只是借来部署，那它就是自研仓库，按清单补齐。

---

## 七、自检

```bash
python3 scripts/check-repo-baseline.py            # 全部自研仓库
python3 scripts/check-repo-baseline.py shenshi    # 单个仓库
python3 scripts/check-repo-baseline.py --all      # 含 fork
```
