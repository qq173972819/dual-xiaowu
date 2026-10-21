"""
双屏模式渲染器
==============
由固件在 /api/render?persona=DUAL_A / DUAL_B 时调用。

- DUAL_A（A 屏 / FPC1 黑白快刷 SSD1619）：渲染便签，输出 1bpp BMP。
- DUAL_B（B 屏 / FPC2 彩色 SSD2683ZA）：渲染今日课程表，输出 2bpp 彩色原始帧
  （0=黑 1=白 2=黄 3=红，与固件 PackInkSightFourColor / image_to_raw_2bpp 一致）。

渲染逻辑全部在服务端（复用 PIL + 现有字体），固件侧只负责拉图 + 刷屏，
无需在 MCU 上做文本排版，规避墨水屏字号/编码痛点。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw

from .image_processing import fit_image_to_box, quantize_image_for_eink
from .patterns.utils import load_font_by_name

logger = logging.getLogger(__name__)

# 相册照片落地目录（与 api/routes/uploads.py 一致，复用其存储）。
# 注意：本文件在 backend/core/ 下，比 uploads.py（backend/api/routes/）浅一层，
# 故用 parents[1] 才能落到 backend/runtime_uploads（parents[2] 会错指到外层仓库根）。
_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "runtime_uploads"

# 中文星期（周一=0），与控制台 grid 的下标一致。
_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ── 相册（无数据时的默认兜底屏）──────────────────────────────
def _album_photos(dual_config: Optional[dict]) -> list[dict]:
    """从 dual_config.content.album.photos 取出有效照片列表。"""
    content = (dual_config or {}).get("content") or {}
    album = content.get("album") or {}
    if not isinstance(album, dict):
        return []
    photos = album.get("photos") or []
    out = []
    for p in photos:
        if isinstance(p, dict) and (p.get("id") or p.get("url")):
            out.append(p)
    return out


def _load_album_image(photo: dict) -> Optional[Image.Image]:
    """读相册照片为 RGB 图像；优先按上传 id 读本地文件，其次解析 url 里的 id。"""
    pid = (photo.get("id") or "").strip()
    if not pid and photo.get("url"):
        m = re.search(r"/api/uploads/([0-9a-fA-F-]{8}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{12})", str(photo.get("url")))
        if m:
            pid = m.group(1)
    if not pid:
        return None
    path = _UPLOAD_DIR / f"{pid}.bin"
    if not path.is_file():
        return None
    try:
        with Image.open(path) as img:
            return img.convert("RGB")
    except Exception as exc:  # 损坏/非图像 → 跳过这张
        logger.warning("[DUAL-RENDER] album image load failed id=%s: %s", pid, exc)
        return None


def _a_mode_has_data(mode: str, dual_config: Optional[dict]) -> bool:
    """判断 A 屏所选内容模式当前是否有数据（用于无数据→相册兜底）。"""
    content = (dual_config or {}).get("content") or {}
    today = datetime.now().strftime("%Y-%m-%d")
    if mode == "homework":
        items = content.get("homework") or []
        return any(isinstance(it, dict) and (it.get("date") or today) == today for it in items)
    if mode == "reading":
        return bool(content.get("reading"))
    if mode == "notes":
        items = content.get("notes") or []
        return any(isinstance(n, dict) and (n.get("persistent") or (n.get("date") or today) == today) for n in items)
    if mode == "album":
        return bool(_album_photos(dual_config))
    return True  # 未知模式不触发兜底


def _render_album_empty(w: int, h: int, colors: int, msg: str) -> Image.Image:
    """相册无照片 / 读取失败时的占位图。"""
    scale = w / 400.0
    if colors >= 3:
        img = _palette_p_image(w, h, colors)
    else:
        img = Image.new("1", (w, h), 1)
    draw = ImageDraw.Draw(img)
    draw.text((int(14 * scale), int(70 * scale)), msg, fill=0, font=_load_cjk(max(14, int(18 * scale))))
    return img


def render_album(mac: str, w: int, h: int, colors: int,
                 dual_config: Optional[dict]) -> Image.Image:
    """A/B 屏通用相册渲染：按「日」轮换展示一张照片，cover 裁切铺满整屏。

    复用正式照片管线 image_processing（Lab 色度闸门 + 照片增强 + Atkinson 抖动），
    与内容模式图片块同一套处理，避免自制灰度量化产生的满屏噪点。
    - colors<3（A 屏黑白）：enhance_bw + Atkinson 1bpp。
    - colors>=3（B 屏彩色）：enhance_photo + 色度门控抖动，输出面板索引 P 图。
    """
    photos = _album_photos(dual_config)
    if not photos:
        return _render_album_empty(w, h, colors, "暂无照片")
    today = datetime.now()
    base_idx = (today.month * 31 + today.day) % len(photos)

    # 个别照片缺失/损坏时，向后轮转找下一张能读出来的，不让单张故障整屏报废。
    src = None
    photo = None
    failed_ids: list[str] = []
    for step in range(len(photos)):
        candidate = photos[(base_idx + step) % len(photos)]
        loaded = _load_album_image(candidate)
        if loaded is not None:
            src = loaded
            photo = candidate
            break
        pid = (candidate.get("id") or "").strip()
        if not pid and candidate.get("url"):
            m = re.search(r"/api/uploads/([0-9a-fA-F-]{8}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{12})",
                          str(candidate.get("url")))
            if m:
                pid = m.group(1)
        if pid:
            failed_ids.append(pid)
    if src is None:
        logger.warning("[DUAL-RENDER] album: all %d photos failed to load (ids=%s)",
                       len(photos), ",".join(failed_ids))
        return _render_album_empty(w, h, colors, "照片读取失败")

    scale = w / 400.0
    caption = (photo.get("caption") or "").strip()
    # 底部说明文字条（有说明才留），其余区域全部给照片
    cap_h = int(26 * scale) if caption else 0
    box_h = max(1, h - cap_h)

    # cover 裁切铺满：竖版照片也占满整屏（居中裁切），不再 letterbox 留白。
    # fit_image_to_box 内部做 exif_transpose，手机竖拍方向不会错。
    fitted = fit_image_to_box(src, w, box_h, fit="cover")
    # 铺回完整 w×h 画布（说明条在底部），保证输出尺寸与面板严格一致。
    canvas = Image.new("RGB", (w, h), (255, 255, 255))
    canvas.paste(fitted, (0, 0))

    # 正式照片管线：照片增强 + 色度闸门 + Atkinson，索引与面板色码一致。
    img = quantize_image_for_eink(
        canvas,
        colors=colors,
        photo_enhance=True,
        enhance="photo",
        dither="atkinson",
    )

    if caption:
        draw = ImageDraw.Draw(img)
        # 底部白条 + 黑字（黑字在 1bpp 与调色板索引 0 均为黑）
        by = h - int(24 * scale)
        draw.rectangle([0, by - int(4 * scale), w, h], fill=1)
        font = _load_cjk(max(12, int(15 * scale)))
        draw.text((int(14 * scale), by), _ellipsis_text(draw, caption, font, w - int(28 * scale)),
                  fill=0, font=font)
    return img


# 便签底色（仅 B 屏彩色可见；A 屏黑白会退化为边框 + 文字）。
_NOTE_RGB = {
    "yellow": (255, 243, 199),
    "pink": (253, 228, 236),
    "blue": (220, 235, 251),
    "green": (223, 242, 228),
    "white": (255, 255, 255),
}

_BREAK_ROW = (0, 0, 0)  # 通栏固定行（眼保健操/大课间等）用黑色文字


def _load_cjk(size: int):
    try:
        return load_font_by_name("NotoSerifSC-Regular.ttf", size)
    except Exception:  # 兜底，绝不让渲染崩溃
        from PIL import ImageFont

        return ImageFont.load_default()


def _palette_p_image(w: int, h: int, colors: int = 4) -> Image.Image:
    """创建彩色调色板图像，索引 0..3 即面板色值。

    colors>=4（BWRY 四色）：0=黑 1=白 2=黄 3=红。
    colors==3（BWR 三色）：0=黑 1=白 2=红 3=红（无黄色，2/3 同为红）。
    固件 PackInkSightFourColor 把帧码 10 解释为面板“彩色”（四色屏=黄、三色屏=红），
    因此后端只需把强调色画到索引 2，设备端即按面板正确显示。
    """
    img = Image.new("P", (w, h), 1)  # 默认白底
    palette = []
    for i in range(256):
        if i == 0:
            palette += [0, 0, 0]          # 黑
        elif i == 1:
            palette += [255, 255, 255]    # 白
        elif i == 2:
            palette += [255, 214, 0] if colors >= 4 else [230, 30, 30]  # 黄 / 红
        elif i == 3:
            palette += [230, 30, 30]      # 红
        else:
            palette += [255, 255, 255]    # 其余一律白
    img.putpalette(palette)
    return img


def _draw_battery_indicator(draw: ImageDraw.ImageDraw, x_right: int, y_center: int,
                            scale: float, level: int, fg=0):
    """在 (x_right, y_center) 右对齐绘制电池 icon（外框+凸头+电量填充）。"""
    level = max(0, min(100, int(level)))
    bw = int(30 * scale)
    bh = int(14 * scale)
    cap = max(2, int(3 * scale))
    lw = max(1, int(2 * scale))
    x0 = x_right - bw
    y0 = y_center - bh // 2
    # 外框 + 正极凸头
    draw.rectangle([x0, y0, x0 + bw, y0 + bh], outline=fg, width=lw)
    draw.rectangle([x0 + bw + lw, y0 + bh // 2 - cap // 2,
                    x0 + bw + lw + cap, y0 + bh // 2 + cap - cap // 2], fill=fg)
    # 内部电量填充（按百分比宽度）
    inner_w = bw - lw - int(4 * scale)
    fill_w = int(inner_w * level / 100)
    if fill_w > 0:
        draw.rectangle([x0 + lw + int(1 * scale), y0 + lw + int(1 * scale),
                        x0 + lw + int(1 * scale) + fill_w,
                        y0 + bh - lw - int(1 * scale)], fill=fg)


def _draw_battery_with_text(draw: ImageDraw.ImageDraw, x_right: int, y_center: int,
                            scale: float, level: int, font, fg=0):
    """右上角：`85%` 文本 + 电池 icon，整体右对齐。"""
    text = f"{max(0, min(100, int(level)))}%"
    icon_w = int(30 * scale) + max(2, int(3 * scale)) + max(1, int(2 * scale))
    _draw_battery_indicator(draw, x_right, y_center, scale, level, fg)
    tx = x_right - icon_w - int(6 * scale) - _text_w(draw, text, font)
    draw.text((tx, y_center - font.size // 2), text, fill=fg, font=font)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        return int(draw.textlength(text, font=font))
    except Exception:
        return len(text) * (font.size // 2)


def _draw_list_header(draw: ImageDraw.ImageDraw, title: str, date_str: str,
                      count_str: str, w: int, scale: float,
                      font_title, font_meta, battery: Optional[int] = None, fg=0):
    """列表屏通用顶部状态栏：标题+日期靠左，右侧为计数与（可选）电量。"""
    title_x = int(14 * scale)
    title_y = int(10 * scale)
    draw.text((title_x, title_y), title, fill=fg, font=font_title)
    date_x = title_x + _text_w(draw, title, font_title) + int(12 * scale)
    draw.text((date_x, title_y + max(0, (font_title.size - font_meta.size) // 2)),
              date_str, fill=fg, font=font_meta)
    cy = title_y + font_title.size // 2
    right = w - int(14 * scale)
    if battery is not None:
        pct = f"{max(0, min(100, int(battery)))}%"
        icon_w = int(30 * scale) + max(2, int(3 * scale)) + max(1, int(2 * scale))
        block_w = icon_w + int(6 * scale) + _text_w(draw, pct, font_meta)
        _draw_battery_with_text(draw, right, cy, scale, battery, font_meta, fg)
        right -= block_w + int(12 * scale)
    if count_str:
        draw.text((right - _text_w(draw, count_str, font_meta),
                   title_y + max(0, (font_title.size - font_meta.size) // 2)),
                  count_str, fill=fg, font=font_meta)
    draw.line([(int(10 * scale), int(40 * scale)),
               (w - int(10 * scale), int(40 * scale))], fill=fg, width=max(1, int(2 * scale)))


def _fit_rows(n: int, top: int, bottom: int, scale: float,
              overflow_reserve: int = 0) -> tuple[int, int, int]:
    """返回 (可显示行数, 行高, 调整后 bottom)：放不下时预留溢出提示条再重算。"""
    avail = bottom - top
    row_h = max(int(26 * scale), min(int(38 * scale), avail // max(1, n)))
    fit = max(1, avail // row_h)
    if n > fit and overflow_reserve:
        bottom -= overflow_reserve
        avail = bottom - top
        row_h = max(int(26 * scale), min(int(38 * scale), avail // n))
        fit = max(1, avail // row_h)
    return fit, row_h, bottom


def _ellipsis_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    """单行截断：超宽时以 … 结尾。"""
    if _text_w(draw, text, font) <= max_w:
        return text
    out = text
    while out and _text_w(draw, out + "…", font) > max_w:
        out = out[:-1]
    return (out + "…") if out else "…"


def _draw_overflow_hint(draw: ImageDraw.ImageDraw, w: int, h: int, scale: float,
                        font, hidden: int, unit: str = "条", fg=0):
    if hidden <= 0:
        return
    text = f"…还有 {hidden} {unit}"
    draw.text((w - int(14 * scale) - _text_w(draw, text, font), h - int(16 * scale)),
              text, fill=fg, font=font)


def render_notes(mac: str, w: int, h: int, dual_config: Optional[dict],
                 battery: Optional[int] = None) -> Image.Image:
    """A 屏：便签（1bpp 黑白，单行一条）。battery 传 0-100 时右上角画电量 icon。"""
    scale = w / 400.0
    img = Image.new("1", (w, h), 1)  # 白底
    draw = ImageDraw.Draw(img)
    font_title = _load_cjk(max(16, int(22 * scale)))
    font_body = _load_cjk(max(13, int(16 * scale)))
    font_meta = _load_cjk(max(11, int(13 * scale)))

    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    content = (dual_config or {}).get("content") or {}
    notes = content.get("notes") or []
    # 生命周期：持续便签(persistent) 或 当天(date==today) 才展示；其余归入历史归档
    cleaned: list[tuple[str, str, str]] = []  # (text, sender, sender_type)
    for n in notes:
        if isinstance(n, dict):
            text = (n.get("text") or "").strip()
            if not text:
                continue
            date = n.get("date") or today_str  # 旧数据无日期按当天处理
            if not (bool(n.get("persistent")) or date == today_str):
                continue
            sender = n.get("sender") or "家长"
            st = n.get("sender_type") or ("teacher" if sender == "老师" else ("parent" if sender == "家长" else ""))
        elif isinstance(n, str):
            text = n.strip()
            if not text:
                continue
            sender = "家长"
            st = "parent"
        else:
            continue
        cleaned.append((text, sender, st))

    _draw_list_header(draw, "老师便签", f"{today.month}月{today.day}日",
                      f"共 {len(cleaned)} 条", w, scale, font_title, font_meta, battery)
    if not cleaned:
        draw.text((int(14 * scale), int(70 * scale)), "暂无通知", fill=0, font=font_body)
        return img

    top = int(52 * scale)
    bottom = int(h - 12 * scale)
    fit, row_h, _ = _fit_rows(len(cleaned), top, bottom, scale, int(28 * scale))
    y = top
    max_w = w - int(14 * scale) * 2 - int(24 * scale)
    for i, (text, sender, st) in enumerate(cleaned[:fit]):
        cy = y + max(2, (row_h - font_body.size) // 2)
        # 左侧色条（黑白屏为黑色实条，仍可区分条目优先级）
        draw.rectangle([int(14 * scale), y + int(7 * scale),
                        int(14 * scale) + max(2, int(4 * scale)),
                        y + row_h - int(7 * scale)], fill=0)
        # 家长是后台录入便签的默认来源，无需在每一行重复显示“长·”；
        # 仅保留老师来源标记，便于一眼区分老师推送的内容。
        prefix = "师·" if st == "teacher" else ""
        full = (prefix + text) if prefix else text
        draw.text((int(28 * scale), cy), _ellipsis_text(draw, full, font_body, max_w),
                  fill=0, font=font_body)
        y += row_h
        if i < fit - 1:
            draw.line([(int(10 * scale), y), (w - int(10 * scale), y)], fill=0, width=1)
    _draw_overflow_hint(draw, w, h, scale, font_meta, len(cleaned) - fit)
    return img


def _collect_items(raw, text_key: str) -> list[dict]:
    """归一化作业/阅读条目：dict 取字段，str 兼容为纯文本。"""
    out: list[dict] = []
    for it in raw or []:
        if isinstance(it, dict):
            text = (it.get(text_key) or "").strip()
            if not text:
                continue
            item = dict(it)
            item[text_key] = text
            out.append(item)
        elif isinstance(it, str) and it.strip():
            out.append({text_key: it.strip()})
    return out


def render_homework(mac: str, w: int, h: int, dual_config: Optional[dict],
                    battery: Optional[int] = None) -> Image.Image:
    """A 屏：今日作业（勾选框 + 单行任务，已完成实心勾选 + 划线）。"""
    scale = w / 400.0
    img = Image.new("1", (w, h), 1)
    draw = ImageDraw.Draw(img)
    font_title = _load_cjk(max(16, int(22 * scale)))
    font_body = _load_cjk(max(13, int(16 * scale)))
    font_meta = _load_cjk(max(11, int(13 * scale)))

    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    content = (dual_config or {}).get("content") or {}
    items = _collect_items(content.get("homework"), "text")
    # 生命周期：仅展示当天作业（覆盖/追加已在写入时落库，次日自然轮出到历史归档）
    cleaned: list[tuple[str, bool, str, str]] = []
    for it in items:
        if (it.get("date") or today_str) != today_str:  # 无日期旧数据按当天
            continue
        sender = it.get("sender") or "家长"
        st = it.get("sender_type") or ("teacher" if sender == "老师" else "")
        cleaned.append((it["text"], bool(it.get("done")), sender, st))

    done_n = sum(1 for _, d, _, _ in cleaned if d)
    _draw_list_header(draw, "今日作业", f"{today.month}月{today.day}日",
                      f"共 {len(cleaned)} 项", w, scale, font_title, font_meta, battery)
    if not cleaned:
        draw.text((int(14 * scale), int(70 * scale)), "暂无作业", fill=0, font=font_body)
        return img

    top = int(52 * scale)
    bottom = int(h - 12 * scale)
    fit, row_h, _ = _fit_rows(len(cleaned), top, bottom, scale, int(28 * scale))
    y = top
    box = max(8, int(13 * scale))
    max_w = w - int(14 * scale) * 2 - box - int(12 * scale)
    for i, (text, done, sender, st) in enumerate(cleaned[:fit]):
        cy = y + max(2, (row_h - font_body.size) // 2)
        by = y + (row_h - box) // 2
        # 勾选框：未完成空心方框，已完成实心
        draw.rectangle([int(14 * scale), by, int(14 * scale) + box, by + box],
                       outline=0, width=max(1, int(2 * scale)))
        if done:
            draw.rectangle([int(14 * scale) + max(1, int(2 * scale)),
                            by + max(1, int(2 * scale)),
                            int(14 * scale) + box - max(1, int(2 * scale)),
                            by + box - max(1, int(2 * scale))], fill=0)
        prefix = "师·" if st == "teacher" else ""
        full = (prefix + text) if prefix else text
        shown = _ellipsis_text(draw, full, font_body, max_w)
        draw.text((int(14 * scale) + box + int(12 * scale), cy), shown, fill=0, font=font_body)
        if done:  # 划线表示已完成
            tx = int(14 * scale) + box + int(12 * scale)
            ty = cy + font_body.size // 2
            draw.line([(tx, ty), (tx + _text_w(draw, shown, font_body), ty)],
                      fill=0, width=max(1, int(2 * scale)))
        y += row_h
        if i < fit - 1:
            draw.line([(int(10 * scale), y), (w - int(10 * scale), y)], fill=0, width=1)
    _draw_overflow_hint(draw, w, h, scale, font_meta, len(cleaned) - fit, "项")
    if len(cleaned) <= fit:  # 无溢出时底部显示完成进度
        draw.text((int(14 * scale), h - int(16 * scale)),
                  f"已完成 {done_n}/{len(cleaned)}", fill=0, font=font_meta)
    return img


def render_reading(mac: str, w: int, h: int, dual_config: Optional[dict],
                   battery: Optional[int] = None) -> Image.Image:
    """A 屏：阅读任务（书名/内容单行居左，进度居右）。"""
    scale = w / 400.0
    img = Image.new("1", (w, h), 1)
    draw = ImageDraw.Draw(img)
    font_title = _load_cjk(max(16, int(22 * scale)))
    font_body = _load_cjk(max(13, int(16 * scale)))
    font_meta = _load_cjk(max(11, int(13 * scale)))

    today = datetime.now()
    content = (dual_config or {}).get("content") or {}
    items = _collect_items(content.get("reading"), "title")
    cleaned = [(it["title"], (it.get("progress") or "").strip()) for it in items]

    _draw_list_header(draw, "阅读任务", f"{today.month}月{today.day}日",
                      f"共 {len(cleaned)} 项", w, scale, font_title, font_meta, battery)
    if not cleaned:
        draw.text((int(14 * scale), int(70 * scale)), "暂无阅读任务", fill=0, font=font_body)
        return img

    top = int(52 * scale)
    bottom = int(h - 12 * scale)
    fit, row_h, _ = _fit_rows(len(cleaned), top, bottom, scale, int(28 * scale))
    y = top
    for i, (title, progress) in enumerate(cleaned[:fit]):
        cy = y + max(2, (row_h - font_body.size) // 2)
        # 书名号强调（黑白屏仍清晰）
        shown_title = title if title.startswith(("《", "「", "\"")) else f"《{title}》"
        meta_w = _text_w(draw, progress, font_meta) + int(16 * scale) if progress else 0
        max_w = w - int(14 * scale) * 2 - meta_w - int(14 * scale)
        draw.text((int(14 * scale), cy), _ellipsis_text(draw, shown_title, font_body, max_w),
                  fill=0, font=font_body)
        if progress:
            draw.text((w - int(14 * scale) - _text_w(draw, progress, font_meta), cy),
                      progress, fill=0, font=font_meta)
        y += row_h
        if i < fit - 1:
            draw.line([(int(10 * scale), y), (w - int(10 * scale), y)], fill=0, width=1)
    _draw_overflow_hint(draw, w, h, scale, font_meta, len(cleaned) - fit, "项")
    return img


def _collect_today_courses(dual_config: Optional[dict], today: datetime) -> tuple[list[dict], int]:
    """收集「今天」要上的课（含正在上/下一节的 current_idx），供课程表与周末兜底共用。"""
    content = (dual_config or {}).get("content") or {}
    ct = content.get("courseTable") or {}
    periods = ct.get("periods") or []
    grid = ct.get("grid") or []
    today_idx = today.weekday()  # 周一=0，与 console grid 下标一致
    rows: list[dict] = []
    now_min = today.hour * 60 + today.minute
    current_idx = -1  # 正在上 / 下一节要上的课（用于高亮）
    for p, per in enumerate(periods):
        cell = None
        if isinstance(grid, list) and today_idx < len(grid) and p < len(grid[today_idx]):
            cell = grid[today_idx][p]
        if isinstance(cell, dict) and cell.get("name"):
            sh = int(per.get("start_hour", 0))
            sm = int(per.get("start_minute", 0))
            eh = int(per.get("end_hour", sh))
            em = int(per.get("end_minute", sm + 40))
            start = sh * 60 + sm
            end = eh * 60 + em
            rows.append({
                "idx": p,
                "time": f"{sh:02d}:{sm:02d}",
                "start": start,
                "end": end,
                "name": cell.get("name", ""),
                "teacher": cell.get("teacher") or "",
                "room": cell.get("room") or "",
            })
            if start <= now_min < end and current_idx < 0:
                current_idx = len(rows) - 1
    if current_idx < 0:
        for i, r in enumerate(rows):
            if r["start"] > now_min:
                current_idx = i
                break
    return rows, current_idx


def render_course(mac: str, w: int, h: int, colors: int,
                  dual_config: Optional[dict]) -> Image.Image:
    """B 屏：今日课程表。colors=2 → 纯黑白 1bpp；colors=3 → 三色(BWR)；colors>=4 → 四色(BWRY)。"""
    scale = w / 400.0
    is_color = colors >= 3
    if is_color:
        img = _palette_p_image(w, h, colors)
    else:
        img = Image.new("1", (w, h), 1)
    draw = ImageDraw.Draw(img)
    fg = 0  # 黑（P 调色板 0 / 1bit 0）

    font_title = _load_cjk(max(16, int(24 * scale)))
    font_body = _load_cjk(max(12, int(16 * scale)))
    font_meta = _load_cjk(max(10, int(12 * scale)))
    font_period = _load_cjk(max(11, int(14 * scale)))

    content = (dual_config or {}).get("content") or {}
    ct = content.get("courseTable") or {}
    enabled = ct.get("enabled", True)
    school = (ct.get("school") or "").strip()
    class_name = (ct.get("class_name") or "").strip()

    today = datetime.now()
    today_idx = today.weekday()  # 周一=0，与 console grid 下标一致
    date_str = f"{today.month}月{today.day}日 {_WEEKDAY_CN[today_idx]}"

    title = class_name or school or "课程表"

    # 标题栏
    draw.text((int(14 * scale), int(10 * scale)), title, fill=fg, font=font_title)
    draw.text((w - int(14 * scale) - _text_w(draw, date_str, font_meta), int(16 * scale)),
              date_str, fill=fg, font=font_meta)
    draw.line([(int(10 * scale), int(44 * scale)),
               (w - int(10 * scale), int(44 * scale))], fill=fg, width=max(1, int(2 * scale)))

    if not enabled:
        draw.text((int(14 * scale), int(70 * scale)), "课程表未启用", fill=fg, font=font_body)
        return img

    rows, current_idx = _collect_today_courses(dual_config, today)

    top = int(54 * scale)
    bottom = int(h - 12 * scale)
    if not rows:
        draw.text((int(14 * scale), top + int(10 * scale)), "今日无课", fill=fg, font=font_body)
        return img

    avail_h = bottom - top
    # 单行布局：节次/时间 | 课名 | 老师@教室（右对齐），行高可压缩到 26px 依然清晰
    row_h = max(int(26 * scale), min(int(40 * scale), avail_h // len(rows)))
    fit = max(1, avail_h // row_h)
    # 溢出时给底部「还有 N 节」指示条留出空间，重算可显示行数
    if len(rows) > fit:
        bottom = int(h - 26 * scale)
        avail_h = bottom - top
        row_h = max(int(26 * scale), min(int(40 * scale), avail_h // len(rows)))
        fit = max(1, avail_h // row_h)
    y = top
    for i, r in enumerate(rows[:fit]):
        hl = is_color and i == current_idx
        if hl:
            draw.rectangle([int(10 * scale), y, w - int(10 * scale), y + row_h - 2],
                           fill=2)  # 强调色底（四色=黄 / 三色=红）
        # 高亮行文字色：四色屏红字(3)压黄底，三色屏白字(1)压红底；其余黑字(0)
        col = 3 if (hl and colors >= 4) else (1 if hl else fg)
        cy = y + max(2, (row_h - font_body.size) // 2)
        draw.text((int(14 * scale), cy), f"{r['idx'] + 1}", fill=col, font=font_period)
        draw.text((int(42 * scale), cy), r["time"], fill=col, font=font_meta)
        draw.text((int(104 * scale), cy), r["name"], fill=col, font=font_body)
        meta = ""
        if r["teacher"]:
            meta += f"{r['teacher']}"
        if r["room"]:
            meta += f" · {r['room']}" if r["teacher"] else f"{r['room']}"
        if meta:
            draw.text((w - int(14 * scale) - _text_w(draw, meta, font_meta), cy),
                      meta, fill=col, font=font_meta)
        y += row_h
        draw.line([(int(10 * scale), y), (w - int(10 * scale), y)],
                  fill=fg, width=1)
    if len(rows) > fit:
        draw.text((w - int(14 * scale) - _text_w(draw, f"…还有 {len(rows) - fit} 节", font_meta),
                  h - int(14 * scale)), f"…还有 {len(rows) - fit} 节",
                  fill=fg, font=font_meta)

    return img


def render_scene_modules(title: str, modules: list, w: int, h: int, colors: int,
                         footer: str = "") -> Image.Image:
    """Render the selected scene's own module list on either panel."""
    scale = w / 400.0
    img = _palette_p_image(w, h, colors) if colors >= 3 else Image.new("1", (w, h), 1)
    draw = ImageDraw.Draw(img)
    fg = 0
    font_title = _load_cjk(max(16, int(22 * scale)))
    font_body = _load_cjk(max(13, int(16 * scale)))
    font_meta = _load_cjk(max(10, int(12 * scale)))
    draw.text((int(14 * scale), int(10 * scale)), title[:24], fill=fg, font=font_title)
    draw.text((w - int(14 * scale) - _text_w(draw, datetime.now().strftime("%m月%d日"), font_meta), int(16 * scale)),
              datetime.now().strftime("%m月%d日"), fill=fg, font=font_meta)
    draw.line([(int(10 * scale), int(40 * scale)), (w - int(10 * scale), int(40 * scale))], fill=fg, width=max(1, int(2 * scale)))
    rows = [str(item).strip() for item in modules if str(item).strip()][:6]
    top = int(52 * scale)
    row_h = max(int(26 * scale), min(int(38 * scale), (h - top - int(26 * scale)) // max(1, len(rows))))
    for index, row in enumerate(rows):
        y = top + index * row_h
        draw.text((int(14 * scale), y + max(1, (row_h - font_body.size) // 2)), row[:28], fill=fg, font=font_body)
        if index < len(rows) - 1:
            draw.line([(int(10 * scale), y + row_h), (w - int(10 * scale), y + row_h)], fill=fg, width=1)
    if footer:
        draw.text((int(14 * scale), h - int(18 * scale)), footer[:36], fill=fg, font=font_meta)
    return img


def render_family_express_bills(express: list[dict], bills: list[dict], w: int, h: int,
                                colors: int) -> Image.Image:
    """家庭快递与缴费并排显示，符合 A 屏快速扫读场景。"""
    scale = w / 400.0
    img = _palette_p_image(w, h, colors) if colors >= 3 else Image.new("1", (w, h), 1)
    draw = ImageDraw.Draw(img)
    fg = 0
    title_font = _load_cjk(max(16, int(22 * scale)))
    head_font = _load_cjk(max(13, int(16 * scale)))
    body_font = _load_cjk(max(10, int(13 * scale)))
    meta_font = _load_cjk(max(9, int(11 * scale)))
    pad = int(14 * scale)
    mid = w // 2
    draw.text((pad, int(10 * scale)), "家庭快递与缴费", fill=fg, font=title_font)
    draw.text((w - pad - _text_w(draw, datetime.now().strftime("%m月%d日"), meta_font), int(16 * scale)),
              datetime.now().strftime("%m月%d日"), fill=fg, font=meta_font)
    draw.line([(pad, int(42 * scale)), (w - pad, int(42 * scale))], fill=fg, width=max(1, int(2 * scale)))
    gutter = int(10 * scale)
    col_w = (w - 2 * pad - gutter) // 2
    x_left, x_right = pad, pad + col_w + gutter
    top = int(52 * scale)
    draw.text((x_left, top), f"快递/取件码 {len(express)}", fill=fg, font=head_font)
    draw.text((x_right, top), f"缴费台账 {len(bills)}", fill=fg, font=head_font)
    draw.line([(x_left, top + int(24 * scale)), (x_left + col_w, top + int(24 * scale))], fill=fg, width=1)
    draw.line([(x_right, top + int(24 * scale)), (x_right + col_w, top + int(24 * scale))], fill=fg, width=1)
    row_top = top + int(32 * scale)
    row_h = max(int(28 * scale), (h - row_top - int(26 * scale)) // 4)
    left_rows = [f"{x.get('pickupCode') or x.get('trackingNo') or '待补取件码'} {x.get('pickupPoint') or x.get('item') or ''}".strip() for x in express[:4]] or ["暂无待取包裹"]
    right_rows = [f"{x.get('name') or x.get('category') or '账单'} ¥{x.get('amount', 0)}" for x in bills[:4]] or ["暂无待缴项目"]
    max_chars = max(5, int(col_w / max(1, body_font.size * 0.95)))
    for idx in range(4):
        y = row_top + idx * row_h
        for x, rows in ((x_left, left_rows), (x_right, right_rows)):
            if idx < len(rows):
                text = str(rows[idx])
                if len(text) > max_chars:
                    text = text[:max(1, max_chars - 1)] + "…"
                draw.text((x, y), text, fill=fg, font=body_font)
            if idx < 3:
                draw.line([(x, y + row_h - int(5 * scale)), (x + col_w, y + row_h - int(5 * scale))], fill=fg, width=1)
    draw.line([(mid, top), (mid, h - int(22 * scale))], fill=fg, width=1)
    draw.text((pad, h - int(18 * scale)), "取件码手动登记 · 待缴账单提醒", fill=fg, font=meta_font)
    return img


def render_dual_screen(mac: str, screen: str, w: int, h: int, colors: int,
                       dual_config: Optional[dict],
                       battery: Optional[int] = None) -> Image.Image:
    """统一入口：screen='A' 按场景配置渲染便签/作业/阅读/相册，screen='B' 渲染课程表/相册。

    无数据兜底：任一内容模式无数据（如周末无课、便签为空）且相册有照片时，
    自动切换为相册，让墨水屏始终有内容可看。
    """
    try:
        if screen == "A":
            # A 屏内容模式由控制台写入 config[sceneKey].a.mode（notes/homework/reading/album）
            cfg_root = dual_config or {}
            scene_key = str(cfg_root.get("sceneKey") or "study")
            scene_cfg = (cfg_root.get("config") or {}).get(scene_key) or {}
            a_cfg = scene_cfg.get("a") or {}
            mode = str(a_cfg.get("mode") or ("health" if scene_key == "health" else ("family" if scene_key == "family" else "notes")))
            photos = _album_photos(dual_config)
            if scene_key == "family" and mode.startswith("family"):
                content = cfg_root.get("content") or {}
                modules = a_cfg.get("modules") or []
                express = [x for x in content.get("express", []) if isinstance(x, dict) and x.get("status") not in ("signed", "exception")]
                bills = [x for x in content.get("bills", []) if isinstance(x, dict) and not x.get("paid")]
                schedule = [x for x in content.get("familySchedule", []) if isinstance(x, dict)]
                schedule.sort(key=lambda x: (str(x.get("date") or "9999"), str(x.get("time") or "")))
                if mode == "family-express-bills":
                    return render_family_express_bills(express, bills, w, h, colors)
                if mode == "family-weather":
                    weather = cfg_root.get("_runtime_weather") or {}
                    from .json_renderer import render_json_mode
                    from .mode_registry import get_registry

                    weather_mode = get_registry().get_json_mode("WEATHER", mac or None, language="zh")
                    if weather_mode:
                        weather_content = dict(weather)
                        current_temp = str(weather_content.get("weather_str") or "").replace("°C", "").replace("℃", "")
                        if current_temp:
                            weather_content["today_temp"] = current_temp
                        weather_content["today_desc"] = weather_content.get("description") or weather_content.get("today_desc") or "天气状况未知"
                        weather_content["today_wind_dir"] = weather_content.get("wind_dir") or weather_content.get("today_wind_dir") or ""
                        weather_content["today_wind_level"] = weather_content.get("wind_level") or weather_content.get("today_wind_level") or ""
                        return render_json_mode(
                            weather_mode.definition,
                            weather_content,
                            date_str=datetime.now().strftime("%Y-%m-%d"),
                            weather_str="",
                            battery_pct=float(battery or 0),
                            weather_code=int(weather_content.get("today_code") or -1),
                            time_str=datetime.now().strftime("%H:%M"),
                            screen_w=w,
                            screen_h=h,
                            colors=colors,
                            language="zh",
                        )
                    rows = [f"{weather.get('city') or '本地'} · {weather.get('weather_str', '--')}", str(weather.get('description') or '当前天气')]
                    footer = str(weather.get("advice") or "实时天气")
                elif mode == "family-todos":
                    rows = [str(x.get('text') or '').strip() for x in content.get('homework', []) if isinstance(x, dict) and not x.get('done')]
                    rows += [str(x.get('text') or '').strip() for x in content.get('notes', []) if isinstance(x, dict) and str(x.get('text') or '').strip()]
                    rows = rows[:6] or ["暂无家庭待办"]
                    footer = f"待办 {len(rows)} 项"
                elif mode == "family-shopping":
                    rows = [str(x.get('text') or '') for x in content.get('familyShopping', []) if isinstance(x, dict) and not x.get('done')][:6]
                    rows = rows or ["采购清单为空"]
                    footer = ""
                elif mode == "family-anniversary":
                    events = [x for x in content.get('familyAnniversaries', []) if isinstance(x, dict)]
                    events.sort(key=lambda x: str(x.get('date') or '9999'))
                    rows = [f"{x.get('date', '')} {x.get('title', '')}" for x in events[:6]] or ["暂无纪念日"]
                    footer = f"共 {len(events)} 个纪念日"
                else:
                    if mode == "family":
                        weather = cfg_root.get("_runtime_weather") or {}
                        todos = [x.get("text", "") for x in content.get("homework", []) if isinstance(x, dict) and not x.get("done")]
                        shopping = [x.get("text", "") for x in content.get("familyShopping", []) if isinstance(x, dict) and not x.get("done")]
                        anniversaries = [f"{x.get('date', '')} {x.get('title', '')}" for x in content.get("familyAnniversaries", []) if isinstance(x, dict)]
                        rows = [f"天气 {weather.get('weather_str', '--')}" ]
                        rows += [f"取件码 {x.get('pickupCode') or x.get('trackingNo') or '待补'} · {x.get('pickupPoint') or x.get('item') or '包裹'}" for x in express[:2]]
                        rows += [f"待缴 {x.get('name') or x.get('category') or '账单'} · ¥{x.get('amount', 0)}" for x in bills[:1]]
                        rows += [f"待办 {x}" for x in todos[:2]]
                        rows += [f"日程 {x.get('date', '')} {x.get('time', '')} {x.get('title', '')}{' · ' + str(x.get('member')) if x.get('member') else ''}".strip() for x in schedule[:2]]
                        rows += [f"采购 {x}" for x in shopping[:1]]
                        rows += [f"纪念日 {x}" for x in anniversaries[:1]]
                        rows = rows[:6]
                        footer = f"家庭数据总览 · 日程 {len(schedule)} 项"
                    else:
                        rows = list(modules)
                        weather = cfg_root.get("_runtime_weather") or {}
                        rows = [f"天气 {weather.get('weather_str', '--')}" if str(x) == "天气" else str(x) for x in rows]
                        rows = [f"{x} · {len(express)} 件 / {len(bills)} 项" if x == "快递与缴费" else x for x in rows]
                        footer = "家庭共享提醒"
                return render_scene_modules("家庭提醒" if mode == "family" else (a_cfg.get("title") or "家庭提醒"), rows, w, h, colors, footer)
            if scene_key in ("family", "health", "finance", "office", "custom") and mode not in ("notes", "homework", "reading", "album"):
                modules = a_cfg.get("modules") or []
                footer = ""
                if scene_key == "family":
                    content = cfg_root.get("content") or {}
                    express = [x for x in content.get("express", []) if isinstance(x, dict) and x.get("status") not in ("signed", "exception")]
                    bills = [x for x in content.get("bills", []) if isinstance(x, dict) and not x.get("paid")]
                    footer = f"未签收快递 {len(express)} · 待缴费 {len(bills)}"
                elif scene_key == "finance":
                    footer = "行情数据源未配置"
                elif scene_key == "health":
                    footer = "请按个人情况安排健康计划"
                return render_scene_modules(a_cfg.get("title") or "家庭提醒", modules, w, h, colors, footer)
            # 所选模式无数据且相册有照片 → 相册兜底（album 自身为空时则由其占位图处理）
            if mode != "album" and not _a_mode_has_data(mode, dual_config) and photos:
                return render_album(mac, w, h, colors, dual_config)
            if mode == "homework":
                return render_homework(mac, w, h, dual_config, battery=battery)
            if mode == "reading":
                return render_reading(mac, w, h, dual_config, battery=battery)
            if mode == "album":
                return render_album(mac, w, h, colors, dual_config)
            return render_notes(mac, w, h, dual_config, battery=battery)
        # B 屏按当前场景与该场景自己的模式渲染。
        cfg_root = dual_config or {}
        scene_key = str(cfg_root.get("sceneKey") or "study")
        scene_cfg = (cfg_root.get("config") or {}).get(scene_key) or {}
        b_cfg = scene_cfg.get("b") or {}
        b_mode = str(b_cfg.get("mode") or ("course" if scene_key == "study" else "family" if scene_key == "family" else "health-plan" if scene_key == "health" else "finance-trend" if scene_key == "finance" else "office"))
        if b_mode == "album":
            return render_album(mac, w, h, colors, dual_config)
        if scene_key == "family" and b_mode in ("calendar", "family"):
            content = cfg_root.get("content") or {}
            events = [x for x in content.get("familySchedule", []) if isinstance(x, dict)]
            events.sort(key=lambda x: (str(x.get('date') or '9999'), str(x.get('time') or '')))
            rows = [f"{x.get('date', '')} {x.get('time', '')} {x.get('title', '')}{' · ' + str(x.get('member')) if x.get('member') else ''}".strip() for x in events[:6]]
            if b_mode == "family":
                rows = rows[:3] + [str(x) for x in (b_cfg.get("modules") or []) if str(x).strip()][:3]
            return render_scene_modules("家庭共享看板" if b_mode == "family" else "家庭日程", rows or ["暂无家庭日程"], w, h, colors, f"共 {len(events)} 项安排")
        if scene_key != "study" or b_mode != "course":
            footer = ""
            if scene_key == "finance":
                footer = "行情数据源未配置"
            elif scene_key == "health":
                footer = "计划仅供日常提醒"
            modules = b_cfg.get("modules") or []
            return render_scene_modules(b_cfg.get("title") or "场景看板", modules, w, h, colors, footer)
        # 学习 B 屏：课程表今日无课且相册有照片 → 相册兜底
        rows, _ = _collect_today_courses(dual_config, datetime.now())
        if not rows and _album_photos(dual_config):
            return render_album(mac, w, h, colors, dual_config)
        return render_course(mac, w, h, colors, dual_config)
    except Exception as exc:  # 渲染异常绝不能让设备卡死，回退到占位图
        logger.error("[DUAL-RENDER] screen=%s failed: %s", screen, exc, exc_info=True)
        scale = w / 400.0
        if colors >= 3:
            img = _palette_p_image(w, h, colors)
        else:
            img = Image.new("1", (w, h), 1)
        draw = ImageDraw.Draw(img)
        draw.text((int(14 * scale), int(70 * scale)),
                  "课程表/便签渲染失败", fill=0, font=_load_cjk(max(13, int(16 * scale))))
        return img


def _wrap_pixels(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """按像素宽度做中文友好的换行。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        if ch in ("\n", "\r"):
            lines.append(cur)
            cur = ""
            continue
        test = cur + ch
        if _text_w(draw, test, font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines
