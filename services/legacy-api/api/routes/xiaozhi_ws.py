"""XiaoZhi WebSocket protocol adapter for the InkSight voice service."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from api.routes.voice import _resolve_device_voice_runtime_settings
from core.auth import require_device_token, validate_mac_param
from core.voice_service import (
    append_voice_ws_audio,
    close_voice_ws_session,
    commit_voice_ws_audio,
    create_voice_ws_session,
    interrupt_voice_ws_session,
    iter_voice_ws_events,
    start_voice_ws_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["xiaozhi-voice"])


def _decode_audio_frame(data: bytes, protocol_version: int) -> bytes | None:
    """Return raw Opus payload from XiaoZhi v1/v2 binary audio frames."""
    if protocol_version < 2:
        return data
    if len(data) < 16:
        return None
    version, packet_type, _reserved, _timestamp, payload_size = struct.unpack("!HHIII", data[:16])
    if version != 2 or packet_type != 0 or payload_size > len(data) - 16:
        return None
    return data[16 : 16 + payload_size]


async def _send_json(websocket: WebSocket, payload: dict) -> None:
    await websocket.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


async def _serve_xiaozhi_voice(websocket: WebSocket) -> None:
    mac_header = websocket.headers.get("device-id", "").strip()
    token_header = websocket.headers.get("x-device-token", "").strip()
    authorization = websocket.headers.get("authorization", "").strip()
    if not token_header and authorization.lower().startswith("bearer "):
        token_header = authorization[7:].strip()

    try:
        mac = validate_mac_param(mac_header)
        await require_device_token(mac, token_header)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:120])
        return

    await websocket.accept()
    try:
        settings = await _resolve_device_voice_runtime_settings(mac)
        session = create_voice_ws_session(settings=settings, access_user_id=None, access_mac=mac)
    except Exception:
        logger.exception("[XIAOZHI_WS] session setup failed mac=%s", mac)
        await websocket.close(code=1011, reason="voice session setup failed")
        return

    client_version = 1
    output_started = False

    async def _sender() -> None:
        nonlocal output_started
        async for event in iter_voice_ws_events(session):
            name = event.get("event")
            if event.get("__binary__"):
                if not output_started:
                    await _send_json(websocket, {"type": "tts", "state": "start", "session_id": session.session_id})
                    output_started = True
                await websocket.send_bytes(event["data"])
            elif name == "asr.final":
                text = str(event.get("transcript") or "")
                if text:
                    await _send_json(websocket, {"type": "stt", "text": text, "session_id": session.session_id})
            elif name == "tts.text_chunk":
                if not output_started:
                    await _send_json(websocket, {"type": "tts", "state": "start", "session_id": session.session_id})
                    output_started = True
                await _send_json(
                    websocket,
                    {
                        "type": "tts",
                        "state": "sentence_start",
                        "text": str(event.get("text") or ""),
                        "session_id": session.session_id,
                    },
                )
            elif name in {"turn.done", "turn.interrupted"}:
                if output_started:
                    await _send_json(websocket, {"type": "tts", "state": "stop", "session_id": session.session_id})
                    output_started = False
            elif name == "error":
                await _send_json(websocket, {"type": "alert", "status": "错误", "message": str(event.get("message") or "语音处理失败"), "emotion": "sad"})

    sender_task = asyncio.create_task(_sender())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            audio = message.get("bytes")
            if audio:
                opus_frame = _decode_audio_frame(audio, client_version)
                if opus_frame is None:
                    await _send_json(websocket, {"type": "error", "message": "invalid Opus audio frame"})
                    continue
                await append_voice_ws_audio(session, opus_frame)
                continue

            text = message.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                await _send_json(websocket, {"type": "error", "message": "invalid JSON"})
                continue
            event_type = str(payload.get("type") or "").lower()

            if event_type == "hello":
                client_version = int(payload.get("version") or websocket.headers.get("protocol-version") or 1)
                await _send_json(
                    websocket,
                    {
                        "type": "hello",
                        "version": client_version,
                        "transport": "websocket",
                        "session_id": session.session_id,
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": 16000,
                            "channels": 1,
                            "frame_duration": 60,
                        },
                    },
                )
            elif event_type == "listen":
                state = str(payload.get("state") or "").lower()
                if state == "start":
                    await start_voice_ws_session(
                        session,
                        sample_rate=16000,
                        screen_w=800,
                        screen_h=480,
                        include_image=False,
                        # The bridge decodes/encodes raw Opus even for XiaoZhi
                        # protocol v1, whose WebSocket carries unwrapped frames.
                        protocol_version=2,
                        audio_codec="opus",
                    )
                    if session.audio_codec != "opus":
                        await _send_json(websocket, {"type": "error", "message": "server Opus codec is unavailable"})
                        await websocket.close(code=1011, reason="Opus codec unavailable")
                        break
                    await _send_json(websocket, {"type": "listen", "state": "detect", "session_id": session.session_id})
                elif state == "stop":
                    await commit_voice_ws_audio(session)
                elif state == "detect":
                    # Wake-word notices are informational; audio capture starts
                    # when the device follows with listen.start.
                    pass
            elif event_type == "abort":
                await interrupt_voice_ws_session(session)
                if output_started:
                    await _send_json(websocket, {"type": "tts", "state": "stop", "session_id": session.session_id})
                    output_started = False
            elif event_type in {"goodbye", "close"}:
                break
            elif event_type == "mcp":
                await _send_json(websocket, {"type": "mcp", "payload": {"event": "error", "message": "MCP forwarding is not available on this voice bridge"}})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("[XIAOZHI_WS] session failed mac=%s", mac)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="voice session error")
    finally:
        await close_voice_ws_session(session)
        sender_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender_task


@router.websocket("/xiaozhi/v1/")
async def xiaozhi_voice_ws(websocket: WebSocket) -> None:
    await _serve_xiaozhi_voice(websocket)


@router.websocket("/xiaozhi/v1")
async def xiaozhi_voice_ws_without_trailing_slash(websocket: WebSocket) -> None:
    await _serve_xiaozhi_voice(websocket)
