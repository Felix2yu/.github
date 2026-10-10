# CPU 指令集基线

产物能不能在别人的服务器上跑，取决于它用了哪些 CPU 指令。这件事**编译不会报错、
测试不会发现**，只在别人的机器上炸——本文就是那次事故的复盘与防线。

自检脚本：`scripts/check-cpu-baseline.py`

---

## 一、事故：ntfy 发布了带 AVX 的二进制

ntfy 的 release 产物在部分服务器上启动即崩溃（exit 132 = SIGILL），且崩溃发生在
任何日志输出之前。构建与测试全程绿灯。

根因是**构建环境**，不是代码：

- GitHub 的 `ubuntu-26.04` runner 的 apt 源与系统 glibc 是 **amd64v3**（即
  x86-64-v3 / AVX2）变体，不是最保守的 x86-64 基线。
- ntfy 需要 CGO（`mattn/go-sqlite3`），且要完全静态链接才能扔进 alpine 镜像。
- 静态链接把带 AVX2 的 `crt1.o` / `libc.a` **直接烧进二进制**。之后无论换什么
  CPU，产物里都带着这些指令——**这是构建期固化的，运行时无法退化**。

这与「glibc 版本太高导致 `GLIBC_2.43 not found`」是同一类问题的两面：都是把
「构建机的环境属性」泄漏进了产物。区别是 glibc 那类会在启动时报错信息，而这个
直接 SIGILL。

## 二、两种 AVX，风险完全不同

扫产物时 `objdump -d` 会看到大量 `ymm`/`zmm` 指令，但**看到不等于有风险**：

| 类型 | 判定依据 | 无 AVX 的 CPU 上 |
|---|---|---|
| **门控型** | 进函数前先查 CPU 特性（Go 的 `internal/cpu.X86.HasAVX2`、SVT-AV1 的 `svt_aom_get_cpu_flags`、dav1d 的 `dav1d_get_cpu_flags_x86`） | 安全，运行时绕开 |
| **无门控型** | 直接进入 AVX2 代码，来源是编译工具链选错或第三方预编译库 | **SIGILL** |

Go 标准库自带大量 AVX2/AVX-512 汇编（`runtime.memmove`、
`crypto/internal/fips140/sha256.blockAVX2`、`internal/runtime/gc/scan.*AVX512` 等），
**全部是门控型**。证据在 `$(go env GOROOT)/src/runtime/memclr_amd64.s`：

```asm
#ifndef hasAVX2
    CMPB    internal/cpu·X86+const_offsetX86HasAVX2(SB), $1
```

所以纯 Go 产物里 ymm 计数是个**固定基线值**（实测 3523~4043 / zmm 496），
不代表有问题。本仓群的实测数据：

| 仓库 | cgo | ymm | zmm | 来源 |
|---|---|---|---|---|
| bili-dl | off | 3523 | 496 | Go 标准库（门控） |
| diarum | off | 3574 | 496 | Go 标准库（门控） |
| qiansi / shenshi / liuxia / yuexi / chaxin | off | 4043 | 496 | Go 标准库（门控） |
| qbhive / qingye | off | 6737 | 496 | Go 标准库 + 依赖 SIMD（门控） |
| tuqie | off | 6965 | 823 | 另加 gen2brain h265/jxl 的 AVX-512 汇编（`if hasAVX512` 门控） |
| **mujian** | glibc | **228561** | **85771** | **avif-go 预编译静态库** |

mujian 的量级比纯 Go 基线高 56 倍，来源在依赖而非本仓工具链：
`github.com/vegidio/avif-go` 链入预编译的 `libsvtav1.a` / `libdav1d.a` /
`libjpeg.a` / `libturbojpeg.a` / `libyuv.a`。其中 svtav1 单独就有 17 万条 ymm
（`Source/Lib/ASM_AVX2/*.asm`）。

判定它是门控型的依据（不是猜的）：该库有 **4580 对** 同名函数成对存在
（`xxx_avx2` 与 `xxx_sse4_1`），加 `svt_aom_get_cpu_flags` / `dav1d_get_cpu_flags_x86`
分发入口；库内还能找到 `HAVE_AVX2` 宏与 `xgetbv` 指令（AVX 的 OS 状态检测必需）。

**但这仍是个需要持续盯着的依赖**：它把自己的编译参数固化成了预编译产物，
上游一旦改成只留 AVX2 版本，本仓不会收到任何通知，产物会在老 CPU 上静默退化。

## 三、契约

### 3.1 CGO_ENABLED=0（10 个仓库）

天然免疫——不链 libc，宿主机的 glibc 指令集进不来。这条路径只受 Go 自身
`GOAMD64` 控制（默认 v1，CI 未改动，实测 `GOAMD64=v1` 与默认产物完全一致：
ymm=4043 / zmm=496）。

约束：**产物不得出现 INTERP 段**。`CGO_ENABLED=0` 挡不住依赖里 `//go:linkname`
拽进 libc 符号（tuqie 的 avif 就曾因此把 glibc 的 INTERP 带进 musl 镜像）。
`reusable-image.yml` 已有守卫。

### 3.2 CGO 路径（ntfy static、mujian glibc）

**必须在固定基线容器里编译，不能在裸 runner 上编。** 容器用
`golang:1.26-trixie` / `golang:1.27-bookworm`——Debian 基线 x86-64 工具链，
`gcc -march=x86-64`，不引入 AVX；AVX 只存在于 IFUNC 多版本
`memcpy`/`strlen`，运行时 CPUID 探测后才进入。

已固化在 `reusable-release.yml` / `reusable-image.yml` 的 CGO 分支里。改
`golang:` 镜像标签时注意：换成 `ubuntu:` 系或带 `-v3` 的变体会把这个修复悄悄撤销。

### 3.3 引入原生 SIMD 库时

新增依赖若自带预编译静态库或汇编，须在本文件登记实测基线并确认门控。判定方法：

```sh
# 1. 库里有几个 AVX2 实现
objdump -t libxxx.a | grep -oE "[a-zA-Z_][a-zA-Z0-9_]*" | grep -c '_avx2$'
# 2. 有没有 SSE 对照版（有 = dispatch 可选 = 安全）
objdump -t libxxx.a | grep -c '_sse4_1$'
# 3. 有没有 CPUID 分发入口
objdump -t libxxx.a | grep -iE 'cpu_flags|dispatch'
```

三项都对得上才能认为门控完备。**只有第 1 项则无门控**，在老 CPU 上必崩。

## 四、自检

```sh
python3 scripts/check-cpu-baseline.py               # 配置面 + 基线对照
python3 scripts/check-cpu-baseline.py --build       # 现场交叉编译再扫
python3 scripts/check-cpu-baseline.py --bin-dir dist # 扫已有产物
python3 scripts/check-cpu-baseline.py mujian        # 单仓
```

判定规则是**「是否超出已登记基线」**，而不是「ymm 是否非 0」——后者会把 10 个
完全正常的仓库全部报成红灯，久而久之就没人看这个脚本了。

超出基线意味着指令集来源变了（多半是新依赖），必须人工确认新增部分是门控的。

依赖 `objdump`（binutils）。macOS 自带的 `/usr/bin/objdump` 可用；
无 binutils 时产物面检查会跳过并提示，不中断配置面检查。

## 五、新仓库接入

1. 纯 Go（`CGO_ENABLED=0`）：无需额外配置，脚本会算出基线并登记。
2. 需要 CGO：`reusable-*.yml` 传 `cgo: static`（alpine 镜像）或 `cgo: glibc`，
   **不要自己写 `docker run golang:...`**，会绕过固定基线容器的约束。
3. 引入原生 SIMD 依赖：按 §3.3 验证门控，登记基线。