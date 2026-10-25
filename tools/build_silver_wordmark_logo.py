from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ICON_SOURCE = ROOT / "xiaowu-ai-logo-fixed-transparent.png"
WORDMARK_SOURCE = ROOT / "xiaowu-ai-logo-for-black-shell-silver.png"
PNG_OUT = ROOT / "xiaowu-ai-logo-original-icon-silver-wordmark.png"
SVG_OUT = ROOT / "xiaowu-ai-logo-original-icon-silver-wordmark.svg"


def alpha_crop(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("Image contains no visible pixels")
    return image.crop(bbox)


def main() -> None:
    # Preserve the approved original icon exactly; discard the old bold wordmark.
    icon_full = Image.open(ICON_SOURCE).convert("RGBA")
    icon = alpha_crop(icon_full.crop((110, 90, 625, 790)))

    # Preserve only the previously approved light silver-gray wordmark.
    word_full = Image.open(WORDMARK_SOURCE).convert("RGBA")
    wordmark = alpha_crop(word_full.crop((825, 120, 2050, 650)))
    clean = Image.new("RGBA", wordmark.size, (0, 0, 0, 0))
    source_alpha = wordmark.getchannel("A")
    clean_alpha = source_alpha.point(lambda value: 255 if value >= 96 else 0)
    clean.paste((184, 186, 189, 255), (0, 0, *wordmark.size), clean_alpha)
    wordmark = clean

    target_icon_height = 520
    icon = icon.resize(
        (round(icon.width * target_icon_height / icon.height), target_icon_height),
        Image.Resampling.LANCZOS,
    )
    target_word_height = 286
    wordmark = wordmark.resize(
        (round(wordmark.width * target_word_height / wordmark.height), target_word_height),
        Image.Resampling.LANCZOS,
    )

    gap = 58
    padding_x = 42
    padding_y = 36
    width = padding_x * 2 + icon.width + gap + wordmark.width
    height = padding_y * 2 + max(icon.height, wordmark.height)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(icon, (padding_x, padding_y))
    word_y = padding_y + (icon.height - wordmark.height) // 2
    canvas.alpha_composite(wordmark, (padding_x + icon.width + gap, word_y))
    canvas.save(PNG_OUT, optimize=True)

    buffer = BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg"
  xmlns:xlink="http://www.w3.org/1999/xlink"
  viewBox="0 0 {width} {height}" width="{width}" height="{height}"
  preserveAspectRatio="xMidYMid meet" role="img" aria-labelledby="title desc">
  <title id="title">小悟AI标志</title>
  <desc id="desc">原始水滴猴子图标搭配轻量银灰色定制小悟AI字标，透明背景</desc>
  <image x="0" y="0" width="{width}" height="{height}"
    preserveAspectRatio="xMidYMid meet"
    href="data:image/png;base64,{encoded}"
    xlink:href="data:image/png;base64,{encoded}"/>
</svg>
'''
    SVG_OUT.write_text(svg, encoding="utf-8")


if __name__ == "__main__":
    main()
