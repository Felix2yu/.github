# SQLite 备份契约（应用自导出）

`docker-db-auto-backup` 统管全舰队的数据库备份，pg / mysql / redis 各有标准
dump 通道，唯独 SQLite 是**应用内嵌的文件型数据库**：没有服务进程可连、容器里
通常也没有 `sqlite3` CLI。本契约约定应用与备份工具之间怎么协作——应用提供
`backup` 子命令输出一致快照，工具复用既有落盘、校验、清单、Kopia、通知管道。

为什么是「应用自导出」而不是拷文件：直接复制正在被写入的 `.db`/`-wal`/`-shm`
不是原子操作，实测活跃写入下会周期性产出损坏副本（db+wal+shm 也只有 14/15 通过）；
应用进程内用 `VACUUM INTO` / backup API 是 SQLite 保证的事务级一致视图。
实测数据与被排除的方案见
[`docker-db-auto-backup/docs/sqlite-backup-design.md`](../../docker-db-auto-backup/docs/sqlite-backup-design.md)——
**本文只立契约，验证记录归工具仓**，两边不重复维护。

---

## 一、应用侧约定（`backup` 子命令）

每个内嵌 SQLite 的自研应用**必须**提供 `backup` 子命令，五条要求：

1. **在 `main()` 最前面拦截**，早于任何存储初始化/迁移——导出进程不该改 schema、
   不该建目录、不该产空库；
2. **只读打开数据库**；数据文件不存在时**报错退出**，不静默创建；
3. **`VACUUM INTO` 到临时目录，再把文件流式写到 stdout**（或先打包再写 stdout）；
4. **日志一律走 stderr**，stdout 只承载备份数据；
5. **命令能被容器内的 `sh -c` 解析**——见第二节的绝对路径要求。

各应用可在此基础上扩展：输出「数据 + 附件」的 zip、支持 `--format` 开关、
多库打进一个包。但 stdout 语义（只有数据）与退出码语义（失败非 0）不变。

> 为什么拦截要早：`docker exec` 起的是独立进程，若它先跑了迁移再导出，
> 一次备份就可能改变库结构；以 root 身份跑出来的迁移还会留下属主错误的文件
> （见第四节）。

## 二、识别与命令声明

`sqlite` provider 没有镜像名 patterns（SQLite 没有官方镜像，镜像匹配没有可靠依据），
识别只有两条路：

| 方式 | 声明 | 说明 |
|------|------|------|
| 容器标签 | `backup.provider=sqlite` | 既有标签机制；同时解锁镜像名推导 |
| 环境变量 | `BACKUP_SQLITE_CMD` | 设置即视为声明，**本舰队唯一可靠入口** |

命令解析优先级：`BACKUP_SQLITE_CMD` > 镜像名推导（`ghcr.io/owner/app` → `app backup`，
仅在打了标签时可达）。

**绝对路径是硬性要求，不是保守**。容器布局标准（见
[`docker.md`](docker.md) 第二节）把二进制放在 `/app/<app>`，`/app` 不在 `PATH` 上，
裸名 `sh -c '<app> backup'` 实测全舰队十一个容器一律 exit 127。所以本舰队的
声明一律写**裸值**：

```yaml
environment:
  - BACKUP_SQLITE_CMD=/app/<app> backup     # 列表式裸值
  # 或映射式（引号是 YAML 语法，剥掉后值仍是裸的）
  BACKUP_SQLITE_CMD: "/app/<app> backup"
```

**不要写成 `- BACKUP_SQLITE_CMD="/app/<app> backup"`**：列表式里引号不是 YAML
语法而是值的一部分，存进环境变量的值会带着引号，`sh -c` 把 `"/app/<app> backup"`
整串（含空格）当单个命令名，报 command not found。整条 KEY=VALUE 加外层引号
（`- "BACKUP_SQLITE_CMD=/app/<app> backup"`）是安全的——YAML 剥掉的是外层包裹。
工具读取时会剥掉最外层成对引号兜底（见第三节），但判据仍按裸值口径检查。

镜像名推导在 `/app` 布局下不成立；二进制名与镜像名不同的（huozhi 的
`huozhi-server` vs `huozhi`）即使上了 PATH 也会猜错。**结论：每个接入容器都
显式声明绝对路径命令，把推导当兜底而不是依赖。**

未声明导出命令、但环境变量值里出现 `.db`/`.sqlite`/`.sqlite3` 后缀或
`sqlite://` DSN 的容器，会被计入「疑似 SQLite 但未能识别」告警——宁可告警，
不对陌生镜像执行猜出来的命令，也不静默漏备份。

## 三、变量契约

| 变量 | 默认 | 语义 |
|------|------|------|
| `BACKUP_SQLITE_CMD` | 镜像名推导 | 自导出命令（空格分隔，经容器内 `sh -c` 执行）；设置即视为声明 sqlite 容器。**值裸写**——引号包裹的值曾被整串当命令名（exit 127），现在工具读取时剥掉最外层成对引号兜底，但别依赖兜底 |
| `BACKUP_SQLITE_EXT` | `db` | 产物扩展名；应用输出「数据 + 附件」的 zip 时设为 `zip`。同样接受引号包裹并剥壳 |
| `BACKUP_SQLITE_USER` | 由 `PUID`/`PGID` 推导 | exec 导出命令的身份（`1000:1000` 形式）；两者都缺失时沿用容器默认用户。同样剥壳；`PUID`/`PGID` 也一样 |

三个变量与 `PUID`/`PGID` 的读取口径统一：**去空白 + 剥最外层成对引号**（`"…"`、`'…'`）。
引号不成对则原样保留——那不是包裹，是值的内容。

**扩展名必须与产物一致**。工具按扩展名选校验器：`db` 查 `SQLite format 3\0`
魔数，`zip` 查 `PK\x03\x04` 魔数。应用输出 zip 却按默认 `db` 校验——校验失败、
产物丢弃、本轮标记 failed。这是有意为之：**不允许把 zip 当 SQLite 存下来**，
坏备份比没备份更危险。`BACKUP_SQLITE_EXT` 的值限字母数字、≤16 位。

**一个容器一个文件**：产物 `{BACKUP_DIR}/{日期}/{容器名}.{ext}`。多库容器由应用
把多库打包后输出（如 bili-history 六个库打一个 zip），每库各自事务一致，
跨库不承诺同一时间点。

## 四、exec 身份

`docker exec` 不经过 ENTRYPOINT，默认以容器配置的用户运行（通常是 **root**）。
pg / mysql 的 dump 只读网络，用默认用户无所谓；SQLite 的导出进程会碰数据目录：

> 应用停止时一个 root 身份的连接会创建属主为 root 的 `-shm`/`-wal`，
> 之后降权运行的应用反而打不开自己的库。

因此 `BACKUP_SQLITE_USER` 的派生优先级是：显式设置 > `PUID`/`PGID`
（同时存在且为纯数字时拼成 `uid:gid`）> 容器默认用户。基镜烘入了
`ENV PUID=1000 PGID=1000`，所以零配置也能与数据属主一致；显式声明是把口径钉死，
改 `PUID/PGID` 时要同步改。

## 五、恢复语义

- `db` 产物：放回数据目录、修属主、重启应用。`VACUUM INTO` 产物是 delete 日志
  模式，应用启动会按自身逻辑切回 WAL——**不要手动转 WAL**。
- `zip` 产物：用应用自己的导入功能恢复（Web UI 上传导入）。
- **恢复演练不支持 sqlite**：演练容器用源镜像，而源镜像没有 `sqlite3` CLI；
  工具会打印「恢复演练暂不支持该类型，已跳过」并在 debug 级留痕。恢复要靠
  定期手工验证，这是本契约唯一的已知空档。

## 六、判据

`scripts/check-docker.py` 按本契约检查各应用仓的 compose 声明：

| 判据 | 说明 |
|------|------|
| 命令绝对路径 | `BACKUP_SQLITE_CMD` 以 `/` 开头；裸名在 `/app` 布局下必然 127 |
| 命令以 `backup` 结尾（或含 `backup` 子命令） | 防手滑写成 `dump` / `export` 等工具读不出的形态 |
| 值不带引号 | compose 列表式 `KEY="value"` 的引号会存进变量值（旧版工具整串当命令名 127）；改裸值或映射式 |
| `BACKUP_SQLITE_EXT` 合法 | 只允许字母数字、≤16 位；`zip` 与 `db` 之外的值提示人工确认 |
| 标签与命令的一致性 | 打了 `backup.provider=sqlite` 标签却无 `BACKUP_SQLITE_CMD` 的容器在 `/app` 布局下备份必然失败，报出来 |

工具侧的运行时防线（疑似告警、魔数校验、扩展名不一致丢弃）在
`docker-db-auto-backup` 仓内，由 `sqlite_test.go` 覆盖；本仓判据只管
**声明写没写对**，不去猜应用的行为。
