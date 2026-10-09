# 容器镜像规范

`Felix2yu/*` 仓库里 16 个有 Dockerfile 的项目，形态都不复杂：一个 Go（或 Python）
后端，多半带一个前端。镜像出问题的地方却高度雷同——**都以 root 跑、都跨 CentOS 时代
遗留下来的 root 习惯、都在 `.dockerignore` 里埋了雷**。

本文把「装配式镜像 + 非 root 运行 + 可探活」固化成契约，`scripts/check-docker.py`
负责把它变成一条命令能看完的表格。

---

## 一、镜像形态：装配式（唯一推荐形态）

编译期依赖留在 CI，Dockerfile **只做拼装**：

```
CI job                     Docker 构建上下文             运行时镜像
─────────────────────     ────────────────────────     ─────────────────
go build -o bin/app   →   COPY bin/app /usr/local/bin  →  只有二进制
pnpm build           →   COPY dist ./dist         →  只有静态产物
```

好处不是「镜像小」这种空话，而是三件具体的事：

1. **工具链版本只有一个来源**。`node_modules`、Go 工具链不进镜像，就不会出现
   「Dockerfile 里悄悄装了 node 22、而仓库 `.nvmrc` 写 26」这种漂移。
2. **多架构不需要 qemu**。arm64 二进制在 arm64 runner 上编译（见共享 CI 的
   `reusable-image.yml`），镜像层只拷 result。
3. **构建失败发生在 CI，不是半夜的服务器上**。装配式要求 `bin/` 与 `dist/` 在构建
   前就存在，缺产物是 `COPY` 直接红，而不是运行到一半 500。

### 装配式的前提

`.dockerignore` **不得排除 CI 落下来的产物**——否则构建时报：

```
COPY failed: file not found in build context or excluded by .dockerignore
CopyIgnoredFile: attempted to copy "bin/app" but the file is excluded
```

这条错误信息最坑的地方在于：它报的是「找不到」，而不是「被忽略了」。第一次遇到的人
会去查 CI 有没有产出那个文件，查半天发现文件好好躺在目录里。

> `scripts/check-docker.py` 用**上下文感知**的方式判这条：先解析 Dockerfile 里每个
> `COPY` 的源路径（跳过 `--from=` 的多阶段复制），再看 `.dockerignore` 有没有静态排除
> 它。不会因为 `.dockerignore` 里出现 `dist/` 就无条件报错——很多仓库排除 `dist/`
> 是对的（前端在容器内重新构建），只有当 Dockerfile 真的要 `COPY dist` 时才算冲突。

---

## 二、非 root 运行

容器里的 root 与宿主的 root 是同一个 uid 0。进程一旦逃逸（哪怕只是一个能写任意路径的
漏洞），拿到的就是宿主 root。自托管服务长期暴露在公网，这条不是理论风险。

### 三种降权方案，按场景选

| 方案 | 适用 | 做法 | 已有仓库 |
|---|---|---|---|
| A · distroless nonroot | 无 shell 需求、不写数据卷 | `FROM gcr.io/distroless/static:nonroot`（uid 65532） | qiansi（待切换） |
| B · 静态 `USER` | 镜像内有固定数据目录，宿主无需对齐 | Dockerfile 里 `RUN adduser` + `USER <name>` | RSSRob / WebMonitor / qbhive / tuqie |
| C · `su-exec` + PUID/PGID | **绑定挂载了宿主目录** | 非 root 用户 + 入口脚本按 PUID/PGID 改 uid 并 `chown` 数据目录 | shenshi / liuxia / diarum / mujian / bili-history |

**判定标准只有一条**：容器是否需要写宿主的绑定挂载目录。需要 → 方案 C；不需要 → A 或 B。

### 为什么需要 PUID/PGID（方案 C 的全部理由）

容器内 uid 与宿主 uid 是同一套数字。宿主机上 `./data` 属于 uid 1000，
容器里跑 uid 100 的 `shenshi`，就对这个目录**没有任何写权限**——SQLite 以 WAL 模式
打开时需要对目录本身可写，症状是：

```
error 14: unable to open database file
```

而 `chmod 777 data` 只是把问题推迟到下一次备份恢复之后重新出现。正确做法是把容器内
用户的 uid/gid 改成宿主 directory 的属主：

```sh
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
groupmod -g "$PGID" appuser 2>/dev/null || true
usermod  -u "$PUID" -g "$PGID" appuser 2>/dev/null || true
chown -R "${PUID}:${PGID}" /data
exec su-exec "${PUID}:${PGID}" "$@"
```

四个细节都不能省：

- **`groupmod`/`usermod` 要 `|| true`**：与镜像里已有用户 uid 冲突时失败是常态，
  不该让容器起不来。
- **`chown -R` 必须覆盖整个数据目录**：历史遗留的 root 属主文件（比如某次用 root
  跑过）会残留下来继续挡住写入。
- **`exec`**：降权后的进程要替换 PID 1，否则信号收不到，`docker stop` 会等到 10 秒
  超时后 SIGKILL——SQLite 的 WAL 就可能来不及 checkpoint。
- **默认值要有**：不传 PUID/PGID 也得能跑起来，默认 `1000:1000`（macOS/Linux 首个
  普通用户通常就是这个）。

> 「慎始」的默认 `PGID=100` 是 macOS 上 `staff` 组的值，为了让宿主 Finder 能直接读写
> `./data`。其它仓库统一用 `1000:1000`，别照抄这个特例。

### 什么情况下保留 root

不是所有容器都能降权，`scripts/check-docker.py` 允许在 `REPOS` 里登记
`root_exempt` 并写明理由，典型的合理豁免：

- **要访问 `/var/run/docker.sock` 做 musl 之外的宿主操作**（`docker-db-auto-backup`：
  它得读宿主任意容器卷做备份，降权后连备份源都读不到）
- 上游镜像，改了会打断下次 merge（`ntfy` 的构建镜像 `Dockerfile-build`）

**豁免必须写理由**。写出来的一律不报错，脚本会把它打印成「已登记的例外」而不是沉默。

---

## 三、基镜像必须 pin

```dockerfile
FROM alpine:3.24            # 对
FROM alpine@sha256:...      # 更对
FROM alpine                 # 错：等价于 :latest
FROM alpine:latest          # 错
```

`latest` 不是「最新版」的意思，是「构建时恰好是这个」的意思。同一份 Dockerfile，今天
构建出 3.24，三个月后构建出 3.27；镜像 digest 变了、行为变了，而 Dockerfile 一行没改。

唯一尴尬的是 distroless：它没有版本 tag，只有 `latest` / `nonroot` / `debug` 三个
变体。要 pin 只能写 digest（`image@sha256:...`），或者在一句注释里写明「此处依赖
distroless 无版本策略」。

> 运行时我们自己发的镜像（`ghcr.io/felix2yu/*:latest`）不在本条约束内——那是发布策略
> 决定的语义，见共享 CI README 第三节。

---

## 四、时区

只要业务代码里有「今天的日期」概念，镜像里就必须有 tzdata：

| 症状 | 原因 |
|---|---|
| 「今天」的时间窗口从 08:00 开始算 | 容器按 UTC 计算自然日，东八区的 08:00 才是 UTC 00:00 |
| 定时任务晚了 8 小时 | 同上 |
| SQLite 里 `date('now')` 与 Go 的 `time.Now()` 差一天 | 前者 UTC，后者本机时区 |

```dockerfile
RUN apk add --no-cache tzdata ca-certificates su-exec
ENV TZ=Asia/Shanghai
```

Alpine 不装 `tzdata` 时，Go 的 `time.LoadLocation("Asia/Shanghai")` 会直接报错
`unknown time zone`，且只在容器里报——本机永远复现不了。

---

## 五、健康检查

```dockerfile
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O- http://127.0.0.1:8787/api/health >/dev/null || exit 1
```

两条硬约束：

1. **探活端点必须免鉴权**。拿需要登录的 `/api/tasks` 探活，配了访问口令之后容器会
   稳定 unhealthy，而服务其实是好的。
2. **`start-period` 不能省**。首次启动要建表、跑迁移，5 秒内起不来是常态；没有
   start-period 的话这几次失败会被计入 retries，容器还没热身就判死。

**distroless 例外**：没有 shell，`HEALTHCHECK` 的 CMD 无处执行。这类镜像改由
编排层探活——在 `docker-compose.yml` 里写 `healthcheck:`，口径一样
（免鉴权端点 + start_period）。二者有一处即可，脚本二选一认可。

---

## 六、自检清单

脚本覆盖前 6 条：

- [x] 装配式：`COPY` 的产物路径没有被 `.dockerignore` 排除
- [x] `FROM` 全部 pin 到具体 tag 或 digest，无裸 `:latest`
- [x] 非 root 运行：`USER` / `su-exec` / distroless `nonroot` 三选一（或已登记豁免）
- [x] 有探活手段：`HEALTHCHECK` 或 compose `healthcheck:`
- [x] 涉及本地日期的镜像装了 tzdata 并设了 `TZ`
- [x] `ENTRYPOINT` / `CMD` 用 exec form（数组写法），而非 shell form

以下 4 条人工：

- [ ] 降权后仍能写数据目录（`docker compose up` 一次，看有没有 `permission denied`）
- [ ] 宿主挂载目录属主与 PUID/PGID 一致
- [ ] `docker stop` 能优雅退出（降权链路上的 `exec` 没漏）
- [ ] 升级基镜像后跑一次 `check-docker.py`：` alpine:3.24 → 3.25` 这种滚动会让 glibc
      下限悄悄变高（CGO 项目尤其敏感，见共享 CI README 第四节）

```bash
python3 scripts/check-docker.py              # 全部仓库
python3 scripts/check-docker.py shenshi ntfy # 只看指定仓库
```
