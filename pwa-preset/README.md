# pwa-preset — PWA 落地预设包

与 [`../PWA规范.md`](../PWA规范.md) 配套的可复用资产。新仓库接入或老仓库对齐时，直接复制/调用这里的东西，避免各写一套。

## 内容

| 文件 | 用途 |
| :--- | :--- |
| `gen-icons.py` | **由一张方形源图产出全套契约图标**（favicon.ico + any 192/512 + maskable 192/512 + apple-touch 180），含自动出血与满幅校验 |
| `head.html` | 标准 `<head>` PWA 块（meta + link 顺序固定） |
| `manifest.template.json` | 标准 manifest 字段集与顺序 |
| `sw-template.js` | 手写 SW 标准模板（版本化缓存 + 条目上限 + 离线兜底） |

## 快速用法

### 1. 生成图标

```bash
# 由现成的方形 PNG 生成（脚本会自动求出血倍数、采样底色）
python3 gen-icons.py --src web/public/icons/icon-512.png --out web/public

# 源图只有 SVG 时，先栅格化成 512 PNG 再跑（macOS 用 QuickLook）
qlmanage -t -s 512 -o /tmp/logo web/public/logo.svg   # 产出 /tmp/logo/logo.svg.png
python3 gen-icons.py --src /tmp/logo/logo.svg.png --out web/public --svg web/public/logo.svg

# 设计本身没有铺满的背景（纯 logo 透明图）时，显式给底色
python3 gen-icons.py --src logo.png --out static --bg '#6366f1'
```

脚本会打印 `出血 zoom` 与 `满幅校验` 结果；**务必肉眼复核生成的 maskable 与 apple-touch**，确认主体没有超出 80% 安全圆。

### 2. 接入 head 块

把 `head.html` 的占位符替换后粘进页面 `<head>`（Vite/Next 等有模板能力时可直接 `include`）。

### 3. 接入 manifest

- **用 bundler 的仓库**（Vite / SvelteKit / Nuxt）：优先走 `vite-plugin-pwa` 系的 `manifest` 配置项，字段集照 `manifest.template.json`，不必手写文件。
- **Go / 静态直出的仓库**：照 `manifest.template.json` 写实体文件，并确保以 `application/manifest+json` 响应。

> ⚠️ Go 的 `mime.TypeByExtension(".webmanifest")` 返回空串。若用 Go 的 `http.FileServer` 直出 `.webmanifest`，必须显式设置 `Content-Type`；否则改用 `.json`（Go 映射为 `application/json`，浏览器可接受）。

### 4. 接入 Service Worker

- **用 bundler 的仓库**：用 `vite-plugin-pwa` 的 `generateSW`（常规）或 `injectManifest`（需要自定义逻辑，如 Web Push）。**不要手写 SW 注册代码**，交给插件的 `virtual:pwa-register`。
- **Go / 静态直出的仓库**：以 `sw-template.js` 为骨架，构建时把 `__BUILD_VERSION__` 替换成版本号（git SHA / 文件 mtime），并在页面里注册：

```html
<script>
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  }
</script>
```
