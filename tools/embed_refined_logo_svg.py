from __future__ import annotations

import base64
from pathlib import Path
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PNG = ROOT / "xiaowu-ai-logo-refined.png"
SVG = ROOT / "xiaowu-ai-logo-refined.svg"


def main() -> None:
    with Image.open(PNG) as image:
        width, height = image.size
    encoded = base64.b64encode(PNG.read_bytes()).decode("ascii")
    SVG.write_text(
        f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
  viewBox="0 0 {width} {height}" width="{width}" height="{height}"
  preserveAspectRatio="xMidYMid meet" role="img" aria-labelledby="title desc">
  <title id="title">小悟AI精修品牌标志</title>
  <desc id="desc">暖石墨灰小悟字标、香槟金AI与水滴猴子AI图标，透明背景</desc>
  <image width="{width}" height="{height}" preserveAspectRatio="xMidYMid meet"
    href="data:image/png;base64,{encoded}" xlink:href="data:image/png;base64,{encoded}"/>
</svg>\n''',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
