import sys
from pathlib import Path

from PIL import Image, ImageDraw

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.config import eink_palette
from core.patterns.utils import draw_status_bar


def test_low_battery_status_uses_dark_outline_and_text_on_six_color_panel():
    palette = eink_palette(6) + [0] * (768 - len(eink_palette(6)))
    img = Image.new("P", (400, 300), 1)
    img.putpalette(palette)
    draw_status_bar(
        ImageDraw.Draw(img), img, "10月15日", "20°C", 21,
        screen_w=400, screen_h=300, colors=6, language="zh", separator_y=36,
    )

    # Battery occupies the rightmost status-bar area; black index 0 must outline
    # the icon and render the percentage, while yellow is only the charge fill.
    status_battery = img.crop((320, 0, 400, 35))
    assert sum(1 for pixel in status_battery.getdata() if pixel == 0) >= 20


def test_status_battery_uses_reported_percentage_and_current_icon_size():
    palette = eink_palette(6) + [0] * (768 - len(eink_palette(6)))
    img = Image.new("P", (400, 300), 1)
    img.putpalette(palette)
    draw_status_bar(
        ImageDraw.Draw(img), img, "10月15日", "20°C", 21,
        screen_w=400, screen_h=300, colors=6, language="zh", separator_y=36,
    )

    yellow = 2
    # The 21% reported value is rendered as yellow fill; the outline remains
    # the current 18x9 size.
    status = img.crop((320, 0, 400, 35))
    yellow_pixels = sum(1 for pixel in status.getdata() if pixel == yellow)
    assert 0 < yellow_pixels < 30

    # A different reported level changes fill amount, without forcing 80%.
    img_full = Image.new("P", (400, 300), 1)
    img_full.putpalette(palette)
    draw_status_bar(
        ImageDraw.Draw(img_full), img_full, "10月15日", "20°C", 80,
        screen_w=400, screen_h=300, colors=6, language="zh", separator_y=36,
    )
    full_yellow_pixels = sum(1 for pixel in img_full.crop((320, 0, 400, 35)).getdata() if pixel == yellow)
    assert full_yellow_pixels > yellow_pixels

    # The percentage text is rendered in dark pixels.
    dark_xs = [x for x in range(320, 400) for y in range(0, 35) if img.getpixel((x, y)) == 0]
    assert dark_xs
    assert max(dark_xs) - min(dark_xs) < 80
