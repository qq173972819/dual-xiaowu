"""量化评估墨水屏抖动管线（不依赖人眼，用于回归与调参）。

用法::

    python scripts/eval_dither.py [--size 800x480] [--save]

输出每张图在 2/4/6 色下的：耗时、面板色占比、中性区误上色比例。
另外用两张合成图做硬指标判定：
  · gray_ramp  —— 纯灰阶渐变，任何彩色像素都是 bug（应 0%）
  · vivid_chart—— 高饱和色块，彩色占比应显著（否则过抑）
"""
from __future__ import annotations

import argparse
import os
import sys
import time

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from PIL import Image  # noqa: E402

from core.image_processing import convert_image_block  # noqa: E402

ART_DIR = os.path.join(BACKEND_ROOT, "static", "art")
NAMES = ["zhulinqixian.png", "shanjuqiuming.png", "chanyilianhua.png", "moyun.png", "yunjuanyunshu.png"]

NAMES_6 = {0: "黑", 1: "白", 2: "黄", 3: "红", 4: "蓝", 5: "绿"}


def make_gray_ramp(w: int, h: int) -> Image.Image:
    g = Image.new("L", (w, 1))
    g.putdata([int(255 * i / (w - 1)) for i in range(w)])
    return g.resize((w, h), Image.NEAREST).convert("RGB")


def make_vivid_chart(w: int, h: int) -> Image.Image:
    cols = [(255, 255, 255), (0, 0, 0), (255, 214, 0), (214, 0, 0),
            (0, 61, 165), (0, 128, 55), (240, 120, 20), (120, 60, 30)]
    img = Image.new("RGB", (w, h))
    cw = max(1, w // len(cols))
    for i, c in enumerate(cols):
        img.paste(Image.new("RGB", (cw, h), c), (i * cw, 0))
    return img


def stats(img: Image.Image, colors: int) -> dict:
    if img.mode == "1":
        hist = img.histogram()
        total = sum(hist) or 1
        return {"白": hist[255] / total, "黑": hist[0] / total}
    px = list(img.getdata())
    total = len(px) or 1
    out = {}
    for i in range(colors):
        c = px.count(i)
        if c:
            out[NAMES_6.get(i, str(i))] = c / total
    return out


def fmt(d: dict) -> str:
    return " ".join(f"{k}{v*100:5.1f}%" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))


def fidelity(prepared: Image.Image, rendered: Image.Image) -> float:
    """抖动结果的**平均色**与源的平均色之间的平均绝对误差（0..255）。

    抖动本质是空间换色深，单点必然有误差，但**局部平均**必须回到原色，
    否则整幅就会偏色/发灰/发脏。这个指标比任何"彩色占比"都更能说明好坏。
    """
    pr = list(prepared.convert("RGB").getdata())
    rr = list(rendered.convert("RGB").getdata())
    n = len(pr)
    step = max(1, n // 20000)  # 抽样，够用且快
    sa = [0, 0, 0]
    sb = [0, 0, 0]
    cnt = 0
    for i in range(0, n, step):
        a, b = pr[i], rr[i]
        for k in range(3):
            sa[k] += a[k]
            sb[k] += b[k]
        cnt += 1
    return sum(abs(sa[k] - sb[k]) / cnt for k in range(3)) / 3.0


def run(name: str, src: Image.Image, w: int, h: int, colors: int, save: bool, out_dir: str) -> None:
    from core.image_processing import (
        enhance_photo_for_eink,
        fit_image_to_box,
        looks_like_photo,
    )

    fitted = fit_image_to_box(src, w, h, fit="cover")
    prepared = enhance_photo_for_eink(fitted, colors) if looks_like_photo(fitted) else fitted
    t0 = time.perf_counter()
    out = convert_image_block(src, w, h, colors, fit="cover", enhance="auto")
    dt = time.perf_counter() - t0
    s = stats(out, colors)
    pal = out.getpalette() or []
    if pal:
        rendered = out.convert("RGB")
    else:
        rendered = out.convert("RGB")
    fid = fidelity(prepared, rendered)
    print(f"  {colors}c  {dt*1000:7.0f}ms  色偏 {fid:5.1f}  {fmt(s)}")
    if save:
        p = os.path.join(out_dir, f"eval-{name}-{w}x{h}-{colors}c.png")
        rendered.save(p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="800x480")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    w, h = (int(x) for x in args.size.split("x"))
    out_dir = os.path.join(BACKEND_ROOT, "docs", "design")
    os.makedirs(out_dir, exist_ok=True)

    print(f"[real art] {w}x{h}")
    for n in NAMES:
        path = os.path.join(ART_DIR, n)
        if not os.path.exists(path):
            print(f"  [skip] {n}")
            continue
        print(f"{n}")
        with Image.open(path) as src:
            src.load()
            for colors in (2, 4, 6):
                run(n[:-4], src, w, h, colors, args.save, out_dir)

    print("\n[synthetic] gray ramp (彩色占比应为 0)")
    g = make_gray_ramp(w, h)
    for colors in (4, 6):
        run("gray_ramp", g, w, h, colors, args.save, out_dir)

    print("\n[synthetic] vivid chart (彩色占比应显著)")
    v = make_vivid_chart(w, h)
    for colors in (4, 6):
        run("vivid_chart", v, w, h, colors, args.save, out_dir)


if __name__ == "__main__":
    main()
