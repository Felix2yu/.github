# PWA 实现规范（v1）

- **适用范围**：本工作区下全部前端可安装为 PWA 的项目（不含纯后端仓库）
- **配套预设**：[`../pwa-preset/`](../pwa-preset)（图标生成器、head 片段、manifest 模板、SW 模板）
- **自检脚本**：`scripts/check-pwa.py`（在本仓库根目录跑，见第六节）
- **制定日期**：2026-10-10
- **来源**：由工作区 `PWA支持现状审计.md` 的最佳实践提炼而来

> 为什么不放在单个项目里：图标命名 / manifest 字段 / SW 行为这些契约是**跨仓库**的，
> 写进任何一个项目都只有那一个项目看得到。共享仓库是唯一「新接入时必然会先翻一遍」的位置——
> 接入 CI 时读 README，顺带就把 PWA 契约看到了。

本规范只约束**对外可观测的契约**（资产命名/尺寸、head 声明、manifest 字段、SW 行为），
不强制统一内部实现——已有的、经过验证的 SW 实现（如慎始的 Web Push、图切的构建产物同步）
予以保留，但必须满足第四节的行为契约。

---

## 一、图标资产契约

统一文件名与尺寸，任何仓库的图标目录都应能"一眼对上"：

| 文件 | 尺寸 | 必需 | manifest purpose | 说明 |
| :--- | :--- | :-: | :--- | :--- |
| `favicon.ico` | 16 / 32 / 48 多尺寸 | ✔ | — | 浏览器标签、老浏览器、爬虫兜底 |
| `favicon.svg` | 矢量（`sizes:any`） | ✔ | `any` | 现代浏览器矢量图标 |
| `icon-192.png` | 192×192 | ✔ | `any` | Chrome/Android 安装最低要求 |
| `icon-512.png` | 512×512 | ✔ | `any` | 启动图 / 高清屏 |
| `icon-maskable-512.png` | 512×512 | ✔ | `maskable` | Android 自适应图标 |
| `icon-maskable-192.png` | 192×192 | 推荐 | `maskable` | 与上面成对 |
| `apple-touch-icon.png` | 180×180 | ✔ | — | iOS 主屏 |

**硬性要求**

1. `maskable` **必须是位图**（PNG），**不能**用 SVG，也不能与 `any` 复用同一份带圆角/透明的图。
2. maskable 图必须**满幅无透明**，且主体落在**中心 80% 直径的安全圆**内。
3. `apple-touch-icon` 必须是 **PNG**（iOS 不识别 SVG），推荐满幅方形，交给 iOS 自己切圆角。
4. manifest 中 `any` 与 `maskable` 必须**分成独立条目**，不要写 `"purpose": "any maskable"`。

**生成方式**：一律用 [`../pwa-preset/gen-icons.py`](../pwa-preset/gen-icons.py)，避免手搓尺寸不一致。
它会自动做「满幅填底 / 去背 / 安全圆校验」，比手工缩放可靠得多。

---

## 二、HTML `<head>` 契约

声明顺序与内容统一如下（片段见 [`../pwa-preset/head.html`](../pwa-preset/head.html)）：

```html
<!-- A 案：主题跟随系统外观（推荐多数内容型应用） -->
<meta name="theme-color" content="#浅色" media="(prefers-color-scheme: light)" />
<meta name="theme-color" content="#深色" media="(prefers-color-scheme: dark)" />

<!-- B 案：主题由应用内设置决定（应用内三档 auto/light/dark）时改用单条 + JS 覆盖 -->
<meta name="theme-color" content="#浅色" />

<link rel="manifest" href="/manifest.webmanifest" />

<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="default" />
<meta name="apple-mobile-web-app-title" content="应用名" />
<meta name="format-detection" content="telephone=no" />

<link rel="icon" href="/favicon.ico" sizes="16x16 32x32 48x48" />
<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
<link rel="icon" href="/icon-192.png" type="image/png" sizes="192x192" />
<link rel="apple-touch-icon" href="/apple-touch-icon.png" sizes="180x180" />
```

**要点**

- `theme-color` **必须有静态声明**；用 JS 按用户偏好二次覆盖是允许的增强，但不能是唯一来源（否则首屏与安装卡片拿不到颜色）。
- 跟系统走就用 A 案、应用内自己控制主题就用 B 案，**不要混用**。尤其不要写成「两条 media + 一条无 media 兜底」并把兜底放在末尾 —— 各家 UA 对「多个 theme-color 谁生效」的处理并不统一，无 media 的那条在任何一套语义下都可能反过来盖掉 media 分档，等于白写。
- A 案的两条按「浅色在前、深色在后」排列：这样无论 UA 取「首个匹配」还是「末个匹配」，浅/深两种外观下都取到正确的那条。
- viewport 建议带 `viewport-fit=cover`，以配合 iOS 刘海屏。
- 拥有 iOS meta 却没有 `manifest` link 是最典型的"假 PWA"，两者必须成对。

---

## 三、Web App Manifest 契约

字段集与顺序见 [`../pwa-preset/manifest.template.json`](../pwa-preset/manifest.template.json)，**最小完整集**如下：

| 字段 | 必需 | 说明 |
| :--- | :-: | :--- |
| `id` | ✔ | 应用唯一标识，默认 `"/"`；缺失会导致重复安装 |
| `name` / `short_name` | ✔ | 全称 / 主屏短名（≤12 个汉字） |
| `description` | ✔ | 用于安装卡片 |
| `lang` / `dir` | ✔ | `zh-CN` / `ltr` |
| `start_url` / `scope` | ✔ | 均默认 `"/"` |
| `display` | ✔ | `standalone` |
| `display_override` | 推荐 | `["standalone","minimal-ui"]` |
| `orientation` | 推荐 | 内容型可 `portrait-primary`，工具型 `any` |
| `background_color` / `theme_color` | ✔ | 启动背景色 / 主题色 |
| `categories` | 推荐 | 如 `["productivity"]` |
| `icons` | ✔ | 按第一节的资产契约，`any` 与 `maskable` 分离 |

**路径与 MIME**

- 规范路径：`/manifest.webmanifest`；由 `vite-plugin-pwa` 系插件自动生成。
- 静态直出时若保留 `/manifest.json`，可以接受，但**必须以 `application/manifest+json` 响应**。

---

## 四、Service Worker 行为契约

### 技术选型（"统一调用的库"）

| 项目形态 | 选用 | 说明 |
| :--- | :--- | :--- |
| 纯 Vite SPA（React / Svelte） | `vite-plugin-pwa` + `generateSW` | 常规缓存策略，零手写 |
| SvelteKit | `@vite-pwa/sveltekit` | 同上，适配 `client/**` 产物 |
| Nuxt 3 | `@vite-pwa/nuxt` | 同上 |
| 需要自定义 SW（Web Push / 复杂预热） | 上述插件 + `strategies: 'injectManifest'` | SW 内用 `workbox-precaching`/`workbox-routing`/`workbox-strategies` |
| Go / 静态直出（无 bundler） | [`../pwa-preset/sw-template.js`](../pwa-preset/sw-template.js) | 构建时替换 `__BUILD_VERSION__` |

> **约定**：新接入一律走 `vite-plugin-pwa` 家族，**不要新写裸手 SW 与手写注册逻辑**（注册用插件的 `virtual:pwa-register`）。

### 行为契约（无论哪种实现都必须满足）

1. **缓存名版本化**：缓存名绑定构建版本/内容哈希（如 `app-${BUILD}`）；禁止写死 `-v1`/`-v3` 后再不更新。
2. **`activate` 清旧**：按前缀批量删除非当前版本的缓存。
3. **预缓存 app shell**：至少缓存入口页与离线兜底页。
4. **导航请求**：network-first（数据型页面）或 stale-while-revalidate（静态型页面），失败回落缓存，再回落壳页。
5. **API 请求**：network-first，失败回落缓存；**GET 缓存必须设条目上限**（推荐 ≤300）与过期时间，避免无限膨胀。
6. **静态资源**：`/assets`、`/icons`、`/static` 等走 cache-first。
7. **离线兜底**：必须有明确的离线页或壳页回落，不能直接显示浏览器断网页。
8. **更新流程**：有明确的旧 SW 接管策略（`skipWaiting` + `clients.claim`，或提示用户刷新）。
9. **敏感路径隔离**：`/api/**`、认证相关路径不得被导航回落吞掉（bundler 系用 `navigateFallbackDenylist`）。

---

## 五、现状符合度（2026-10-10，整改后实测）

图例：● 符合　◐ 部分/待收口　○ 缺失

下表为**整改完成后**按脚本逐项实测的结果（head/manifest 以产物或源文件正则核验，图标资产以静态目录遍历核验，构建均通过）：

| 仓库 | 技术栈 | SW 实现 | 资产契约 | head 契约 | manifest 契约 | SW 行为契约 |
| :--- | :--- | :--- | :-: | :-: | :-: | :-: |
| 慎始 shenshi | React+Vite | 手写 | ● | ● | ● | ● |
| 幕间 mujian | SvelteKit | 手写模板 | ● | ● | ● | ● |
| 吾身 diarum | SvelteKit | vite-pwa | ● | ● | ● | ● |
| 图切 tuqie | React+Vite | 手写 | ● | ● | ● | ● |
| ntfy | React+Vite | vite-pwa(inject) | ● | ● | ● | ● |
| 拾帧集 bili-history | Nuxt 3 | vite-pwa | ● | ● | ● | ● |
| 青野集 qingye | SvelteKit | vite-pwa | ● | ● | ● | ● |
| 流霞 liuxia | Go+原生 | Go 生成 | ● | ● | ● | ● |
| 月汐 yuexi | Go+Alpine | 静态 sw.js | ● | ● | ● | ● |
| 货殖 huozhi | SvelteKit | vite-pwa | ● | ● | ● | ● |
| 牵丝 qiansi | Svelte+Vite | vite-pwa | ● | ● | ● | ● |
| 察新 chaxin | Go+原生 | 手写 | ● | ● | ● | ● |

**脚注（不构成缺口）**

- **牵丝**：`index.html` 源码中没有 manifest link，由 vite-plugin-pwa 在构建时注入（dist 已验证）；`format-detection` 等以构建产物为准。
- **拾帧集**：head 项写在 `nuxt.config.ts` 的 `app.head`（Nuxt 对象语法，非 HTML 字面量）；manifest link 由 @vite-pwa/nuxt 注入。
- **月汐**：七件图标资产由 Go 运行时动态绘制（无磁盘文件），路由覆盖与契约一致。
- **流霞 / 图切 / 月汐**：manifest 路径保留 `/manifest.json`，但以 `application/manifest+json` 显式响应（见第三节，合规）。
- **ntfy**：manifest 由 Go `handleWebManifest` 动态生成，screenshots 复用应用图标（上游行为，保留）。

> 本表之前的一版「基线表」（含多处 ◐/○）记录的是整改前状态，历史差异见 `PWA支持现状审计.md` §六整改记录。RSSRob / WebMonitor / QBHive / whoami / healthchecks 明确不做 PWA，不在本表范围。

---

## 六、接入/自检清单

新仓库接入或存量仓库对齐时：**先跑脚本，再过人工项**。

### 6.1 自动自检（脚本覆盖前 7 条）

在本仓库根目录执行，默认扫描工作区下全部已知 Web 应用：

```bash
python3 scripts/check-pwa.py            # 全部
python3 scripts/check-pwa.py shenshi    # 只看指定仓库
```

脚本逐项核对：

- [x] 图标七件套齐全（文件名 / 尺寸 / 存在性）
- [x] `maskable` 为**满幅无透明**的位图、主体落在 80% 安全圆内（需 Pillow）
- [x] `apple-touch-icon` 为 PNG 且不透明（需 Pillow）
- [x] `<head>` 含 `theme-color` / `manifest` link / iOS meta / 四档 icon 声明
- [x] manifest 含 `id` / `scope` / `categories`，`any` 与 `maskable` 分离
- [x] SW 存在，且缓存名版本化、`activate` 清旧、离线条目有上限
- [x] 构建产物中图标 / manifest / SW 均存在且路径正确（产物存在时）

> 没装 Pillow 时脚本仍会跑完，只是跳过位图深度校验并在结尾提示
> `pip install pillow` —— 这是有意的：**不能因为缺一个可选依赖就让整个自检不可用**。

### 6.2 人工项（脚本判不了）

- [ ] manifest 以 `application/manifest+json` 响应（Go 侧要显式设，见第三节）
- [ ] 敏感路径（`/api/**`、认证）不被导航回落吞掉
- [ ] 主题色在浅/深两种外观下都正确（A 案两条 / B 案单条 + JS 覆盖，别混用）
- [ ] 真实浏览器验证：DevTools → Application → Manifest 无警告 / Service Workers 已激活

### 6.3 判定误报的处理

脚本扫的是**源文件与产物**，但有些仓库的 head / manifest link 是构建时注入的，
源文件里根本没有字面量。遇到「扫出缺失但产物里其实有」的情况，把该仓库加进
`scripts/check-pwa.py` 的 `KNOWN_INJECTED` 白名单并注明注入方，不要反过来放宽契约。
