"""Reusable image fitting and e-ink quantization helpers.

Pipeline for any image that lands on an e-ink panel:

    fit (high-quality resample) -> tone/sharpen preparation -> dither -> palette

E-ink panels only reproduce 2-4 flat colors, so the perceptual quality is decided
*before* dithering: how well the source is downscaled (aliasing / moire), how the
tonality is stretched (flat midtones collapse to white), and how chroma is handled
(low-saturation pixels must not be sprayed with yellow/red noise).
"""
from __future__ import annotations

import colorsys
import math

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from . import native_dither

# Resampling: LANCZOS everywhere, with Pillow's multi-step pre-reduction for large
# downscales (a 3356px artwork -> 248px block in one step aliases badly; reducing_gap
# makes Pillow box-average first and only then apply the LANCZOS kernel).
_REDUCING_GAP = 3.0

# Photo preparation defaults (tuned for black/white[/yellow/red] panels).
_PHOTO_SAT_CUT = 0.20        # below this chroma we desaturate (kills color speckle)
_PHOTO_SAT_LOW_POW = 0.75    # pull faint chroma towards grey
_PHOTO_SAT_HIGH_POW = 0.70   # push real chroma towards the pure panel colors
_TARGET_LUMA = 0.52          # mid-grey target for auto gamma
_GAMMA_SLOPE = 0.90
_GAMMA_MIN = 0.88
_GAMMA_MAX = 1.28

# Heuristics for "is this a photo-like continuous-tone image?"
_AUTO_MIN_PIXELS = 48 * 48
_AUTO_MIN_COLORS = 24


def _aligned_offset(container: int, content: int, align: str) -> int:
    if align in ("left", "top", "start"):
        return 0
    if align in ("right", "bottom", "end"):
        return container - content
    return (container - content) // 2


def resample(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """LANCZOS resample with multi-step pre-reduction for large downscales."""
    src_w, src_h = img.size
    dst_w, dst_h = size
    if (src_w, src_h) == (dst_w, dst_h):
        return img
    gap = _REDUCING_GAP if (dst_w < src_w or dst_h < src_h) else None
    try:
        return img.resize((dst_w, dst_h), Image.LANCZOS, reducing_gap=gap)
    except TypeError:  # pragma: no cover - Pillow < 8 has no reducing_gap
        return img.resize((dst_w, dst_h), Image.LANCZOS)


def fit_image_to_box(
    src: Image.Image,
    width: int,
    height: int,
    *,
    fit: str = "fill",
    align_x: str = "center",
    align_y: str = "center",
) -> Image.Image:
    """Fit an image into an RGB box using JSON image-block semantics."""
    src_rgba = ImageOps.exif_transpose(src).convert("RGBA")
    fit_mode = str(fit or "fill").lower()
    if fit_mode in ("fill", "stretch"):
        base = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        base.alpha_composite(resample(src_rgba, (width, height)))
        return base.convert("RGB")

    src_w = max(1, src_rgba.size[0])
    src_h = max(1, src_rgba.size[1])
    scale_x = width / src_w
    scale_y = height / src_h

    if fit_mode == "contain_blur":
        # 前景：等比缩放到完整可见（contain 语义，绝不拉伸/裁切主体）
        fg_scale = min(scale_x, scale_y)
        fg_w = max(1, int(round(src_w * fg_scale)))
        fg_h = max(1, int(round(src_h * fg_scale)))
        fg = resample(src_rgba, (fg_w, fg_h))

        # 背景：同一张图 cover 填满 + 强高斯模糊 + 压暗，避免留白同时突出前景
        bg_scale = max(scale_x, scale_y)
        bg_w = max(1, int(round(src_w * bg_scale)))
        bg_h = max(1, int(round(src_h * bg_scale)))
        bg = resample(src_rgba, (bg_w, bg_h))
        blur_radius = max(8, int(round(min(width, height) * 0.07)))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        base = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        base.alpha_composite(
            bg,
            (_aligned_offset(width, bg_w, align_x), _aligned_offset(height, bg_h, align_y)),
        )
        bg_rgb = ImageEnhance.Brightness(base.convert("RGB")).enhance(0.80)
        bg_rgb = ImageEnhance.Color(bg_rgb).enhance(0.85)
        base = bg_rgb.convert("RGBA")

        base.alpha_composite(
            fg,
            (_aligned_offset(width, fg_w, align_x), _aligned_offset(height, fg_h, align_y)),
        )
        return base.convert("RGB")

    scale = min(scale_x, scale_y) if fit_mode == "contain" else max(scale_x, scale_y)
    resized_w = max(1, int(round(src_w * scale)))
    resized_h = max(1, int(round(src_h * scale)))
    resized = resample(src_rgba, (resized_w, resized_h))
    base = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    paste_x = _aligned_offset(width, resized_w, align_x)
    paste_y = _aligned_offset(height, resized_h, align_y)
    base.alpha_composite(resized, (paste_x, paste_y))
    return base.convert("RGB")


def _gamma_lut(gamma: float) -> list[int]:
    inv = 1.0 / gamma
    return [min(255, int(255.0 * ((i / 255.0) ** inv) + 0.5)) for i in range(256)]


# 墨水屏只有黑/白(/黄/红)极少数颜色，照片中间调在量化后会大面积坍缩成白色，
# 因此必须在上游主动加深中间调、提高色彩浓度。
_PHOTO_GAMMA_LUT = _gamma_lut(0.86)


def _mean_luma(img: Image.Image) -> float:
    """Average luma in 0..1, sampled on a tiny thumbnail (cost is negligible)."""
    small = img.convert("L").resize((32, 32), Image.BOX)
    hist = small.histogram()
    total = sum(hist) or 1
    return sum(i * h for i, h in enumerate(hist)) / total / 255.0


def _mean_saturation(img: Image.Image) -> float:
    """Average HSV saturation (0..255), sampled cheaply for photo/scan gating."""
    return sum(img.convert("RGB").convert("HSV").resize((32, 32), Image.BOX).getchannel("S").getdata()) / 1024


def _auto_gamma(mean_luma: float) -> float:
    """Bright sources get compressed (gamma<1), dark sources lifted (gamma>1)."""
    gamma = 1.0 - (mean_luma - _TARGET_LUMA) * _GAMMA_SLOPE
    return max(_GAMMA_MIN, min(_GAMMA_MAX, gamma))


def _saturation_lut(cut: float, low_pow: float, high_pow: float) -> list[int]:
    """S-curve on the HSV saturation channel.

    Faint chroma (JPEG noise, off-white backgrounds) is pushed to grey so it can
    never be dithered into yellow/red speckle; real chroma is pushed outward so it
    quantizes onto the pure panel colors instead of muddy mid-tones.
    """
    lut: list[int] = []
    for i in range(256):
        x = i / 255.0
        if x <= 0.0:
            y = 0.0
        elif x >= 1.0:
            y = 1.0
        elif x < cut:
            y = x * ((x / cut) ** low_pow)
        else:
            t = (x - cut) / (1.0 - cut)
            y = cut + (1.0 - cut) * (t ** high_pow)
        lut.append(max(0, min(255, int(y * 255.0 + 0.5))))
    return lut


_SAT_LUT = _saturation_lut(_PHOTO_SAT_CUT, _PHOTO_SAT_LOW_POW, _PHOTO_SAT_HIGH_POW)
# Selective micro-contrast mask: protect near-neutral sky, clouds, and runway
# from extra texture while allowing colored photographic subjects to gain detail.
_PHOTO_DETAIL_MASK_LUT = [
    0 if value <= 20 else 255 if value >= 80 else int((value - 20) * 255 / 60 + 0.5)
    for value in range(256)
]

# 六色面板有 4 个彩色端点（黄/红/蓝/绿），误判的代价比四色大得多：水墨的墨色、
# 扫描件的偏色都可能被"吸"成实心蓝或绿，整屏发脏。所以六色的彩度门槛显著抬高
# （0.20 -> 0.42）：饱和度低于 42% 一律归灰、交给黑白抖动，只有真正的彩色才上色。
_SAT6_CUT = 0.26
_SAT6_LOW_POW = 0.60
_SAT6_HIGH_POW = 0.55
_SAT6_LUT = _saturation_lut(_SAT6_CUT, _SAT6_LOW_POW, _SAT6_HIGH_POW)

# ── 暖色许可（四色面板专用）───────────────────────────────────────────────
# 四色面板只有黄/红两个端点，绿青蓝紫必须归灰。但硬阈值会在暖冷交界处留下一道
# 生硬的边界，所以这里用 20° 的软过渡带把权重平滑衰减到 0。
# Pillow 的 HSV 色相 0..255 映射到 0..360°，暖弧取 -30°..56°（即 330..360 与 0..56）。
_WARM_HI_DEG = 56.0
_WARM_LO_DEG = 330.0
_WARM_RAMP_DEG = 20.0


def _warm_ramp_lut() -> list[int]:
    lut: list[int] = []
    for i in range(256):
        deg = i * 360.0 / 255.0
        if deg <= _WARM_HI_DEG or deg >= _WARM_LO_DEG:
            d = 0.0
        else:
            d = min(deg - _WARM_HI_DEG, _WARM_LO_DEG - deg)
        lut.append(int(255.0 * max(0.0, 1.0 - d / _WARM_RAMP_DEG) + 0.5))
    return lut


_WARM_RAMP_LUT = _warm_ramp_lut()


def _panel_hue(hue: int) -> int:
    """Pillow HSV hue (0..255 == 0..360°) of one e-ink palette entry."""
    from .config import EINK_6COLOR_PALETTE

    r, g, b = EINK_6COLOR_PALETTE[hue * 3 : hue * 3 + 3]
    return Image.new("RGB", (1, 1), (r, g, b)).convert("HSV").getpixel((0, 0))[0]


def _refine_chroma(
    img: Image.Image, color_gain: float = 1.35, colors: int = 4
) -> Image.Image:
    """提纯真彩色、漂白噪点，让墨水屏量化结果干净。

    四色面板（黑/白/黄/红）：
      - 冷色（绿青蓝紫）按暖色许可权重平滑归灰：面板没有对应色，保留只会变脏噪点
      - 暖色饱和度走 S 曲线：弱色归零、强色提纯

    六色面板（黑/白/黄/红/蓝/绿）：
      - 蓝/绿是面板真实可显色，**不再**强制去饱和，否则白白丢掉两个通道
      - 低彩度像素（墨色、偏色扫描件）由 S 曲线压到近灰，交给黑白抖动

    ⚠️ 关键：归灰必须回到**亮度 L**，不能用 HSV 的 V。V 是 RGB 最大通道，
    蓝天 (100,167,221) 的 V=221 而亮度只有 153 —— 按 V 归灰会把整片天空刷成
    近白（实测 yunjuanyunshu 图在四色下白占 95.5%），这是四色"发白"的根因。
    """
    boosted = ImageEnhance.Color(img).enhance(color_gain)
    hsv = boosted.convert("HSV")
    h, s, v = hsv.split()
    sat_lut = _SAT6_LUT if colors >= 6 else _SAT_LUT
    # 保色权重：低彩度 → 0（走灰），高彩度 → 255（保留提纯后的色）
    keep = s.point(sat_lut)
    if colors < 6:
        keep = ImageChops.multiply(keep, h.point(_WARM_RAMP_LUT))
    shaped = Image.merge("HSV", (h, s.point(sat_lut), v)).convert("RGB")
    # ImageEnhance.Color 保持亮度不变，所以这里用原图亮度即可。
    gray = img.convert("L").convert("RGB")
    return Image.composite(shaped, gray, keep)


def _local_contrast(
    img: Image.Image,
    *,
    radius: float,
    percent: int,
    threshold: int = 3,
) -> Image.Image:
    """Wide-radius unsharp mask == cheap local contrast (CLAHE-like "通透感")."""
    return img.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))


def looks_like_photo(img: Image.Image) -> bool:
    """Cheap heuristic: continuous-tone photo vs. flat icon/line art.

    Icons and 1-bit art must NOT be tone-mapped (autocontrast/USM would wreck them),
    so auto-detection is deliberately conservative.
    """
    if img.mode == "1":
        return False
    w, h = img.size
    if w * h < _AUTO_MIN_PIXELS:
        return False
    small = img.convert("RGB").resize((64, 64), Image.BOX)
    if len(set(small.getdata())) < _AUTO_MIN_COLORS:
        return False
    return True


def enhance_photo_for_eink(rgb: Image.Image, colors: int = 4) -> Image.Image:
    """Photo preparation tuned for the limited e-ink palette (4 or 6 colors).

    - autocontrast：拉开色阶
    - 局部对比（大半径 USM）：救回被压缩的中间调层次，避免大片糊成一坨
    - 色度净化：弱色去饱和（杜绝噪点），强色提纯；四色面板杀掉冷色，
      六色面板改为把色相吸附到黄/红/蓝/绿四个端点
    - 自适应 gamma：按画面平均亮度决定压深还是提亮（原来固定 0.86，暗图会更暗）
    - 全局对比 + 锐化：提升墨水屏上的通透感与边缘清晰度
    """
    img = ImageOps.autocontrast(rgb.convert("RGB"), cutoff=0.5)

    width, height = img.size
    big_radius = max(4.0, min(width, height) * 0.025)
    img = _local_contrast(img, radius=big_radius, percent=60, threshold=4)

    # 六色面板可用蓝/绿墨色补照片暗部；适度提亮中间调并提高有效彩度，
    # 避免暗照片量化后大面积坍缩成黑色。四色路径保持原有调校不变。
    color_gain = 1.85 if colors >= 6 else 1.35
    img = _refine_chroma(img, color_gain, colors)
    mean_luma = _mean_luma(img)
    gamma = _auto_gamma(mean_luma)
    if colors >= 6:
        # Add extra lift only as the source gets darker. A fixed lift washed out
        # already-bright photos, while this brings dark scenes toward mid-grey.
        dark_scene_lift = max(0.0, min(0.30, (_TARGET_LUMA - mean_luma) * 0.70))
        gamma += 0.24 + dark_scene_lift
        gamma = min(_GAMMA_MAX + 0.57, gamma)
    img = img.point(_gamma_lut(gamma) * 3)
    img = ImageEnhance.Contrast(img).enhance(1.20 if colors >= 6 else 1.18)

    if colors >= 6:
        # Aircraft liveries and small decals need local separation after the
        # 800x480 downscale. Apply this micro-contrast only where the source has
        # real chroma so neutral sky/runway noise is not sharpened equally.
        source_sat = rgb.convert("RGB").convert("HSV").getchannel("S")
        chroma_mask = source_sat.point(_PHOTO_DETAIL_MASK_LUT)
        detail = _local_contrast(img, radius=1.4, percent=125, threshold=5)
        img = Image.composite(detail, img, chroma_mask)

    img = ImageEnhance.Sharpness(img).enhance(1.30 if colors >= 6 else 1.25)
    return img.filter(
        ImageFilter.UnsharpMask(
            radius=1.0 if colors >= 6 else 0.9,
            percent=135 if colors >= 6 else 110,
            threshold=3 if colors >= 6 else 2,
        )
    )


def enhance_graphic_for_eink(rgb: Image.Image) -> Image.Image:
    """Light preparation for flat art / icons: stretch levels, no tone remapping."""
    return ImageOps.autocontrast(rgb.convert("RGB"), cutoff=0.2)


def enhance_bw_for_eink(rgb: Image.Image) -> Image.Image:
    """Grayscale preparation for 1-bit output (much more than a plain autocontrast)."""
    gray = ImageOps.autocontrast(rgb.convert("L"), cutoff=0.5)
    width, height = gray.size
    gray = _local_contrast(gray, radius=max(4.0, min(width, height) * 0.025), percent=75)
    gray = gray.point(_gamma_lut(_auto_gamma(_mean_luma(gray))))
    gray = ImageEnhance.Contrast(gray).enhance(1.20)
    gray = ImageEnhance.Sharpness(gray).enhance(1.25)
    return gray.filter(ImageFilter.UnsharpMask(radius=0.9, percent=110, threshold=2))


def _resolve_enhance(img: Image.Image, enhance: str | None, photo_enhance: bool) -> str:
    """Map legacy ``photo_enhance`` + new ``enhance`` onto a concrete mode."""
    if photo_enhance:
        return "photo"
    mode = str(enhance or "off").strip().lower()
    if mode in ("auto", "smart", ""):
        return "photo" if looks_like_photo(img) else "graphic"
    if mode in ("photo", "on", "true", "1", "yes"):
        return "photo"
    if mode in ("graphic", "flat", "icon"):
        return "graphic"
    return "off"


def dither_to_palette(
    rgb: Image.Image,
    colors: int,
    dither: str = "atkinson",
    *,
    gate=None,
) -> Image.Image:
    """Run the final error-diffusion / quantization step.

    ``gate`` 是 ``_chroma_gate()`` 的预计算结果（彩色掩码 + 端点索引图）。
    应当由调用方在**增强之前**的原图上算好再传进来 —— 增强会放大饱和度，
    若在增强后重算，淡淡的墨色/偏色会被 S 曲线推成"真彩色"而重新混进彩色
    抖动（这正是水墨图被撒上蓝点的原因）。
    """
    mode = str(dither or "atkinson").strip().lower()
    if colors < 3:
        if mode in ("none", "off", "threshold"):
            return rgb.convert("L").point(_THRESHOLD_LUT).convert("1")
        if mode in ("floyd", "floyd-steinberg", "fs"):
            return rgb.convert("L").convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        return native_dither.atkinson_bw(rgb if rgb.mode == "L" else rgb.convert("L"))

    if mode in ("none", "off", "threshold"):
        return _nearest_palette(rgb, colors)
    return _dither_with_chroma_gate(rgb, colors, mode, gate)


# ── 彩度闸门：谁才配得上"上色" ────────────────────────────────────────────
# 墨水屏的彩色端点（黄/红/蓝/绿）在 RGB 空间里离中灰比黑白色还近：实测
# (128,128,128) 到面板蓝只有 13937、到黑/白却要 ~49000。所以只要把灰度像素
# 混进彩色调色板做误差扩散，整屏立刻被撒满蓝/绿/黄点，层次也被吃掉。
# 因此必须先把像素分成「真彩色」和「中性」两段：中性走纯黑白 Atkinson
# （灰阶干净、层次完整），真彩色才进彩色抖动。
#
# 判据用 Lab 的 a/b（绝对色差，与明度解耦），不用 HSV 饱和度：
# 深色像素的 HSV 饱和度会虚高 —— 墨褐 (40,32,24) 的 HSV S=102/255，
# 但它的 Lab 彩度只有 10；而面板绿是 49、蓝是 68、黄是 89、红是 125。
# 用 HSV 阈值就会把水墨、暗部、木纹、肤色判成彩色，Lab 判据天然干净。
_ABS_DEV_LUT = [abs(i - 128) for i in range(256)]

# 闸门按"端点自身彩度的 42%"取值：像素彩度必须达到该端点的四成以上才允许
# 落到这个颜色上。红色本身最艳，门槛也最高（不会把肤色、砖灰染红）；
# 绿/蓝本身偏暗，门槛低（树叶、天空不会被整片丢掉）。
_GATE_RATIO = 0.42
# ⚠️ 但比例法对**暗端点**会算出过低的门槛：面板绿彩度只有 49、蓝 68，
# 42% 分别是 21 和 29 —— 而水墨画 moyun 的彩度 p99 就已经到 29（max 36），
# 于是整幅水墨被判成"有蓝色"，实测刷出 1.3% 的蓝点。
# 实测各类真实色彩的 Lab 彩度：天空 44、深空 51、树叶 66、向日葵 80、
# 草 87、玫瑰 112；水墨/扫描件这类近中性内容则普遍 < 36。
# 把绝对下限抬到 38，正好卡在这两条分布之间：水墨全灭、天空仍有 6 的余量。
_GATE_MIN = 38
# 大连佳显这块 Spectra 6 的蓝、绿墨色比屏幕色卡上的标准 RGB 更柔和；
# 对应的原始图片彩度常落在 30 多。暖色端点仍保持 38，避免浅肤色/暖灰被染色，
# 冷色端点略降到 34，让有连续色面的淡蓝天空、青绿电路也能进入六色抖动。
# 该值仍高于近中性色噪声主体区，且过渡带保持原宽度。
_GATE_MIN_COOL = 34
_DARK_COOL_MIN_SAT = 88
_DARK_COOL_MIN_VALUE = 28
_DARK_COOL_MAX_VALUE = 176
_DARK_COOL_SAT_LUT = [255 if v >= _DARK_COOL_MIN_SAT else 0 for v in range(256)]
_DARK_COOL_VALUE_LUT = [
    255 if _DARK_COOL_MIN_VALUE <= v <= _DARK_COOL_MAX_VALUE else 0
    for v in range(256)
]


def _panel_endpoints(colors: int) -> tuple[int, ...]:
    """该色数面板可用的彩色索引。"""
    if colors >= 6:
        return (2, 3, 4, 5)   # 黄 / 红 / 蓝 / 绿
    if colors == 4:
        return (2, 3)         # 黄 / 红
    return (3,)               # 三色板只有红


def _panel_lab_chroma(idx: int) -> int:
    """某个面板色的 Lab 彩度（|Δa| + |Δb|，Pillow LAB 0..255 缩放）。"""
    from .config import EINK_6COLOR_PALETTE

    r, g, b = EINK_6COLOR_PALETTE[idx * 3 : idx * 3 + 3]
    _, a, b_ch = Image.new("RGB", (1, 1), (r, g, b)).convert("LAB").getpixel((0, 0))
    return abs(a - 128) + abs(b_ch - 128)


_ENDPOINT_LUT_CACHE: dict[int, tuple[list[int], list[int]]] = {}


def _panel_lab_ab(idx: int) -> tuple[float, float]:
    """某个面板色在 Lab 彩度平面上的向量（相对中性点 128）。"""
    from .config import EINK_6COLOR_PALETTE

    r, g, b = EINK_6COLOR_PALETTE[idx * 3 : idx * 3 + 3]
    _, a, b_ch = Image.new("RGB", (1, 1), (r, g, b)).convert("LAB").getpixel((0, 0))
    return float(a - 128), float(b_ch - 128)


_HUE_LAB_CACHE: list[tuple[float, float]] | None = None


def _hue_lab_vectors() -> list[tuple[float, float]]:
    """256 个代表色相（S=0.85, V=0.75）在 Lab 彩度平面上的向量 (Δa, Δb)。

    ⚠️ 必须一次性把 256 个颜色拼成一张 256×1 的图再整体转 Lab。Pillow 的
    RGB→LAB 走 LittleCMS，每次 convert 都要重建一次色彩变换（实测 ~10ms），
    逐个像素转换 256 次就是 2.8 秒 —— 这是过去每次冷启动都要付的冤枉钱。
    """
    global _HUE_LAB_CACHE
    if _HUE_LAB_CACHE is not None:
        return _HUE_LAB_CACHE
    row = []
    for h in range(256):
        rr, gg, bb = colorsys.hsv_to_rgb(h / 255.0, 0.85, 0.75)
        row.append((int(rr * 255), int(gg * 255), int(bb * 255)))
    strip = Image.new("RGB", (256, 1))
    strip.putdata(row)
    lab = strip.convert("LAB")
    out = [(float(a) - 128.0, float(b) - 128.0) for _, a, b in lab.getdata()]
    _HUE_LAB_CACHE = out
    return out


def _endpoint_luts(colors: int) -> tuple[list[int], list[int]]:
    """(色相 → 最近端点索引, 色相 → 该端点的彩度闸门) 两张 256 项 LUT。

    端点归属按 **Lab 彩度平面的夹角** 判定，而不是按 RGB/HSV 色相。
    原因：面板绿是偏青的深绿（H≈163°），而照片里的草地/树叶多是黄绿（H≈90~105°），
    按色相划分会把整片草地判进黄色端点（实测草地绿 (110,180,65) 落在色相中点
    偏黄一侧）；换成彩度平面夹角后草地归绿、天空仍归蓝，只有真正偏黄的新芽
    才走黄色端点。LUT 仍以 HSV 色相为索引，所以应用端依旧只是一次 point()。
    """
    cached = _ENDPOINT_LUT_CACHE.get(colors)
    if cached is not None:
        return cached
    endpoints = _panel_endpoints(colors)
    hues = {i: _panel_hue(i) for i in endpoints}
    ab = {i: _panel_lab_ab(i) for i in endpoints}
    ab_norm = {i: math.hypot(*v) or 1e-9 for i, v in ab.items()}

    idx_lut: list[int] = []
    for h in range(256):
        # 用该色相下最具代表性的 RGB（饱和度高、亮度中上）来定归属
        va, vb = _hue_lab_vectors()[h]
        na = math.hypot(va, vb)
        if na < 4.0:
            # 近中性色相（理论上的灰轴）：退回色相就近，反正闸门会拦掉
            best, best_d = endpoints[0], 1 << 30
            for i in endpoints:
                d = abs(h - hues[i])
                d = min(d, 256 - d)
                if d < best_d:
                    best_d, best = d, i
            idx_lut.append(best)
            continue
        best, best_cos = endpoints[0], -2.0
        for i in endpoints:
            ea, eb = ab[i]
            cos = (va * ea + vb * eb) / (na * ab_norm[i])
            if cos > best_cos:
                best_cos, best = cos, i
        idx_lut.append(best)
    thr_lut = [0] * 256
    for i in endpoints:
        min_chroma = _GATE_MIN_COOL if colors >= 6 and i in (4, 5) else _GATE_MIN
        thr_lut[i] = max(min_chroma, int(round(_GATE_RATIO * _panel_lab_chroma(i))))
    _ENDPOINT_LUT_CACHE[colors] = (idx_lut, thr_lut)
    return idx_lut, thr_lut


def _panel_lab_angle(idx: int) -> float:
    """面板色在 Lab 彩度平面上的极角（弧度）。"""
    a, b = _panel_lab_ab(idx)
    return math.atan2(b, a)


def _arc_dist(x: float, y: float) -> float:
    """两个极角之间的最小夹角（0..π）。"""
    d = abs(x - y) % (2.0 * math.pi)
    return min(d, 2.0 * math.pi - d)


def _arc_contains(a: float, b: float, t: float) -> bool:
    """极角 t 是否落在 a→b 的逆时针弧内。"""
    tau = 2.0 * math.pi
    a %= tau
    b %= tau
    t %= tau
    if a <= b:
        return a <= t <= b
    return t >= a or t <= b


def _panel_pairs(colors: int) -> tuple[tuple[int, ...], ...]:
    """把面板彩色端点按 Lab 彩度平面的极角排成**相邻对**。

    为什么要成对而不是单个端点：单端点子集（黑+白+绿）只能表达"绿的一个
    明度序列"，中间色相全部被迫吸附到最近端点，渐变处会出现硬色带。
    成对后相邻两端点可以混色：

        六色 → (黄,红) (黄,绿) (绿,蓝) (蓝,红)
               橙=黄+红、黄绿=黄+绿、青=绿+蓝、紫=蓝+红
        四色 → (黄,红)            橙由黄红混出
        三色 → (红,)

    误差扩散会自动挑选最近的颜色，所以纯色区域依然出纯色，只有渐变区才混。
    """
    endpoints = _panel_endpoints(colors)
    if len(endpoints) < 2:
        return (tuple(endpoints),)
    order = sorted(endpoints, key=_panel_lab_angle)
    if len(order) == 2:
        return (tuple(order),)
    return tuple((order[k], order[(k + 1) % len(order)]) for k in range(len(order)))


_PAIR_LUT_CACHE: dict[int, list[int]] = {}


def _pair_luts(colors: int) -> list[int]:
    """色相(Pillow 0..255) → 相邻端点对的序号。"""
    cached = _PAIR_LUT_CACHE.get(colors)
    if cached is not None:
        return cached
    pairs = _panel_pairs(colors)
    endpoints = {e for p in pairs for e in p}
    angles = {i: _panel_lab_angle(i) for i in endpoints}
    pair_angles = [(angles[p[0]], angles[p[1]]) for p in pairs]
    lut: list[int] = []
    for h in range(256):
        va, vb = _hue_lab_vectors()[h]
        t = math.atan2(vb, va)
        best, best_d = 0, 1 << 30
        for pid, (pa, pb) in enumerate(pair_angles):
            if _arc_contains(pa, pb, t):
                best = pid
                break
            d = _arc_dist(t, pa) + _arc_dist(t, pb)
            if d < best_d:
                best_d, best = d, pid
        lut.append(best)
    _PAIR_LUT_CACHE[colors] = lut
    return lut


# ── 闸门软过渡：8×8 Bayer 有序抖动 ────────────────────────────────────────
# 彩度闸门的硬阈值会在"有色/无色"交界处留下一条生硬的轮廓。给阈值加一条窄
# 过渡带（±_GATE_HALF 个 Lab 彩度单位），再用 Bayer 矩阵把过渡带抖成网点，
# 边界就化开了。过渡带很窄（不到彩度全量程的 6%），不会引入可见彩噪。
_GATE_FEATHER = 14
_GATE_HALF = _GATE_FEATHER // 2
_GATE_RAMP_LUT = [min(255, int(255.0 * v / _GATE_FEATHER + 0.5)) for v in range(256)]

# point() 传函数对象会让 Pillow 退回逐像素的 Python 回调（800×480 就是 38 万次
# 调用、每次约 1µs）；传 256 项列表则走 C 查表。凡是热路径上的 point 一律用表。
_GATE_SUB_HALF_LUT = [max(0, v - _GATE_HALF) for v in range(256)]
_GATE_POS_LUT = [0] + [255] * 255
_BW_IDX_LUT = [1 if v > 127 else 0 for v in range(256)]
_THRESHOLD_LUT = [255 if v >= 128 else 0 for v in range(256)]

_BAYER8 = [
    0, 32, 8, 40, 2, 34, 10, 42,
    48, 16, 56, 24, 50, 18, 58, 26,
    12, 44, 4, 36, 14, 46, 6, 38,
    60, 28, 52, 20, 62, 30, 54, 22,
    3, 35, 11, 43, 1, 33, 9, 41,
    51, 19, 59, 27, 49, 17, 57, 25,
    15, 47, 7, 39, 13, 45, 5, 37,
    63, 31, 55, 23, 61, 29, 53, 21,
]
_BAYER8 = [v * 255 // 64 for v in _BAYER8]
_BAYER_CACHE: dict[tuple[int, int], Image.Image] = {}


def _bayer_tile(size: tuple[int, int]) -> Image.Image:
    """把 8×8 Bayer 矩阵平铺成 size 大小的 L 模式图。"""
    key = (size[0], size[1])
    cached = _BAYER_CACHE.get(key)
    if cached is not None:
        return cached
    w, h = size
    rows = [bytes(_BAYER8[r * 8 + (x & 7)] for x in range(w)) for r in range(8)]
    data = bytearray()
    for y in range(h):
        data += rows[y & 7]
    img = Image.frombytes("L", (w, h), bytes(data))
    if len(_BAYER_CACHE) > 8:
        _BAYER_CACHE.clear()
    _BAYER_CACHE[key] = img
    return img


def _chroma_gate(rgb: Image.Image, colors: int, *, photo: bool = False):
    """返回 (彩色掩码, 端点索引图)。

    掩码为 255 的像素才允许上色，其余一律交给黑白抖动。
    端点索引图给出每个像素被吸附到的面板端点（2/3/4/5）。
    """
    idx_lut, thr_lut = _endpoint_luts(colors)
    if photo and colors >= 6:
        # Natural photos contain subdued sky/foliage colors that the stricter
        # graphic/scan threshold intentionally rejects. Admit those colors in
        # photo mode, but keep a floor so near-neutral noise stays monochrome.
        # The Bayer gate below is also disabled for photos: its 8x8 threshold
        # pattern is conspicuous on large smooth regions (e.g. sky).
        photo_thr_lut = list(thr_lut)
        # Keep warm endpoints strict: airport grass/soil and off-white fuselage
        # can otherwise acquire yellow/red speckle. The cool inks need the extra
        # allowance because their physical pigments are unusually dark.
        for endpoint in (4, 5):
            photo_thr_lut[endpoint] = max(23, int(thr_lut[endpoint] * 0.72))
        thr_lut = photo_thr_lut
    lab = rgb.convert("LAB")
    chroma = ImageChops.add(
        lab.getchannel("A").point(_ABS_DEV_LUT),
        lab.getchannel("B").point(_ABS_DEV_LUT),
    )
    hsv = rgb.convert("HSV")
    hue, sat, value = hsv.split()
    ep = hue.point(idx_lut)
    thr = ep.point(thr_lut)
    # 软过渡：chroma 落在 [thr-half, thr+half] 之间时给一个 0..255 的中间权重
    lo = thr.point(_GATE_SUB_HALF_LUT)
    soft = ImageChops.subtract(chroma, lo).point(_GATE_RAMP_LUT)
    if colors < 6:
        # 三色/四色面板没有冷色端点。绿青蓝紫必须归灰，否则会被"最近的端点"
        # 吸附成黄或红 —— 照片里的天空、树叶会整片染色（暖色许可带软过渡）。
        soft = ImageChops.multiply(
            soft, rgb.convert("HSV").getchannel("H").point(_WARM_RAMP_LUT)
        )
    gate = (
        soft.point(_GATE_POS_LUT)
        if photo and colors >= 6
        else ImageChops.subtract(soft, _bayer_tile(rgb.size)).point(_GATE_POS_LUT)
    )
    if colors >= 6:
        # Spectra 6 的蓝/绿墨水本身较暗。Lab 彩度会随亮度下降而收缩，
        # 于是深蓝阴影虽然色相鲜明，却被原彩度门槛当成近灰并量成黑色。
        # 仅对蓝/绿端点上有明确 HSV 饱和度、且不在近黑区的像素开例外；
        # 棕色仍归黄/红，近中性色仍由 Lab 门槛保护。
        cool_endpoint = ep.point([255 if i in (4, 5) else 0 for i in range(256)])
        saturated = sat.point(_DARK_COOL_SAT_LUT)
        visible = value.point(_DARK_COOL_VALUE_LUT)
        dark_cool = ImageChops.multiply(
            cool_endpoint, ImageChops.multiply(saturated, visible)
        ).filter(ImageFilter.MedianFilter(size=3))
        gate = ImageChops.lighter(gate, dark_cool)
    return gate, ep


def _floyd_onto(rgb: Image.Image, idxs: tuple[int, ...], colors: int) -> Image.Image:
    """把 rgb 用 Floyd-Steinberg 量化到 idxs 指定的面板色子集上。

    子集通常是「黑 + 白 + 相邻两个彩色端点」：浅色被抖成「白 + 彩点」、
    深色被抖成「黑 + 彩点」，亮度信息由黑白承担，色相由两个端点插值。
    走 Pillow 的 C 实现（quantize），比纯 Python 的误差扩散快一个数量级。
    """
    pal = _palette_bytes(colors)
    sub: list[int] = []
    for i in idxs:
        sub.extend(pal[i * 3 : i * 3 + 3])
    pal_img = Image.new("P", (1, 1))
    pal_img.putpalette(sub + [0] * (768 - len(sub)))
    out = rgb.convert("RGB").quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
    remap = [0] * 256
    for pos, i in enumerate(idxs):
        remap[pos] = i
    out = out.point(remap)
    out.putpalette(pal)
    return out


def _dither_with_chroma_gate(rgb: Image.Image, colors: int, mode: str, gate=None) -> Image.Image:
    """两段式抖动：中性区走黑白、彩色区走面板彩色。"""
    pal = _palette_bytes(colors)
    flavored = mode in ("floyd", "floyd-steinberg", "fs")
    gray = rgb if rgb.mode == "L" else rgb.convert("L")
    if flavored:
        bw = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    else:
        bw = native_dither.atkinson_bw(gray)
    # "1"(0/255) -> 0/1 索引，正好对上所有面板调色板的 黑=0 白=1
    bw_idx = bw.convert("L").point(_BW_IDX_LUT).convert("P")
    bw_idx.putpalette(pal)

    if gate is None:
        gate = _chroma_gate(rgb, colors)
    mask, _ep = gate
    if mask.getbbox() is None:  # 整幅没有值得上色的像素
        return bw_idx

    pairs = _panel_pairs(colors)
    out = bw_idx
    if len(pairs) == 1:
        selectors = [mask]
    else:
        # 每个像素按色相落到某一对相邻端点上，混色只在这一对内部发生，
        # 因此不会出现"绿区里蹦蓝点"这类跨色相串扰。
        pair_map = rgb.convert("HSV").getchannel("H").point(_pair_luts(colors))
        selectors = [
            ImageChops.multiply(
                mask, pair_map.point([255 if v == pid else 0 for v in range(256)])
            )
            for pid in range(len(pairs))
        ]
    for subset, sel in zip(pairs, selectors):
        if sel.getbbox() is None:
            continue
        out = Image.composite(_floyd_onto(rgb, (0, 1) + tuple(subset), colors), out, sel)
    out.putpalette(pal)
    return out


# 3-color panels carry black/white/red; the wire value for red stays 3 (yellow slot
# unused) so the firmware mapping matches the native ditherer exactly.
_INDEX_TO_EINK_3COLOR = [0, 1, 3, 3] + [3] * 252


def _floyd_palette(rgb: Image.Image, colors: int) -> Image.Image:
    """Pillow's C-implemented Floyd-Steinberg onto the e-ink palette (denser than Atkinson)."""
    pal_img = Image.new("P", (1, 1))
    pal_img.putpalette(_quant_palette_bytes(colors))
    out = rgb.convert("RGB").quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
    if colors < 4:
        out = out.point(_INDEX_TO_EINK_3COLOR)
    out.putpalette(_palette_bytes(colors))
    return out


def _nearest_palette(rgb: Image.Image, colors: int) -> Image.Image:
    """Hard nearest-color mapping: maximum contrast, banding instead of texture."""
    pal_img = Image.new("P", (1, 1))
    pal_img.putpalette(_quant_palette_bytes(colors))
    out = rgb.convert("RGB").quantize(palette=pal_img, dither=Image.Dither.NONE)
    if colors < 4:
        out = out.point(_INDEX_TO_EINK_3COLOR)
    out.putpalette(_palette_bytes(colors))
    return out


def _quant_palette_bytes(colors: int) -> list[int]:
    from .config import EINK_4COLOR_PALETTE, EINK_6COLOR_PALETTE

    base = list(EINK_6COLOR_PALETTE) if colors >= 6 else list(EINK_4COLOR_PALETTE)
    if colors < 4:
        # Index 2 is unused on 3-color panels; park red there so nothing maps to yellow.
        base = base[:6] + base[9:12] + base[9:12]
    return base + [0] * (768 - len(base))


def _palette_bytes(colors: int) -> list[int]:
    from .config import EINK_4COLOR_PALETTE, EINK_6COLOR_PALETTE

    base = list(EINK_6COLOR_PALETTE) if colors >= 6 else list(EINK_4COLOR_PALETTE)
    return base + [0] * (768 - len(base))


def quantize_image_for_eink(
    rgb: Image.Image,
    *,
    colors: int,
    photo_enhance: bool = False,
    enhance: str | None = None,
    dither: str = "atkinson",
) -> Image.Image:
    """Quantize RGB image data for 2-, 3-, or 4-color e-ink output."""
    mode = _resolve_enhance(rgb, enhance, photo_enhance)

    if colors < 3:
        if mode == "photo":
            prepared = enhance_bw_for_eink(rgb)
        else:
            prepared = rgb.convert("L")
            if mode == "graphic":
                prepared = ImageOps.autocontrast(prepared, cutoff=0.2)
        return dither_to_palette(prepared, colors, dither)

    # 彩度闸门在**增强之前**的原图上判定：增强（提饱和 + S 曲线）只负责让
    # 已经够彩的像素更艳，不应该改变"哪些像素算彩色"这个判断本身。
    photo_color_gate = mode == "photo" and colors >= 6 and _mean_saturation(rgb) >= 38
    chroma_gate = _chroma_gate(rgb.convert("RGB"), colors, photo=photo_color_gate)

    if mode == "photo":
        prepared = enhance_photo_for_eink(rgb, colors)
    elif mode == "graphic":
        prepared = enhance_graphic_for_eink(rgb)
    else:
        prepared = rgb.convert("RGB")

    return dither_to_palette(prepared, colors, dither, gate=chroma_gate)


def convert_image_block(
    src: Image.Image,
    width: int,
    height: int,
    colors: int,
    *,
    fit: str = "fill",
    align_x: str = "center",
    align_y: str = "center",
    photo_enhance: bool = False,
    enhance: str | None = None,
    dither: str = "atkinson",
) -> Image.Image:
    """Fit and quantize an image for a JSON image block."""
    fitted = fit_image_to_box(src, width, height, fit=fit, align_x=align_x, align_y=align_y)
    return quantize_image_for_eink(
        fitted,
        colors=colors,
        photo_enhance=photo_enhance,
        enhance=enhance,
        dither=dither,
    )
