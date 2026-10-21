import base64
import io
import json
import re
import time
from datetime import datetime
from json import JSONDecodeError
from typing import Annotated, Optional

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from PIL import Image, UnidentifiedImageError

from api.shared import (
    _preview_push_queue,
    _preview_push_queue_lock,
    _render_device_unbound_image,
    _render_device_unregistered_image,
    build_image,
    content_cache,
    ensure_web_or_device_access,
    limiter,
    log_render_stats,
    logger,
    reconnect_threshold_seconds,
    resolve_preview_voltage,
    resolve_refresh_minutes_for_device_state,
)
from core.activity_store import log_user_activity
from core.auth import require_device_token, validate_mac_param
from core.config import DEFAULT_CITY, DEFAULT_REFRESH_INTERVAL, SCREEN_HEIGHT, SCREEN_WIDTH
from core.context import _weather_code_to_desc, get_qweather_current_cached, get_weather_forecast
from core.config_store import (
    consume_pending_refresh,
    get_active_config,
    get_device_owner,
    get_device_state,
    get_dual_config,
    get_or_create_claim_token,
    update_device_state,
)
from core.context import extract_location_settings, get_date_context, get_weather
from core.pipeline import get_effective_mode_config
from core.pipeline import generate_and_render
from core.renderer import (
    image_to_bmp_bytes,
    image_to_png_bytes,
    image_to_raw_2bpp,
    image_to_raw_4bpp,
    render_error,
)


def _pack_raw(img: "Image.Image", colors: int, bpp: int) -> bytes:
    """按设备能力打包原始帧：六色面板 + bpp=4 走 4bpp，其余沿用 2bpp。"""
    if bpp >= 4 and colors >= 6:
        return image_to_raw_4bpp(img)
    return image_to_raw_2bpp(img)
from core.schemas import RenderQuery
from core.stats_store import get_latest_heartbeat

router = APIRouter(tags=["render"])


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _configured_refresh_minutes(config: Optional[dict]) -> int:
    refresh_minutes_raw = config.get("refresh_interval") if config else DEFAULT_REFRESH_INTERVAL
    try:
        refresh_minutes = int(refresh_minutes_raw)
    except (TypeError, ValueError):
        refresh_minutes = DEFAULT_REFRESH_INTERVAL
    if refresh_minutes < 10:
        return 10
    if refresh_minutes > 1440:
        return 1440
    return refresh_minutes


def _dual_refresh_minutes(dual_config: Optional[dict], screen: str, fallback: int) -> int:
    """Convert the dual console's display value (such as ``每 15 分钟``)."""
    if not isinstance(dual_config, dict):
        return fallback
    scene_key = dual_config.get("sceneKey")
    scenes = dual_config.get("config")
    if not isinstance(scene_key, str) or not isinstance(scenes, dict):
        return fallback
    scene = scenes.get(scene_key)
    screen_cfg = scene.get(screen.lower()) if isinstance(scene, dict) else None
    value = screen_cfg.get("refresh") if isinstance(screen_cfg, dict) else None
    if not isinstance(value, str):
        return fallback
    match = re.fullmatch(r"\s*每\s*(\d+)\s*(分钟|小时)\s*", value)
    if not match:
        return fallback
    minutes = int(match.group(1)) * (60 if match.group(2) == "小时" else 1)
    return max(1, min(minutes, 1440))


@router.get("/render")
@limiter.limit("10/minute")
async def render(
    request: Request,
    params: Annotated[RenderQuery, Depends()],
    x_device_token: Optional[str] = Header(default=None),
):
    mac = params.mac
    cfg: Optional[dict] = None
    configured_refresh_minutes: Optional[int] = None
    owner = None
    if mac:
        mac = validate_mac_param(mac)
        await require_device_token(mac, x_device_token)
        cfg = await get_active_config(mac, log_load=False)
        configured_refresh_minutes = _configured_refresh_minutes(cfg)
        owner = await get_device_owner(mac)
        logger.info("[RENDER] mac=%s  cfg_exists=%s  owner_exists=%s  cfg=%s",
                    mac, cfg is not None, owner is not None,
                    list(cfg.keys())[:6] if cfg else None)
        # 能发出 /api/render 就说明设备此刻活着 —— 记一条心跳，让「设备配置」页的
        # 在线状态反映真实情况。很多固件只拉图、从不 POST /heartbeat，
        # 以前这类设备在网页上会永远显示「离线」。
        try:
            from core.stats_store import log_heartbeat

            await log_heartbeat(mac, params.v or 3.3, params.rssi or None)
        except Exception:  # 心跳失败绝不能影响出图
            logger.warning("[RENDER] Failed to log heartbeat for mac=%s", mac, exc_info=True)
    else:
        # /api/render 是设备取图接口：没有 MAC 就无法判定这台设备属于谁，
        # 更不该拿平台 Key 去给它生成内容。以前这里会走「无主预览」，平台
        # .env 没配 Key 时设备刷出来的是一张「API key 无效或已过期」——
        # 和真实原因（固件没上报 MAC / 设备未注册）毫无关系，纯属误导。
        logger.warning(
            "[RENDER] /api/render 未携带 mac（w=%s h=%s colors=%s bpp=%s）；"
            "返回重新配网引导图，不生成内容",
            params.w,
            params.h,
            params.colors,
            params.bpp,
        )
        guide = _render_device_unregistered_image(params.w, params.h)
        if params.colors >= 3:
            guide_bytes = _pack_raw(guide, params.colors, params.bpp)
            guide_media = "application/octet-stream"
        else:
            guide_bytes = image_to_bmp_bytes(guide)
            guide_media = "image/bmp"
        return Response(
            content=guide_bytes,
            media_type=guide_media,
            headers={"X-Device-Unregistered": "no-mac"},
        )

    start_time = time.time()
    force_next = params.next_mode == 1

    try:
        if mac and owner is None:
            claim = await get_or_create_claim_token(mac, source="render")
            img = _render_device_unbound_image(
                params.w, params.h, claim.get("pair_code", ""), mac=mac
            )
            # 彩色设备也要拿到配对码：以前这里恒返回 BMP，六色固件（Load4bpp 只认
            # 定长 raw 帧）会判为「长度不足」再回落一次 2bpp 请求，白耗一个 45s
            # 超时周期；极端情况下这一来一回就停在旧画面上，看上去像"没出配对码"。
            if params.colors >= 3:
                unbound_bytes = _pack_raw(img, params.colors, params.bpp)
                unbound_media = "application/octet-stream"
            else:
                unbound_bytes = image_to_bmp_bytes(img)
                unbound_media = "image/bmp"
            headers: dict[str, str] = {"X-Device-Unbound": "1"}
            if configured_refresh_minutes is not None:
                headers["X-Refresh-Minutes"] = str(configured_refresh_minutes)
            if await consume_pending_refresh(mac):
                headers["X-Pending-Refresh"] = "1"
            return Response(content=unbound_bytes, media_type=unbound_media, headers=headers)

        if mac:
            if cfg is None:
                logger.warning(
                    "[RENDER] Device %s is bound but has NO active config; serving fallback image. "
                    "Open the device in web admin and SAVE CONFIG (check configs table MAC if this persists).",
                    mac,
                )
            async with _preview_push_queue_lock:
                pushed_payload = _preview_push_queue.pop(mac, None)
            logger.info("[RENDER] Checked queue for mac=%s: found=%s, mode=%s", mac, pushed_payload is not None, pushed_payload.get("mode") if pushed_payload else None)
            if pushed_payload and pushed_payload.get("image"):
                try:
                    with Image.open(io.BytesIO(pushed_payload["image"])) as pushed_img:
                        if params.colors >= 3 and pushed_img.mode == "P":
                            img = pushed_img.copy()
                        else:
                            img = pushed_img.convert("1")
                        if img.size != (params.w, params.h):
                            img = img.resize((params.w, params.h), Image.NEAREST)
                    if params.colors >= 3 and img.mode == "P":
                        out_bytes = _pack_raw(img, params.colors, params.bpp)
                        out_media = "application/octet-stream"
                    else:
                        out_bytes = image_to_bmp_bytes(img)
                        out_media = "image/bmp"
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    resolved_persona = pushed_payload.get("mode") or params.persona or "PUSH_PREVIEW"
                    await log_render_stats(
                        mac,
                        resolved_persona,
                        False,
                        elapsed_ms,
                        voltage=params.v,
                        rssi=params.rssi,
                    )
                    if params.refresh_min is not None:
                        await update_device_state(mac, expected_refresh_min=params.refresh_min)
                    # Clear pending_mode after delivering the pushed preview, so the device
                    # returns to normal polling instead of re-requesting the same mode every cycle.
                    await update_device_state(mac, pending_mode=None)
                    headers = {"X-Preview-Push": "1"}
                    if configured_refresh_minutes is not None:
                        headers["X-Refresh-Minutes"] = str(configured_refresh_minutes)
                    if await consume_pending_refresh(mac):
                        headers["X-Pending-Refresh"] = "1"
                    return Response(content=out_bytes, media_type=out_media, headers=headers)
                except (OSError, TypeError, UnidentifiedImageError, ValueError) as exc:
                    logger.warning("[RENDER] Failed to deliver pushed preview for %s: %s", mac, exc)

        skip_cache_for_this_render = False

        # 双屏模式：DUAL_A=便签（A 屏 1bpp BMP），DUAL_B=今日课程表（B 屏彩色 2bpp）。
        # 后端读 device_state.dual_config 直接出图，固件只拉图 + 刷屏，无需 MCU 排版。
        if mac and params.persona in ("DUAL_A", "DUAL_B"):
            from core.dual_render import render_dual_screen

            dual_cfg = await get_dual_config(mac)
            screen = "B" if params.persona == "DUAL_B" else "A"
            if dual_cfg and str(dual_cfg.get("sceneKey") or "") == "family":
                try:
                    dual_city = str(((dual_cfg or {}).get("content") or {}).get("weatherCity") or "").strip()
                    if dual_city:
                        location_args = extract_location_settings({"city": dual_city}, fallback_city=DEFAULT_CITY)
                    else:
                        weather_cfg = get_effective_mode_config(cfg or {}, "WEATHER")
                        location_args = extract_location_settings(weather_cfg, fallback_city=DEFAULT_CITY)
                    current_weather = await get_qweather_current_cached(**location_args)
                    forecast_weather = await get_weather_forecast(
                        city=location_args.get("city") or DEFAULT_CITY, days=3,
                        lat=location_args.get("lat"), lon=location_args.get("lon"), language="zh",
                    )
                    if not current_weather.get("description"):
                        current_weather["description"] = _weather_code_to_desc(current_weather.get("weather_code", -1))
                    dual_cfg["_runtime_weather"] = {
                        **forecast_weather,
                        "weather_str": current_weather.get("weather_str", "--°C"),
                        "description": current_weather.get("description") or forecast_weather.get("today_desc"),
                        "humidity": current_weather.get("humidity") or forecast_weather.get("today_humidity"),
                        "feels_like": current_weather.get("feels_like"),
                        "wind_dir": current_weather.get("wind_dir") or forecast_weather.get("today_wind_dir"),
                        "wind_level": forecast_weather.get("today_wind_level"),
                    }
                except Exception:
                    logger.warning("[DUAL-RENDER] family weather unavailable", exc_info=True)
            # 设备端点：尺寸恒用固件请求的原生 w/h（必须与硬件帧一致）；
            # 色数取后台 screen.colors，但夹紧到固件请求的面板色数上限，避免超硬件。
            screen_cfg = (dual_cfg or {}).get("screen") or {}
            cfg_colors = screen_cfg.get("colors")
            eff_colors = cfg_colors if isinstance(cfg_colors, int) and cfg_colors in (2, 3, 4) else (params.colors or 4)
            eff_colors = min(eff_colors, params.colors or 4)
            img = render_dual_screen(mac, screen, params.w, params.h,
                                     eff_colors, dual_cfg,
                                     battery=params.battery if screen == "A" else None)
            if screen == "B":
                out_bytes = _pack_raw(img, eff_colors, 2)
                out_media = "application/octet-stream"
            else:
                out_bytes = image_to_bmp_bytes(img)
                out_media = "image/bmp"
            elapsed_ms = int((time.time() - start_time) * 1000)
            if mac:
                await log_render_stats(mac, params.persona, True, elapsed_ms,
                                       voltage=params.v, rssi=params.rssi)
            dual_refresh_minutes = _dual_refresh_minutes(
                dual_cfg, screen, configured_refresh_minutes or DEFAULT_REFRESH_INTERVAL
            )
            headers = {
                "X-Render-Time-Ms": str(elapsed_ms),
                "X-Mode-Id": params.persona,
                "X-Refresh-Minutes": str(dual_refresh_minutes),
            }
            if mac and await consume_pending_refresh(mac):
                headers["X-Pending-Refresh"] = "1"
            return Response(content=out_bytes, media_type=out_media, headers=headers)

        if mac:
            if cfg:
                state = await get_device_state(mac)
                refresh_minutes = resolve_refresh_minutes_for_device_state(cfg, state)
                latest_heartbeat = await get_latest_heartbeat(mac)
                if latest_heartbeat and latest_heartbeat.get("created_at"):
                    try:
                        now_dt = datetime.now()
                        delta_seconds = (
                            now_dt - datetime.fromisoformat(latest_heartbeat["created_at"])
                        ).total_seconds()
                        threshold_seconds = reconnect_threshold_seconds(refresh_minutes)
                        last_regen_raw = state.get("last_reconnect_regen_at", "") if state else ""
                        regen_cooldown_ok = True
                        if isinstance(last_regen_raw, str) and last_regen_raw:
                            since_last_regen = (
                                now_dt - datetime.fromisoformat(last_regen_raw)
                            ).total_seconds()
                            regen_cooldown_ok = since_last_regen > threshold_seconds
                        if delta_seconds > threshold_seconds and regen_cooldown_ok:
                            # Reconnect detected → synchronously pre-warm cache BEFORE
                            # returning a response. This ensures the device gets a
                            # cache hit on this very request (no regeneration delay).
                            await update_device_state(
                                mac, last_reconnect_regen_at=now_dt.isoformat()
                            )
                            await content_cache.force_regenerate_all(
                                mac, cfg, params.v, params.w, params.h,
                                colors=params.colors,
                            )
                            # Do NOT set skip_cache=True here: after force_regenerate_all
                            # the cache is warm, so build_image should hit it immediately.
                    except (TypeError, ValueError, OSError):
                        logger.warning("[RECONNECT] Failed to evaluate reconnect policy for %s", mac, exc_info=True)

        img, resolved_persona, cache_hit, content_fallback, quota_exhausted, api_key_invalid, llm_mode_requires_quota, _usage_source = await build_image(
            params.v,
            mac,
            params.persona,
            screen_w=params.w,
            screen_h=params.h,
            force_next=force_next,
            skip_cache=skip_cache_for_this_render,
            colors=params.colors,
        )

        if img.size != (params.w, params.h):
            logger.warning(
                "[RENDER] Image size mismatch for %s:%s: got %sx%s, expected %sx%s. Resizing.",
                mac,
                resolved_persona,
                img.size[0],
                img.size[1],
                params.w,
                params.h,
            )
            img = img.resize((params.w, params.h), Image.NEAREST)

        if params.colors >= 3:
            out_bytes = _pack_raw(img, params.colors, params.bpp)
            out_media = "application/octet-stream"
        else:
            out_bytes = image_to_bmp_bytes(img)
            out_media = "image/bmp"
        elapsed_ms = int((time.time() - start_time) * 1000)
        if mac:
            await log_render_stats(
                mac,
                resolved_persona,
                cache_hit,
                elapsed_ms,
                voltage=params.v,
                rssi=params.rssi,
                is_fallback=content_fallback,
            )
            if params.refresh_min is not None:
                await update_device_state(mac, expected_refresh_min=params.refresh_min)

        headers: dict[str, str] = {
            "X-Render-Time-Ms": str(elapsed_ms),
            "X-Cache-Hit": "1" if cache_hit else "0",
            "X-Mode-Id": resolved_persona,
        }
        if configured_refresh_minutes is not None:
            headers["X-Refresh-Minutes"] = str(configured_refresh_minutes)
        if mac and await consume_pending_refresh(mac):
            headers["X-Pending-Refresh"] = "1"
        if content_fallback:
            headers["X-Content-Fallback"] = "1"

        return Response(content=out_bytes, media_type=out_media, headers=headers)
    except (OSError, RuntimeError, TypeError, UnidentifiedImageError, ValueError) as exc:
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.error("[RENDER] Failed: %s", exc, exc_info=True)
        if mac:
            await log_render_stats(
                mac,
                params.persona or "unknown",
                False,
                elapsed_ms,
                voltage=params.v,
                rssi=params.rssi,
                status="error",
            )
        err_img = render_error(mac=mac or "unknown", screen_w=params.w, screen_h=params.h)
        return Response(
            content=image_to_bmp_bytes(err_img),
            media_type="image/bmp",
            status_code=500,
        )


@router.get("/widget/{mac}")
async def get_widget(
    mac: str,
    mode: str = "",
    w: int = 400,
    h: int = 300,
    size: str = "",
    x_device_token: Optional[str] = Header(default=None),
):
    await require_device_token(mac, x_device_token)
    if size == "small":
        w, h = 200, 150
    elif size == "medium":
        w, h = 400, 300
    elif size == "large":
        w, h = 800, 480

    config = await get_active_config(mac) or {}
    persona = mode.upper() if mode else config.get("modes", ["STOIC"])[0] if config.get("modes") else "STOIC"
    location_args = extract_location_settings(config)
    date_ctx = await get_date_context()
    weather = await get_weather(**location_args)
    img, _ = await generate_and_render(
        persona,
        config,
        date_ctx,
        weather,
        100.0,
        screen_w=w,
        screen_h=h,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300", "X-InkSight-Mode": persona},
    )


@router.get("/preview")
@limiter.limit("20/minute")
async def preview(
    request: Request,
    v: Optional[float] = Query(default=None),
    mac: Optional[str] = Query(default=None),
    persona: Optional[str] = Query(default=None),
    city_override: Optional[str] = Query(default=None),
    mode_override: Optional[str] = Query(default=None),
    memo_text: Optional[str] = Query(default=None),
    memo_title_1: Optional[str] = Query(default=None),
    memo_text_1: Optional[str] = Query(default=None),
    memo_title_2: Optional[str] = Query(default=None),
    memo_text_2: Optional[str] = Query(default=None),
    memo_title_3: Optional[str] = Query(default=None),
    memo_text_3: Optional[str] = Query(default=None),
    w: int = Query(default=SCREEN_WIDTH, ge=100, le=1600),
    h: int = Query(default=SCREEN_HEIGHT, ge=100, le=1200),
    no_cache: Optional[int] = Query(default=None),
    intent: Optional[int] = Query(default=None),
    colors: int = Query(default=2, ge=2, le=6),
    bpp: int = Query(default=2, ge=1, le=4),
    ui_language: Optional[str] = Query(default=None, description="Preview only: zh|en, overrides device mode_language"),
    x_device_token: Optional[str] = Header(default=None),
    x_inksight_llm_api_key: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Cookie(default=None),
):
    if mac:
        mac = validate_mac_param(mac)
        await ensure_web_or_device_access(request, mac, x_device_token, ink_session)
    
    # 获取当前登录用户 ID：
    # - 用于 Web 预览时关联 user_llm_config（个人信息里的 API Key）
    # - 计费归属仍按 BILLING.md：设备端按 owner 计费，Web 端按登录用户计费
    current_user_id = None
    try:
        from core.auth import get_current_user_optional

        current_user = await get_current_user_optional(request, ink_session)
        if current_user:
            current_user_id = current_user.get("user_id")
            logger.debug("[PREVIEW] Current user_id=%s for preview (mac=%s)", current_user_id, mac)
    except Exception:
        logger.warning("[PREVIEW] Failed to resolve current user for preview", exc_info=True)
    
    try:
        effective_v = await resolve_preview_voltage(v, mac)

        # 双屏模式预览：与 /render 的 DUAL_A/DUAL_B 同源同渲染，方便在浏览器
        # 直接核对设备将显示的便签/课程表，无需硬件在手。
        if mac and persona in ("DUAL_A", "DUAL_B"):
            from core.dual_render import render_dual_screen

            dual_cfg = await get_dual_config(mac)
            # 预览端点：按后台 screen 配置的尺寸/色数生成代表性图，便于在浏览器
            # 直接核对设备将显示的内容（无需硬件在手）。
            screen_cfg = (dual_cfg or {}).get("screen") or {}
            size = screen_cfg.get("size") or "4.2"
            pw, ph = (648, 480) if size == "5.83" else (400, 300)
            cfg_colors = screen_cfg.get("colors")
            pcolors = cfg_colors if isinstance(cfg_colors, int) and cfg_colors in (2, 3, 4) else colors
            img = render_dual_screen(mac, persona[-1], pw, ph, pcolors, dual_cfg)
            return Response(
                content=image_to_png_bytes(img),
                media_type="image/png",
                headers={"X-Mode-Id": persona, "X-Cache-Hit": "0"},
            )

        parsed_mode_override = None
        if mode_override:
            try:
                candidate = json.loads(mode_override)
                if isinstance(candidate, dict):
                    parsed_mode_override = candidate
            except JSONDecodeError:
                logger.warning("[PREVIEW] Failed to parse mode_override JSON", exc_info=True)
        _ui_lang = (ui_language or "").strip().lower()
        _preview_ui_lang = _ui_lang if _ui_lang in ("zh", "en") else None
        img, resolved_persona, cache_hit, _content_fallback, quota_exhausted, api_key_invalid, llm_mode_requires_quota, usage_source = await build_image(
            effective_v,
            mac,
            persona,
            screen_w=w,
            screen_h=h,
            skip_cache=(no_cache == 1),
            preview_city_override=(city_override.strip() if city_override else None),
            preview_mode_override=parsed_mode_override,
            preview_memo_text=(memo_text if isinstance(memo_text, str) else None),
            preview_memo_settings={k: v for k, v in {
                "memo_title_1": memo_title_1, "memo_text_1": memo_text_1,
                "memo_title_2": memo_title_2, "memo_text_2": memo_text_2,
                "memo_title_3": memo_title_3, "memo_text_3": memo_text_3,
            }.items() if v is not None} or None,
            preview_ui_language=_preview_ui_lang,
            current_user_id=current_user_id,
            user_api_key=x_inksight_llm_api_key,
            intent_only=(intent == 1),
            colors=colors,
        )
        if intent == 1:
            from fastapi.responses import JSONResponse
            if current_user_id:
                await log_user_activity(
                    current_user_id,
                    "preview.intent",
                    request=request,
                    metadata={"mac": mac or "", "persona": resolved_persona, "cache_hit": bool(cache_hit)},
                )

            return JSONResponse(
                status_code=200,
                content={
                    "cache_hit": cache_hit,
                    "usage_source": usage_source,
                    "persona": resolved_persona,
                    "requires_invite_code": quota_exhausted,
                    "llm_mode_requires_quota": llm_mode_requires_quota,
                },
            )
        # 如果 API key 无效，返回 JSON 响应，提醒用户
        if api_key_invalid:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=400,  # Bad Request
                content={
                    "error": "api_key_invalid",
                    "message": "您提供的 API key 无效或已过期，请检查个人信息或服务器中的 API key 配置",
                },
            )
        # 如果额度耗尽，返回 JSON 响应，让前端显示邀请码输入弹窗
        if quota_exhausted:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=402,  # Payment Required
                content={
                    "error": "quota_exhausted",
                    "message": "您的免费额度已用完，请输入邀请码获取更多额度",
                    "requires_invite_code": True,
                },
            )
        # 如果 img 为 None（不应该发生，但为了安全起见）
        if img is None:
            logger.error("[PREVIEW] img is None but quota_exhausted is False")
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={"error": "image_generation_failed", "message": "图片生成失败"},
            )
        png_bytes = image_to_png_bytes(img)
        logger.info("[PREVIEW] Generated PNG persona=%s size=%sx%s", resolved_persona, w, h)
        if current_user_id:
            await log_user_activity(
                current_user_id,
                "preview.render",
                request=request,
                metadata={"mac": mac or "", "persona": resolved_persona, "cache_hit": bool(cache_hit), "size": f"{w}x{h}"},
            )
        
        # 确定生成状态（使用英文避免编码问题）
        status_msg = "no_llm_required" if not llm_mode_requires_quota else ("model_generated" if not _content_fallback else "fallback_used")
        
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "X-Cache-Hit": "1" if cache_hit else "0",
                "X-Preview-Bypass": "1" if no_cache == 1 else "0",
                "X-Preview-Status": status_msg,
                "X-Llm-Required": "1" if llm_mode_requires_quota else "0",
            },
        )
    except (OSError, RuntimeError, TypeError, ValueError, UnidentifiedImageError):
        logger.exception("Exception occurred during preview")
        err_img = render_error(mac=mac or "unknown", screen_w=w, screen_h=h)
        return Response(
            content=image_to_png_bytes(err_img),
            media_type="image/png",
            status_code=500,
        )


@router.get("/preview/stream")
@limiter.limit("20/minute")
async def preview_stream(
    request: Request,
    v: Optional[float] = Query(default=None),
    mac: Optional[str] = Query(default=None),
    persona: Optional[str] = Query(default=None),
    city_override: Optional[str] = Query(default=None),
    mode_override: Optional[str] = Query(default=None),
    memo_text: Optional[str] = Query(default=None),
    memo_title_1: Optional[str] = Query(default=None),
    memo_text_1: Optional[str] = Query(default=None),
    memo_title_2: Optional[str] = Query(default=None),
    memo_text_2: Optional[str] = Query(default=None),
    memo_title_3: Optional[str] = Query(default=None),
    memo_text_3: Optional[str] = Query(default=None),
    w: int = Query(default=SCREEN_WIDTH, ge=100, le=1600),
    h: int = Query(default=SCREEN_HEIGHT, ge=100, le=1200),
    no_cache: Optional[int] = Query(default=None),
    colors: int = Query(default=2, ge=2, le=6),
    bpp: int = Query(default=2, ge=1, le=4),
    ui_language: Optional[str] = Query(default=None, description="Preview only: zh|en, overrides device mode_language"),
    x_device_token: Optional[str] = Header(default=None),
    x_inksight_llm_api_key: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Cookie(default=None),
):
    if mac:
        mac = validate_mac_param(mac)
        await ensure_web_or_device_access(request, mac, x_device_token, ink_session)
    
    # 获取当前登录用户 ID：同上，用于合入 user_llm_config，而计费仍按 owner / 登录用户归属
    current_user_id = None
    try:
        from core.auth import get_current_user_optional

        current_user = await get_current_user_optional(request, ink_session)
        if current_user:
            current_user_id = current_user.get("user_id")
            logger.debug("[PREVIEW_STREAM] Current user_id=%s for preview (mac=%s)", current_user_id, mac)
    except Exception:
        logger.warning("[PREVIEW_STREAM] Failed to resolve current user for preview", exc_info=True)

    async def stream():
        try:
            yield _sse_event("status", {"stage": "generating", "message": "正在生成..."})
            effective_v = await resolve_preview_voltage(v, mac)
            parsed_mode_override = None
            if mode_override:
                try:
                    candidate = json.loads(mode_override)
                    if isinstance(candidate, dict):
                        parsed_mode_override = candidate
                except JSONDecodeError:
                    logger.warning("[PREVIEW_STREAM] Failed to parse mode_override JSON", exc_info=True)

            _ui_lang = (ui_language or "").strip().lower()
            _preview_ui_lang = _ui_lang if _ui_lang in ("zh", "en") else None
            img, resolved_persona, cache_hit, _content_fallback, quota_exhausted, api_key_invalid, llm_mode_requires_quota, usage_source = await build_image(
                effective_v,
                mac,
                persona,
                screen_w=w,
                screen_h=h,
                skip_cache=(no_cache == 1),
                preview_city_override=(city_override.strip() if city_override else None),
                preview_mode_override=parsed_mode_override,
                preview_memo_text=(memo_text if isinstance(memo_text, str) else None),
                preview_memo_settings={k: v for k, v in {
                    "memo_title_1": memo_title_1, "memo_text_1": memo_text_1,
                    "memo_title_2": memo_title_2, "memo_text_2": memo_text_2,
                    "memo_title_3": memo_title_3, "memo_text_3": memo_text_3,
                }.items() if v is not None} or None,
                preview_ui_language=_preview_ui_lang,
                current_user_id=current_user_id,
                user_api_key=x_inksight_llm_api_key,
                colors=colors,
            )
            # 如果 API key 无效，返回错误事件
            if api_key_invalid:
                yield _sse_event("error", {
                    "error": "api_key_invalid",
                    "message": "您提供的 API key 无效或已过期，请检查个人信息或服务器中的 API key 配置",
                })
                return
            # 如果额度耗尽，返回错误事件
            if quota_exhausted:
                yield _sse_event("error", {
                    "error": "quota_exhausted",
                    "message": "您的免费额度已用完，请输入邀请码获取更多额度",
                    "requires_invite_code": True,
                    "usage_source": usage_source,
                })
                return
            yield _sse_event("status", {"stage": "rendering", "message": "正在渲染..."})
            png_bytes = image_to_png_bytes(img)
            data_url = f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}"
            # Keep SSE result payload aligned with /preview headers for UI.
            status_msg = (
                "no_llm_required"
                if not llm_mode_requires_quota
                else ("model_generated" if not _content_fallback else "fallback_used")
            )
            yield _sse_event(
                "result",
                {
                    "stage": "done",
                    "message": "完成",
                    "persona": resolved_persona,
                    "cache_hit": cache_hit,
                    "usage_source": usage_source,
                    "image_url": data_url,
                    "preview_status": status_msg,
                    "llm_required": bool(llm_mode_requires_quota),
                },
            )
        except (OSError, RuntimeError, TypeError, ValueError, UnidentifiedImageError) as exc:
            logger.exception("[PREVIEW_STREAM] Streaming preview failed")
            yield _sse_event("error", {"message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/dev/cache/clear")
async def clear_cache_dev():
    """Clear all caches (in-memory + persistent) - dev/testing endpoint."""
    await content_cache.clear()
    return {"status": "ok", "message": "Cache cleared"}
