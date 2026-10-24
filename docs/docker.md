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
go build -o bin/app   →   COPY bin/app /app/app          →  只有二进制
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

**`.dockerignore` 不许是 `.gitignore` 的符号链接**。`docker-db-auto-backup` 曾用
`.dockerignore -> ./.gitignore` 让两边内容自动同步，看上去省事，但两者的语义恰好相反：

| | 该排除什么 |
|---|---|
| `.gitignore` | `bin/`、`dist/` —— 构建产物不入库 |
| `.dockerignore` | **不能**排除 `bin/`、`dist/` —— 那是 `COPY` 源 |

给 `.gitignore` 补了一条 `/bin/`，镜像构建立刻报
`failed to calculate checksum of ref ...: "/bin/db-auto-backup": not found`。
链接删掉、改成独立文件即可。两个文件的读者不同（git vs docker daemon），
**内容天然不该一致**，链接只是把两份清单压成一份的偷懒。

---

## 二、非 root 运行

容器里的 root 与宿主的 root 是同一个 uid 0。进程一旦逃逸（哪怕只是一个能写任意路径的
漏洞），拿到的就是宿主 root。自托管服务长期暴露在公网，这条不是理论风险。

### 三种降权方案，按场景选

| 方案 | 适用 | 做法 | 已有仓库 |
|---|---|---|---|
| A · distroless nonroot | 无 shell 需求、不写数据卷 | `FROM gcr.io/distroless/static:nonroot`（uid 65532） | 暂无 |
| B · 静态 `USER` | 镜像内有固定数据目录，宿主无需对齐 | Dockerfile 里 `RUN adduser` + `USER <name>` | RSSRob / WebMonitor（Python，基镜是 python 官方镜像） |
| C · 公共基镜 + PUID/PGID | **所有自研 Go 服务** | `FROM ghcr.io/felix2yu/base:<variant>`；降权入口随基镜提供，应用只给 `CMD` | 15 个 alpine 服务 + mujian（走 debian 版） |

**判定标准只有一条**：容器是否需要写宿主的绑定挂载目录。需要 → 方案 C；不需要 → A 或 B。

### 方案 C：公共基镜 + 降权入口（默认形态）

`PUID/PGID` 解析、数据目录 `chown`、`exec su-exec` 这些**全部下沉到公共基镜**
`ghcr.io/felix2yu/base`，各应用只提供 `CMD`：

```dockerfile
FROM ghcr.io/felix2yu/base:alpine-3.24      # glibc/CGO 用 :debian-13

WORKDIR /app
ENV APP_DATA_DIR=/app/data                  # 要 chown 的数据目录，多个用空格分隔
COPY --chmod=755 bin/app /app/app
CMD ["/app/app", "-addr", ":8080"]          # ENTRYPOINT 由基镜提供（降权后 exec）
```

**为什么二进制与数据都收在 `/app` 下**：容器里「属于这个应用的东西」集中在一处——
`/app/app` 是可执行文件、`/app/data` 是挂载卷、静态产物是 `/app/dist`，看 compose 的
挂载点和看镜像内容都对得上，不用在 `/data` 与 `/app/data` 之间逐仓库回忆。代价要写明白：
`/app` 不在 `PATH` 上，所以 `CMD` 和 `docker exec` 都必须写绝对路径，任何依赖裸名解析的
机制（例如 docker-db-auto-backup 按镜像名推导 `<bin> backup`）在这种布局下不成立，容器要
显式声明绝对命令（`BACKUP_SQLITE_CMD=/app/app backup`）。备份声明的完整契约见
[`backup.md`](backup.md)。

**为什么不让各仓库各写一份 entrypoint**：这 8 份脚本已经分叉出不同的默认值
（`1000:100` vs `1000:1000`）、不同的 chown 目标（`/data` vs `/app/data`）、
以及对非 root 启动的不同处理；`apk`/`apt` 层在独立构建下还不可复现
（安装时刻写进了 `/lib/apk/db/installed` 与 `/var/log/dpkg.log`），各自 `apk add`
永远各存一份 —— 只有 `FROM` 同一个基镜 digest，那些公共层才真正共享。

基镜的环境契约（应用可覆盖）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `PUID` / `PGID` | `1000` / `1000` | 运行身份；数字 uid/gid 直接用于 chown 与降权 |
| `APP_DATA_DIR` | `/data` | 启动时 `chown -R` 给运行身份的数据目录，空格分隔可给多个。应用镜像按上面的模板显式覆盖为 `/app/data`；基镜默认保持 `/data`，是为了不牵动仍在用旧路径挂载的容器 |

四个细节由基镜统一保证：`chown -R` 覆盖整棵数据目录树、非 root 启动（`--user`）时
短路直接运行、`exec` 替换 PID 1（信号可达，`docker stop` 不超时）、默认 `1000:1000`。

> 各仓库不再有 `scripts/docker-entrypoint.sh`。`huozhi` 是唯一例外：它要生成并持久化
> JWT secret，保留了自己的 entrypoint，但在最后 `exec /usr/local/bin/docker-entrypoint.sh …`
> 调用基镜的降权入口。

### 为什么需要 PUID/PGID（方案 C 的全部理由）

容器内 uid 与宿主 uid 是同一套数字。宿主机上 `./data` 属于 uid 1000，
容器里跑 uid 100 的 `shenshi`，就对这个目录**没有任何写权限**——SQLite 以 WAL 模式
打开时需要对目录本身可写，症状是：

```
error 14: unable to open database file
```

而 `chmod 777 data` 只是把问题推迟到下一次备份恢复之后重新出现。正确做法是把容器内
运行身份的 uid/gid 对齐到宿主目录属主。这段逻辑**只存在基镜里一份**
（`.github/docker/base/docker-entrypoint.sh`）：

```sh
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
for dir in $APP_DATA_DIR; do            # 默认 /data，空格分隔可给多个目录
    chown -R "${PUID}:${PGID}" "$dir" 2>/dev/null || true
done
exec su-exec "${PUID}:${PGID}" "$@"      # debian 版基镜用 gosu
```

四个细节都不能省：

- **`chown -R` 必须覆盖整个数据目录**：历史遗留的 root 属主文件（比如某次用 root
  跑过）会残留下来继续挡住写入。
- **`exec`**：降权后的进程要替换 PID 1，否则信号收不到，`docker stop` 会等到 10 秒
  超时后 SIGKILL——SQLite 的 WAL 就可能来不及 checkpoint。
- **默认值要有**：不传 PUID/PGID 也得能跑起来，默认 `1000:1000`（macOS/Linux 首个
  普通用户通常就是这个）。
- **非 root 启动要短路**：`docker run --user` 或 k8s `runAsUser` 时已经没有 root 权限，
  直接 `exec "$@"`，别让 chown 失败把容器卡在启动阶段。

> 统一默认 `1000:1000` 后，「慎始」原先为 macOS `staff` 组特设的 `PGID=100` 已取消；
> 确有需要时在 compose 里显式设 `PGID=100` 即可。

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

**公共基镜是唯一不 pin digest 的 `FROM`**：

```dockerfile
FROM ghcr.io/felix2yu/base:alpine-3.24   # 应用层这样写
```

写 tag、不写 digest 是**刻意**的：基镜（`.github/docker/base/`）一变，`base-image.yml`
就向各应用发 `repository_dispatch` 触发重建，build 的 `pull: true` 会重新解析 tag 到
最新 digest。这样各应用不必手改 digest，也不会永久停在某个旧基镜上。（顺带一提：
`apk`/`apt` 层在独立构建下不可复现，「层共享」恰恰不能靠指令一致，只能靠 `FROM`
同一个基镜 digest —— 这正是抽公共基镜的动机。）

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

公共基镜 `ghcr.io/felix2yu/base:*` 已内置 `tzdata` 与 `ENV TZ=Asia/Shanghai`，
应用无需再 `apk add`；确需别的时区时覆盖 `TZ` 即可。

Alpine 不装 `tzdata` 时，Go 的 `time.LoadLocation("Asia/Shanghai")` 会直接报错
`unknown time zone`，且只在容器里报——本机永远复现不了。基镜消除的正是这类
「每个仓库都得记得装一次」的隐性前提。

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

**distroless 的真相**：`gcr.io/distroless/static` 里既没有 shell 也没有 `wget`/`curl`，
`HEALTHCHECK` 的 CMD 无处执行 —— **改到 compose 里写 `healthcheck:` 同样执行不了**，
因为编排层也必须在容器内跑命令。distroless 镜像想探活只有两条路：

1. 镜像内塞一个健康检查二进制（多一层构建，极少值得）；
2. **登记例外**，靠 `restart: unless-stopped` 兜底，并保证应用自身有外部可观测的
   `/healthz`（端点留给外部监控用）。

非 distroless 的 alpine 镜像没这个问题，`wget` 自带，探活写在 Dockerfile 或 compose
都行。判断口径只有一条：**容器里有没有一个能发 HTTP 请求的可执行文件**。

**qiansi 的取舍**（2026-10-17）：它原本是唯一一个走方案 A 的仓库，但 distroless
带来两个代价——探活只能登记例外、镜像里没有任何调试手段（进不去、抓不到网络栈）。
改成公共基镜（alpine 版）后 `HEALTHCHECK` 打 `/api/v1/health` 就能用，`tzdata` 与
降权入口也随基镜一起来：数据卷绑定挂载 `./data` 时按 `PUID/PGID` chown 属主即可。


---

## 六、自检清单

脚本覆盖前 6 条：

- [x] 装配式：`COPY` 的产物路径没有被 `.dockerignore` 排除
- [x] `FROM` 全部 pin 到具体 tag 或 digest，无裸 `:latest`
- [x] 非 root 运行：`FROM ghcr.io/felix2yu/base:*`（公共基镜）/ `USER` / distroless `nonroot` 三选一（或已登记豁免）
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
