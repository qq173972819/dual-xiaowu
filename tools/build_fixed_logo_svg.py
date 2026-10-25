from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from collections import deque

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "logo1-xiaowu-ai.png"
PNG_OUT = ROOT / "xiaowu-ai-logo-fixed-transparent.png"
SVG_OUT = ROOT / "xiaowu-ai-logo-fixed.svg"


def remove_connected_background(image: Image.Image) -> Image.Image:
    """Remove only near-white pixels connected to the canvas edge."""
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    seen = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def is_background(x: int, y: int) -> bool:
        r, g, b, _ = pixels[x, y]
        return r >= 244 and g >= 244 and b >= 244

    def add(x: int, y: int) -> None:
        idx = y * width + x
        if not seen[idx] and is_background(x, y):
            seen[idx] = 1
            queue.append((x, y))

    for x in range(width):
        add(x, 0)
        add(x, height - 1)
    for y in range(height):
        add(0, y)
        add(width - 1, y)

    while queue:
        x, y = queue.popleft()
        pixels[x, y] = (*pixels[x, y][:3], 0)
        if x:
            add(x - 1, y)
        if x + 1 < width:
            add(x + 1, y)
        if y:
            add(x, y - 1)
        if y + 1 < height:
            add(x, y + 1)
    return rgba


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)

    transparent = remove_connected_background(Image.open(SOURCE))
    transparent.save(PNG_OUT, optimize=True)

    buffer = BytesIO()
    transparent.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    width, height = transparent.size

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg"
  xmlns:xlink="http://www.w3.org/1999/xlink"
  viewBox="0 0 {width} {height}"
  width="{width}" height="{height}"
  preserveAspectRatio="xMidYMid meet"
  role="img" aria-labelledby="title desc">
  <title id="title">小悟AI品牌标志</title>
  <desc id="desc">保持确认稿原始比例与字形的透明背景品牌标志</desc>
  <image x="0" y="0" width="{width}" height="{height}"
    preserveAspectRatio="xMidYMid meet"
    href="data:image/png;base64,{encoded}"
    xlink:href="data:image/png;base64,{encoded}"/>
</svg>
'''
    SVG_OUT.write_text(svg, encoding="utf-8")


if __name__ == "__main__":
    main()
