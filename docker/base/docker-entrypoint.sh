#!/bin/sh
# 公共运行时基镜的降权入口脚本（alpine / debian 通用）。
#
# 契约见 Felix2yu/.github/docs/docker.md 第二节（非 root 运行）：
#   PUID / PGID   运行身份，默认 1000:1000（macOS/Linux 首个普通用户）
#   APP_DATA_DIR  数据目录，默认 /data；启动时把它 chown 给运行身份
#
# 为什么下沉到基镜：这段逻辑原先在 8 个仓库各写一份，已经分叉成不同的默认值
# （shenshi 1000:100、diarum 写死不降权、liuxia 用 gosu）、不同的 chown 目标
# （/data vs /app/data）、以及对非 root 启动的不同处理。基镜只保留这一份后，
# 应用 Dockerfile 不再各有 entrypoint，只提供 CMD。
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
# 支持多个数据目录（空格分隔）：有的服务把数据库与上传分开挂
# （如 qingye 的 /app/data + /app/uploads），都要 chown 到运行身份。
APP_DATA_DIR="${APP_DATA_DIR:-/data}"

# 已是非 root 启动（docker run --user / k8s runAsUser+runAsGroup）：无力改属主，
# 直接以当前身份运行，避免 chown 失败把容器卡死在启动阶段。
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

# 数据目录归属到运行身份。历史遗留的 root 属主文件会继续挡住 SQLite 写入
# （WAL 需要对目录本身可写），所以用 -R 覆盖整个目录树，而不是只改顶层。
for dir in $APP_DATA_DIR; do
    [ -d "$dir" ] || continue
    chown -R "${PUID}:${PGID}" "$dir" 2>/dev/null || true
done

# 降权后 HOME 仍指向 /root（不可写），把依赖 $HOME 的写入引到第一个数据目录。
export HOME="${APP_DATA_DIR%% *}"

# 用数字 uid/gid 降权：真正决定文件权限的是数字，与容器内是否存在同名用户无关。
# 必须 exec 替换 PID 1 —— 否则信号收不到，docker stop 会等 10 秒超时后 SIGKILL，
# SQLite 的 WAL 就可能来不及 checkpoint。
if command -v su-exec >/dev/null 2>&1; then
    exec su-exec "${PUID}:${PGID}" "$@"
else
    exec gosu "${PUID}:${PGID}" "$@"
fi
