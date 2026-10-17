# 代码风格与静态检查基线

目录结构与构建约定见 [`project-structure.md`](project-structure.md)、
[`build-script.md`](build-script.md)。这一份管的是**编辑器行为和静态检查**——
仓库里那三个"谁都该有、但谁都没写"的文件。

## 一、三份文件与它们的来源

模板放在本仓 [`templates/`](../templates)，各仓**内容逐字节相同**，改模板后用
[`scripts/sync-format-baseline.py`](../scripts/sync-format-baseline.py) 一键分发：

| 模板 | 落地文件 | 作用范围 |
|---|---|---|
| `templates/editorconfig` | `.editorconfig` | 全部仓库（含非 Go） |
| `templates/gitattributes` | `.gitattributes` | 全部仓库 |
| `templates/golangci.yml` | `.golangci.yml` | 有 `go.mod` 的仓库 |

不许逐仓手改这三份文件——下次同步会被覆盖。要改就先改模板。

## 二、`.editorconfig`

```
[*.go]           indent_style = tab     # gofmt 只认 tab
[{Makefile,*.mk}] indent_style = tab    # recipe 必须是 tab
[*.{md,markdown}] trim_trailing_whitespace = false   # 行尾两空格=硬换行
[*]              charset=utf-8 / eol=lf / insert_final_newline / trim_trailing_whitespace
```

其余语言（TS / Svelte / CSS / JSON / YAML）统一 2 空格。

## 三、`.gitattributes`

```
* text=auto eol=lf
```

**为什么强制 LF**：开发机是 macOS、CI 与容器是 Linux。CRLF 不进来时一切正常，
一旦进来就是两类幽灵故障——`scripts/*.sh` 报 `bad interpreter: /bin/bash^M`，
以及 `go:embed` 的内容哈希（如 liuxia 的 `SWVersion`）在不同机器上算出不同值。

配套的二进制标记（`*.png/gif/ico/woff2/db` 等）让 `git diff` 不再把图片当文本刷屏。
`**/dist/**` 与 `**/node_modules/**` 的 linguist 标注只是让 GitHub 的语言统计不被产物带偏。

## 四、`.golangci.yml` 基线

版本是 **golangci-lint v2 格式**（首行 `version: "2"`）。v1 的
`linters.enable` 直接写在顶层，v2 要包在 `linters:` 下，照抄 v1 配置会静默不生效。

启用的是六项低噪音检查，`default: none` 而非官方默认集合：

| linter | 抓什么 |
|---|---|
| `errcheck` | 未处理的 error 返回值 |
| `govet` | 官方 vet（printf、lock copy、unreachable…） |
| `ineffassign` | 赋值后从未读取 |
| `staticcheck` | 静态分析主力（含 gosimple/stylecheck 合并后的规则） |
| `unused` | 未使用的函数/变量/类型 |
| `misspell` | 代码注释里的拼写 |

`errcheck` 关掉 `check-type-assertions` 与 `check-blank`——`v, _ := m[k]` 这种
有意忽略很常见，开着只会制造噪音。

本地跑：

```bash
golangci-lint run                       # 用仓里的 .golangci.yml
golangci-lint run --fix                 # 自动修可修项
golangci-lint config verify             # 改配置后先验语法
```

### 为什么现在不接进 CI

存量代码从来没 lint 过，直接把 `golangci-lint run` 设成阻断门禁，21 个仓会同时变红，
PR 无法推进。当前定位是**本地可跑、逐步收敛**：先让配置就位，等各仓 `run` 归零后
再在共享 workflow 里加一个 informational 的 lint job（与 Codecov 同样的处理思路）。

## 五、验证

```bash
python3 scripts/check-repo-hygiene.py     # 三份文件是否齐全
python3 scripts/sync-format-baseline.py --check   # 内容是否与模板一致
```

`--check` 只比对不写；修偏差用不带参数的 `sync-format-baseline.py`。

## 六、例外

- **fork 仓**（GoldPrice / healthchecks / ntfyx）不套用：改这些文件会在 merge 上游时变成冲突。
- **非 Go 仓**（RSSRob 是 Python、WebMonitor 是纯前端）不写 `.golangci.yml`。
