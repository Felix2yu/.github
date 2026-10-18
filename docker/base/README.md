# 公共运行时基镜

`Felix2yu/*` 的各服务镜像统一 `FROM` 这里构建的基镜，不再各自 `apk add` / `apt install`。
契约见 [docs/docker.md](../../docs/docker.md) 第二节，发布流程见
[`.github/workflows/base-image.yml`](../../.github/workflows/base-image.yml)。

## 文件

| 文件 | 作用 |
|---|---|
| `Dockerfile.alpine` | alpine 版基镜 → `ghcr.io/felix2yu/base:alpine-3.24` |
| `Dockerfile.debian` | debian slim 版基镜 → `ghcr.io/felix2yu/base:debian-13`（glibc / CGO） |
| `docker-entrypoint.sh` | 通用降权入口（两版共用，运行时探测 su-exec / gosu） |

## 基镜内容

- `ca-certificates`：TLS 校验（Go 读系统根证书）
- `tzdata` + `ENV TZ=Asia/Shanghai`：容器内本机时区
- `su-exec`（alpine）/ `gosu`（debian）：降权工具
- `app` 用户（uid/gid 1000）
- `docker-entrypoint.sh`：`ENTRYPOINT`，按 `PUID/PGID` 降权

## 应用怎么用

```dockerfile
FROM ghcr.io/felix2yu/base:alpine-3.24      # glibc/CGO 用 :debian-13

ENV APP_DATA_DIR=/data                       # 要 chown 的数据目录，多个用空格分隔
COPY --chmod=755 bin/app /usr/local/bin/app
CMD ["app", "-addr", ":8080"]                # ENTRYPOINT 由基镜提供
```

环境变量契约：

| 变量 | 默认 | 作用 |
|---|---|---|
| `PUID` / `PGID` | `1000` / `1000` | 运行身份 |
| `APP_DATA_DIR` | `/data` | 启动时 `chown -R` 的目标，空格分隔可给多个 |

## 为什么抽基镜

`apk` / `apt` 层在**独立构建**下不可复现：安装时刻会写进 `/lib/apk/db/installed`
与 `/var/log/dpkg.log`，同一份 Dockerfile 两次 `--no-cache` 构建出的层 digest 都不同。
所以「层共享」不能靠指令一致，只能靠 `FROM` 同一个基镜 digest。基镜在这里构建一次、
发布一次，各应用引用它 —— 公共层（ca-certificates / tzdata / 降权入口）全网只存一份。

## 本地构建

```bash
cd /path/to/.github
docker build -f docker/base/Dockerfile.alpine -t ghcr.io/felix2yu/base:alpine-3.24 docker/base
docker build -f docker/base/Dockerfile.debian -t ghcr.io/felix2yu/base:debian-13 docker/base

# 行为自检
docker run --rm ghcr.io/felix2yu/base:alpine-3.24 id                     # uid=1000(app)
docker run --rm -e PUID=1500 ghcr.io/felix2yu/base:alpine-3.24 id        # uid=1500
docker run --rm --user 2000:2000 ghcr.io/felix2yu/base:alpine-3.24 id    # uid=2000（短路）
```

## 触发重建

基镜推上之后，`base-image.yml` 的 `notify` job 会向矩阵里的每个应用仓库发
`repository_dispatch(base-image-updated)`。需要仓库 secret `DISPATCH_TOKEN`
（对目标仓库有 `Actions: write`）。应用侧 `ci.yml` 里要有：

```yaml
on:
  repository_dispatch:
    types: [base-image-updated]
```
