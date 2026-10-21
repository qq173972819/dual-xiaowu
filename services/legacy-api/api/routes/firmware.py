import asyncio
import logging
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from typing import Optional

from api.shared import (
    GITHUB_OWNER,
    GITHUB_REPO,
    load_firmware_releases,
    validate_firmware_url,
)
from core.auth import validate_mac_param
from core.config_store import (
    generate_device_token,
    get_device_state as _get_device_state_row,
)

router = APIRouter(tags=["firmware"])
ota_manifest_router = APIRouter(tags=["firmware-ota-manifest"])
logger = logging.getLogger(__name__)

# ── Concurrent OTA download limiter ─────────────────────────
OTA_MAX_CONCURRENT = int(os.getenv("OTA_MAX_CONCURRENT", "3"))
_ota_semaphore: Optional[asyncio.Semaphore] = None


def _get_ota_semaphore() -> asyncio.Semaphore:
    global _ota_semaphore
    if _ota_semaphore is None:
        _ota_semaphore = asyncio.Semaphore(OTA_MAX_CONCURRENT)
    return _ota_semaphore


def _build_backend_base(request: Request) -> str:
    """Build the backend base URL for firmware proxy endpoints."""
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
        or ""
    ).strip()
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").strip()
    return f"{scheme}://{host}"


def _ota_chip_family(system_info: dict) -> str:
    """Resolve the ESP chip family from the system-info POST sent by XiaoZhi."""
    chip_name = str(system_info.get("chip_model_name") or "").strip().lower()
    if "s3" in chip_name:
        return "ESP32-S3"
    if "c3" in chip_name:
        return "ESP32-C3"
    if "esp32" in chip_name or chip_name in {"esp32", "esp32-d0wdq6"}:
        return "ESP32"

    # Older firmware may omit chip_model_name; infer from its board name.
    board = system_info.get("board")
    if isinstance(board, dict):
        board_name = " ".join(str(value) for value in board.values()).lower()
        if "s3" in board_name:
            return "ESP32-S3"
        if "c3" in board_name:
            return "ESP32-C3"
    return ""


@ota_manifest_router.post("/ota")
async def xiaozhi_ota_manifest(request: Request):
    """XiaoZhi-compatible OTA check endpoint used by firmware's POST /ota.

    The firmware expects an optional ``firmware`` object in the response. We
    only advertise releases matching the device chip family; when no matching
    release exists (or GitHub is temporarily unavailable), the running version
    is returned with an empty URL, which is interpreted as no update.
    """
    try:
        system_info = await request.json()
    except Exception:
        system_info = {}
    if not isinstance(system_info, dict):
        system_info = {}

    application = system_info.get("application")
    if not isinstance(application, dict):
        application = {}
    current_version = str(application.get("version") or "0.0.0").strip() or "0.0.0"
    chip_family = _ota_chip_family(system_info)

    firmware = {"version": current_version, "url": ""}
    if chip_family:
        try:
            release_data = await load_firmware_releases()
            match = next(
                (
                    release
                    for release in release_data.get("releases", [])
                    if str(release.get("chip_family") or "").upper() == chip_family
                    and release.get("version")
                    and release.get("download_url")
                ),
                None,
            )
            if match:
                firmware = {
                    "version": str(match["version"]),
                    "url": str(match["download_url"]),
                }
        except Exception as exc:
            logger.warning(
                "[OTA MANIFEST] Release lookup failed; reporting no update "
                "(chip=%s current=%s): %s",
                chip_family,
                current_version,
                exc,
            )

    now_ms = int(time.time() * 1000)
    response = {
        "firmware": firmware,
        "server_time": {"timestamp": now_ms, "timezone_offset": 480},
    }

    device_id = request.headers.get("device-id") or system_info.get("mac_address") or ""
    try:
        mac = validate_mac_param(str(device_id).strip())
    except Exception:
        mac = ""
    if mac:
        try:
            state = await _get_device_state_row(mac)
            device_token = (state or {}).get("auth_token") or await generate_device_token(mac)
            websocket_url = os.getenv("INKSIGHT_XIAOZHI_WS_URL", "").strip()
            if not websocket_url:
                host = (
                    request.headers.get("x-forwarded-host")
                    or request.headers.get("host")
                    or request.url.netloc
                ).strip()
                scheme = (
                    request.headers.get("x-forwarded-proto")
                    or request.url.scheme
                    or "http"
                ).strip().lower()
                websocket_scheme = "wss" if scheme == "https" else "ws"
                websocket_url = f"{websocket_scheme}://{host}/xiaozhi/v1/"
            response["websocket"] = {
                "url": websocket_url,
                "token": device_token,
                "version": 1,
            }
        except Exception as exc:
            logger.warning("[OTA MANIFEST] Could not provision voice config for %s: %s", mac, exc)

    return response


@router.get("/health")
async def health():
    return {"status": "ok", "version": "1.1.0"}


@router.get("/firmware/releases")
async def firmware_releases(refresh: bool = Query(default=False)):
    try:
        return await load_firmware_releases(force_refresh=refresh)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        return JSONResponse(
            {
                "error": "firmware_release_fetch_failed",
                "message": str(exc),
                "repo": f"{GITHUB_OWNER}/{GITHUB_REPO}",
            },
            status_code=503,
        )


@router.get("/firmware/releases/latest")
async def firmware_releases_latest(refresh: bool = Query(default=False)):
    try:
        data = await load_firmware_releases(force_refresh=refresh)
        releases = data.get("releases", [])
        if not releases:
            return JSONResponse(
                {
                    "error": "firmware_release_not_found",
                    "message": "No published firmware release with .bin asset found",
                    "repo": f"{GITHUB_OWNER}/{GITHUB_REPO}",
                },
                status_code=404,
            )
        return {
            "source": data.get("source"),
            "repo": data.get("repo"),
            "cached": data.get("cached", False),
            "latest": releases[0],
        }
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        return JSONResponse(
            {
                "error": "firmware_release_fetch_failed",
                "message": str(exc),
                "repo": f"{GITHUB_OWNER}/{GITHUB_REPO}",
            },
            status_code=503,
        )


@router.get("/firmware/validate-url")
async def firmware_validate_url(url: str = Query(..., description="Firmware .bin URL")):
    try:
        return await validate_firmware_url(url)
    except ValueError as exc:
        return JSONResponse(
            {"error": "invalid_firmware_url", "message": str(exc), "url": url},
            status_code=400,
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        return JSONResponse(
            {"error": "firmware_url_unreachable", "message": str(exc), "url": url},
            status_code=503,
        )


# ── Firmware download proxy (GitHub CDN → ESP32) ─────────────

@router.get("/firmware/download/{version}")
async def firmware_download(
    version: str,
    request: Request,
    mac: str = Query(..., description="Device MAC address"),
    accept_language: Optional[str] = Header(default=None, alias="Accept-Language"),
):
    """
    Proxy firmware download from GitHub CDN to the ESP32 device.

    The ESP32 may fail TLS handshake with GitHub's DigiCert chain because it
    only has Let's Encrypt CA built-in. This endpoint streams the binary
    through the backend so the ESP32 only needs to trust one server certificate.

    Concurrency is limited by OTA_MAX_CONCURRENT (default 3) to protect the
    backend from simultaneous large outbound transfers. If the limit is hit,
    the device's OTA state is cleared so the mobile app can show a retry prompt.
    """
    sem = _get_ota_semaphore()
    is_zh = (accept_language or "").lower().startswith("zh")

    # Try to grab a concurrent slot (wait up to 0.5s before giving up)
    try:
        await asyncio.wait_for(sem.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        logger.warning(
            "[OTA DOWNLOAD] Concurrency limit (%d) reached, rejecting version=%s mac=%s",
            OTA_MAX_CONCURRENT,
            version,
            mac,
        )
        # Clear device OTA state so the app can prompt a retry
        from core.config_store import update_device_state

        await update_device_state(
            mac,
            pending_ota=0,
            ota_version="",
            ota_url="",
            ota_progress=0,
            ota_result="failed:concurrent_limit",
        )
        # Return a JSON response so both ESP32 and mobile polling can parse it
        return JSONResponse(
            status_code=503,
            content={
                "error": "concurrent_limit_reached",
                "detail": (
                    "服务器同时刷机人数已达上限，请稍后重试"
                    if is_zh
                    else "Too many firmware updates in progress. Please try again shortly."
                ),
            },
            headers={"Content-Type": "application/json"},
        )

    # Look up the firmware URL stored by mobile when OTA was triggered.
    # For new OTA records: use ota_original_url (GitHub CDN direct link) to download.
    # For backward compatibility: fall back to ota_url if ota_original_url is not present.
    state = await _get_device_state_row(mac)
    ota_original_url = state.get("ota_original_url", "") if state else ""
    download_url = ota_original_url or state.get("ota_url", "") if state else ""

    if not download_url:
        sem.release()
        raise HTTPException(status_code=404, detail="No pending OTA firmware URL for this device")

    # ── Phase 1: Fetch headers only to get Content-Length ─────────────────
    # This MUST succeed before we create StreamingResponse, because headers
    # (including Content-Length) are sent before the generator runs.
    content_length: Optional[int] = None
    try:
        # Browser-like headers to avoid CDN returning placeholder data
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/octet-stream,application/x-bin,application/binary,*/*",
            "Accept-Encoding": "identity",
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=20.0),
            follow_redirects=True,
            max_redirects=10,
            http2=False,
        ) as client:
            async with client.stream("GET", download_url, headers=headers) as resp:
                resp.raise_for_status()

                # Check common case variations
                cl = (resp.headers.get("content-length") or
                      resp.headers.get("Content-Length") or
                      resp.headers.get("CONTENT-LENGTH"))
                if cl:
                    content_length = int(cl)
                else:
                    logger.warning("[OTA DOWNLOAD] Phase-1: No Content-Length header in response")
                await resp.aclose()
    except Exception as exc:
        # Phase-1 is mandatory: without Content-Length header we cannot set it
        # in StreamingResponse, and the ESP32 will reject the OTA (magic byte error).
        # Return 502 so device can retry later (network may recover).
        logger.error("[OTA DOWNLOAD] Phase-1 FAILED (%s): %s", type(exc).__name__, exc, exc_info=True)
        sem.release()
        raise HTTPException(status_code=502, detail="Could not retrieve firmware metadata (network error)")

    # If GitHub didn't provide Content-Length, we still proceed but warn.
    if content_length is None:
        logger.warning("[OTA DOWNLOAD] Proceeding without Content-Length; device will use unknown-size mode")

    # ── Phase 2: Download full firmware then stream to device ───────────────
    async def stream_and_release():
        """Download full firmware then stream to device (non-streaming upstream).

        Uses full GET (not streaming) to avoid CDN issues with streaming requests.
        """
        try:
            # Browser-like headers to avoid CDN returning placeholder data
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/octet-stream,application/x-bin,application/binary,*/*",
                "Accept-Encoding": "identity",  # No compression
            }
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                follow_redirects=True,
                max_redirects=10,
                http2=False,  # Disable HTTP/2
            ) as client:
                # Full download (not streaming) - mimics browser behavior
                resp = await client.get(download_url, headers=headers)
                resp.raise_for_status()

                content = resp.content  # Full content in memory
                total_bytes = len(content)

                # Stream to device in chunks
                for i in range(0, total_bytes, 8192):
                    yield content[i:i + 8192]

                if content_length is not None and total_bytes != content_length:
                    logger.warning("[OTA DOWNLOAD] Size mismatch: declared=%d, actual=%d", content_length, total_bytes)
        except httpx.HTTPError as exc:
            logger.error(
                "[OTA DOWNLOAD] Upstream fetch failed version=%s mac=%s: %s",
                version,
                mac,
                exc,
            )
        except Exception as exc:
            logger.error(
                "[OTA DOWNLOAD] Unexpected download error version=%s mac=%s: %s",
                version,
                mac,
                exc,
            )
        finally:
            sem.release()

    # Build response headers (Content-Length set here, BEFORE generator runs)
    filename = download_url.split("/")[-1] or f"inksight-{version}.bin"
    response_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Firmware-Version": version.lstrip("v"),
        "X-Device-MAC": mac,
    }
    if content_length is not None:
        response_headers["Content-Length"] = str(content_length)

    return StreamingResponse(
        stream_and_release(),
        media_type="application/octet-stream",
        headers=response_headers,
    )
