#!/usr/bin/env python3
"""PWA 图标契约生成器 —— 由单张方形源图产出标准图标集。

产出（写入 --out 目录）:
  favicon.ico            16 / 32 / 48 多尺寸
  icon-192.png           192x192   manifest purpose=any
  icon-512.png           512x512   manifest purpose=any
  icon-maskable-192.png  192x192   manifest purpose=maskable
  icon-maskable-512.png  512x512   manifest purpose=maskable
  apple-touch-icon.png   180x180   iOS 主屏

约定（见 ../PWA规范.md）:
- any 图标：源图原样缩放，保留设计自带的圆角与透明边。
- maskable / apple-touch：做「满幅出血」处理——按自动求得的 zoom 中心放大裁切，
  把圆角外的透明区裁掉、画面铺满整幅；若仍有残留透明，用 --bg 采样色兜底。
  自动 zoom 的判据是：放大后四条边的角点必须不透明（即圆角被裁掉）。

用法:
  python gen-icons.py --src logo.png --out ./static --svg logo.svg
  python gen-icons.py --src logo.png --out ./static --bg '#6366f1'   # 无自带底色的 logo
  python gen-icons.py --src logo.png --out ./static --zoom 1.15      # 强制 zoom
"""
from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

MASK_192 = 192
MASK_512 = 512
ATC_SIZE = 180
ICO_SIZES = [(16, 16), (32, 32), (48, 48)]


def load_square_rgba(path: str) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    if im.width != im.height:
        s = min(im.width, im.height)
        left = (im.width - s) // 2
        top = (im.height - s) // 2
        im = im.crop((left, top, left + s, top + s))
    return im


def resize_square(im: Image.Image, size: int) -> Image.Image:
    return im.resize((size, size), Image.LANCZOS)


def auto_zoom(im: Image.Image, max_zoom: float = 1.5) -> float:
    """求最小 zoom：使放大裁切后四条边的角点均不透明（圆角被裁掉）。"""
    w, _ = im.size
    alpha = im.split()[3]
    for d in range(0, w // 2):
        pts = [(d, d), (w - 1 - d, d), (d, w - 1 - d), (w - 1 - d, w - 1 - d)]
        if all(alpha.getpixel(p) > 250 for p in pts):
            if d == 0:
                return 1.0
            return min(w / (w - 2 * d), max_zoom)
    return max_zoom


def cover_square(im: Image.Image, zoom: float) -> Image.Image:
    """按 zoom 中心放大后裁切回原尺寸 → 满幅。"""
    w, _ = im.size
    if zoom <= 1.0:
        return im.copy()
    z = int(round(w * zoom))
    big = im.resize((z, z), Image.LANCZOS)
    off = (z - w) // 2
    return big.crop((off, off, off + w, off + w))


def fill_transparent(im: Image.Image) -> Image.Image:
    """把透明像素按最近的不透明邻居逐层扩张填满（边缘外扩）。

    用于「满幅插画型」图标：既保住原设计不被放大裁切，又消除圆角外的透明。
    比用单一底色平铺更自然——补出来的颜色就是紧邻边缘自身的颜色，没有色块接缝。
    """
    out = im.copy()
    px = out.load()
    w, h = out.size
    remaining = {(x, y) for y in range(h) for x in range(w) if px[x, y][3] < 255}
    while remaining:
        filled: dict[tuple[int, int], tuple[int, ...]] = {}
        for x, y in remaining:
            acc = [0, 0, 0, 0]
            n = 0
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in remaining:
                    c = px[nx, ny]
                    acc[0] += c[0]
                    acc[1] += c[1]
                    acc[2] += c[2]
                    acc[3] += c[3]
                    n += 1
            if n:
                filled[(x, y)] = (acc[0] // n, acc[1] // n, acc[2] // n, acc[3] // n)
        if not filled:
            break
        for pos, c in filled.items():
            px[pos[0], pos[1]] = c
            remaining.discard(pos)
    return out


def sample_bg(im: Image.Image) -> tuple[int, int, int, int]:
    """采样设计底色：取上边缘中央（通常落在设计的实心底内）。"""
    w, _ = im.size
    y = max(1, int(w * 0.02))
    for dy in range(0, max(2, int(w * 0.1))):
        px = im.getpixel((w // 2, min(y + dy, w - 1)))
        if px[3] > 250:
            return px
    return (255, 255, 255, 255)


def unmatte(im: Image.Image, bg: tuple[int, int, int, int]) -> tuple[Image.Image, str]:
    """去掉"被拍平的底色"(matte)。

    有些源 PNG 是"圆角图形 + 白色底"拍平而来（例如由截图/导出工具生成），
    四角是不透明但与设计底色不同的颜色。这会让 maskable 出现白角、失去满幅。
    做法：从四角做洪水填充，把与角点同色且连通的区域改为透明。
    由于图形边界（不同色）会截断连通性，不会误伤图形内部的同色元素。
    """
    w, _ = im.size
    corner = im.getpixel((0, 0))
    if corner[3] <= 250:
        return im, "四角已透明，无需去背"
    if all(abs(corner[i] - bg[i]) <= 6 for i in range(3)) and corner[3] == bg[3]:
        return im, "四角颜色与底色一致，无需去背"

    out = im.copy()
    for seed in [(0, 0), (w - 1, 0), (0, w - 1), (w - 1, w - 1)]:
        ImageDraw.floodfill(out, seed, (0, 0, 0, 0), thresh=12)
    # 去背后若产生半透明毛边，统一阈值归零；再腐蚀 2px 去掉"混向白色"的抗锯齿环，
    # 否则放大出血后会在四角留下比底色更亮的晕边。
    a = out.split()[3].point(lambda v: 0 if v < 200 else 255)
    a = a.filter(ImageFilter.MinFilter(5)).point(lambda v: 0 if v < 200 else 255)
    out.putalpha(a)
    return out, f"四角底色 #{corner[0]:02x}{corner[1]:02x}{corner[2]:02x} 已按连通区域去除（含 2px 抗锯齿环）"


def flatten(im: Image.Image, bg: tuple[int, int, int, int]) -> Image.Image:
    canvas = Image.new("RGBA", im.size, bg)
    canvas.alpha_composite(im)
    return canvas


def parse_bg(value: str | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) == 6:
        v += "ff"
    if len(v) == 8:
        return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]
    raise SystemExit(f"无法解析颜色: {value}")


def corners_transparent(im: Image.Image) -> bool:
    w, _ = im.size
    a = im.split()[3]
    return any(a.getpixel(p) < 250 for p in [(0, 0), (w - 1, 0), (0, w - 1), (w - 1, w - 1)])


def main() -> int:
    ap = argparse.ArgumentParser(description="PWA 图标契约生成器")
    ap.add_argument("--src", required=True, help="方形源图（PNG，建议 >=512）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--svg", default=None, help="可选：随附的 SVG 源，将被复制为 favicon.svg")
    ap.add_argument("--bg", default=None, help="maskable/apple-touch 的兜底底色，如 '#6366f1'")
    ap.add_argument("--zoom", type=float, default=None, help="强制出血放大倍数（默认自动求解）")
    ap.add_argument(
        "--matte",
        choices=["auto", "none"],
        default="auto",
        help="是否自动去除被拍平的角底色（默认 auto）",
    )
    ap.add_argument(
        "--mask-mode",
        choices=["zoom", "fill"],
        default="zoom",
        help="maskable/apple-touch 满幅方式：zoom=放大裁切（图标型，默认）；fill=边缘外扩填角（满幅插画型）",
    )
    args = ap.parse_args()

    if not os.path.isfile(args.src):
        raise SystemExit(f"源图不存在: {args.src}")
    os.makedirs(args.out, exist_ok=True)

    raw = load_square_rgba(args.src)
    bg = parse_bg(args.bg) or sample_bg(raw)
    src, matte_note = unmatte(raw, bg) if args.matte == "auto" else (raw, "已跳过（--matte none）")
    if args.mask_mode == "fill":
        # 满幅插画型：不缩放，直接把圆角外的透明按边缘颜色外扩补齐
        zoom = 1.0
        bleed = flatten(fill_transparent(src), bg)
        mode_note = "fill（边缘外扩填角，保原设计不裁切）"
    else:
        zoom = args.zoom if args.zoom else auto_zoom(src)
        bleed = flatten(cover_square(src, zoom), bg)
        mode_note = f"zoom（放大裁切 x{zoom:.3f}）"

    written: list[str] = []

    def save(im: Image.Image, name: str) -> None:
        p = os.path.join(args.out, name)
        im.save(p)
        written.append(f"{name}  {im.width}x{im.height}")

    # any：保留设计自带的圆角与透明
    save(resize_square(src, 192), "icon-192.png")
    save(resize_square(src, 512), "icon-512.png")
    # maskable / apple-touch：满幅出血
    save(resize_square(bleed, MASK_192), "icon-maskable-192.png")
    save(resize_square(bleed, MASK_512), "icon-maskable-512.png")
    save(resize_square(bleed, ATC_SIZE), "apple-touch-icon.png")
    # favicon.ico
    ico = os.path.join(args.out, "favicon.ico")
    resize_square(src, 256).save(ico, format="ICO", sizes=ICO_SIZES)
    written.append("favicon.ico  16/32/48")
    # favicon.svg
    if args.svg:
        if not os.path.isfile(args.svg):
            raise SystemExit(f"SVG 不存在: {args.svg}")
        with open(args.svg, "rb") as f:
            data = f.read()
        with open(os.path.join(args.out, "favicon.svg"), "wb") as f:
            f.write(data)
        written.append("favicon.svg  (复制自 " + os.path.basename(args.svg) + ")")

    print(f"源图   : {args.src}  ({raw.width}x{raw.height})")
    print(f"底色   : #{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}  满幅方式: {mode_note}")
    print(f"去背   : {matte_note}")
    warn = " ⚠️ 仍有残留透明，已用底色兜底" if corners_transparent(bleed) else ""
    print(f"满幅校验: {'通过' if not corners_transparent(bleed) else '未通过'}{warn}")
    print(f"输出目录: {args.out}")
    for line in written:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
