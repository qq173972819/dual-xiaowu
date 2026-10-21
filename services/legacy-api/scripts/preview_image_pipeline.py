"""离线对比「图片 → 墨水屏」渲染管线的前后效果。

用法::

    python scripts/preview_image_pipeline.py [输出目录]

会对 backend/static/art 下的素材按 4 色 / 2 色分别跑：
  · before —— 旧管线（单步 LANCZOS + 固定 gamma + Atkinson）
  · after  —— 新管线（多级降采样 + 局部对比 + 色度净化 + 自适应 gamma + Atkinson）

另外输出一张 before/after 的并排对比图，便于人眼评审。
"""
from __future__ import annotations

import os
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps  # noqa: E402

from core import native_dither  # noqa: E402
from core.image_processing import convert_image_block  # noqa: E402

ART_DIR = os.path.join(BACKEND_ROOT, "static", "art")

# 目标尺寸取自真实模式：ARTWALL 800x480 下 248x166，MOYU 400x234
CASES = [
    ("zhulinqixian.png", 400, 234, "cover"),
    ("shanjuqiuming.png", 248, 166, "cover"),
    ("chanyilianhua.png", 248, 166, "cover"),
    ("moyun.png", 400, 234, "cover"),
]


# ── 旧管线（改造前的实现，仅用于对照） ────────────────────────────


def _old_gamma_lut(gamma: float) -> list[int]:
    inv = 1.0 / gamma
    return [min(255, int(255.0 * ((i / 255.0) ** inv) + 0.5)) for i in range(256)]


_OLD_GAMMA = _old_gamma_lut(0.86)


def _old_enhance(rgb: Image.Image) -> Image.Image:
    img = ImageOps.autocontrast(rgb.convert("RGB"), cutoff=0.5)
    img = ImageEnhance.Color(img).enhance(1.45)
    img = img.point(_OLD_GAMMA * 3)
    img = ImageEnhance.Contrast(img).enhance(1.22)
    img = ImageEnhance.Sharpness(img).enhance(1.35)
    return img.filter(ImageFilter.UnsharpMask(radius=0.9, percent=100, threshold=2))


def _old_fit(src: Image.Image, width: int, height: int, fit: str) -> Image.Image:
    src_rgba = ImageOps.exif_transpose(src).convert("RGBA")
    sw, sh = src_rgba.size
    sx, sy = width / sw, height / sh
    if fit in ("fill", "stretch"):
        base = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        base.alpha_composite(src_rgba.resize((width, height), Image.LANCZOS))
        return base.convert("RGB")
    scale = min(sx, sy) if fit == "contain" else max(sx, sy)
    rw, rh = max(1, round(sw * scale)), max(1, round(sh * scale))
    resized = src_rgba.resize((rw, rh), Image.LANCZOS)
    base = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    base.alpha_composite(resized, ((width - rw) // 2, (height - rh) // 2))
    return base.convert("RGB")


def old_pipeline(src: Image.Image, w: int, h: int, colors: int, fit: str) -> Image.Image:
    """旧行为：默认不增强（photo_enhance=False），只有显式开启才走 _old_enhance。"""
    fitted = _old_fit(src, w, h, fit)
    if colors < 3:
        gray = ImageOps.autocontrast(fitted.convert("L"), cutoff=0.5)
        return native_dither.atkinson_bw(gray)
    return native_dither.atkinson_palette(fitted, 3 if colors == 3 else 4)


def new_pipeline(src: Image.Image, w: int, h: int, colors: int, fit: str) -> Image.Image:
    return convert_image_block(src, w, h, colors, fit=fit, enhance="auto")


# ── 输出 ──────────────────────────────────────────────────────────


def _to_preview(img: Image.Image) -> Image.Image:
    """把 P/1 模式结果按墨水屏调色板还原成可视 RGB。"""
    if img.mode == "1":
        return img.convert("RGB")
    pal = img.getpalette() or []
    if not pal:
        return img.convert("RGB")
    rgb = Image.new("RGB", img.size)
    px = img.load()
    out = rgb.load()
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            idx = px[x, y] & 0x0F
            if idx * 3 + 2 >= len(pal):
                idx &= 0x03
            out[x, y] = (pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2])
    return rgb


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BACKEND_ROOT, "docs", "design")
    os.makedirs(out_dir, exist_ok=True)

    panels: list[tuple[str, Image.Image]] = []
    label_h = 22

    for name, w, h, fit in CASES:
        path = os.path.join(ART_DIR, name)
        if not os.path.exists(path):
            print(f"[skip] {name} not found")
            continue
        with Image.open(path) as src:
            for colors in (6, 4, 2):
                # 旧管线最多只有 4 色，6 色一栏用旧 4 色结果充当「升级前」参照。
                old = _to_preview(old_pipeline(src, w, h, min(colors, 4), fit))
                new = _to_preview(new_pipeline(src, w, h, colors, fit))
                panels.append((f"{name} {w}x{h} {colors}c OLD", old))
                panels.append((f"{name} {w}x{h} {colors}c NEW", new))

                row = Image.new("RGB", (w * 2 + 12, h + label_h), "white")
                d = ImageDraw.Draw(row)
                d.text((2, 4), "OLD(4c max)" if colors == 6 else "OLD", fill="black")
                d.text((w + 12, 4), f"NEW {colors}c", fill="black")
                row.paste(old, (0, label_h))
                row.paste(new, (w + 12, label_h))
                row.save(os.path.join(out_dir, f"image-pipeline-{name[:-4]}-{colors}c.png"))
                print(f"[ok] image-pipeline-{name[:-4]}-{colors}c.png")

    # 汇总对比图
    if panels:
        cols = 2
        cw = max(p[1].size[0] for p in panels) + 8
        ch = max(p[1].size[1] for p in panels) + label_h + 8
        rows = (len(panels) + cols - 1) // cols
        sheet = Image.new("RGB", (cw * cols, ch * rows), "white")
        d = ImageDraw.Draw(sheet)
        for i, (label, img) in enumerate(panels):
            gx, gy = (i % cols) * cw, (i // cols) * ch
            d.text((gx + 2, gy + 2), label, fill="black")
            sheet.paste(img, (gx, gy + label_h))
        sheet_path = os.path.join(out_dir, "image-pipeline-compare.png")
        sheet.save(sheet_path)
        print(f"[ok] {sheet_path}")


if __name__ == "__main__":
    main()
