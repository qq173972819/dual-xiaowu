"""
作业 / 便签 OCR 识别
接收一张作业黑板/白板照片，调用视觉大模型（阿里云 qwen-vl）识别成文字清单，
返回结构化行，由前端回填到导入框让用户核对后再推送。
"""
from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse

from api.shared import logger
from core.auth import require_user
from core.config_store import get_user_llm_config
from core.mode_generator import VISION_MODELS, _build_messages, _call_llm_with_messages

router = APIRouter()

OCR_PROMPT = """你是一台「作业识别器」。下面是一张学生作业黑板/白板照片（可能含手写与打印文字，可能有红/蓝/绿笔标注）。
请识别其中的作业清单，严格遵守以下规则：
1. 每行一条作业，格式为「科目：内容」，例如「数学：练习册P60-64」。
2. 若某条无法判断科目，用「其他：」开头。
3. 保留原题号、页码、数量（如 ×2、第8课时、必做/选做）等关键信息，不要改写或省略。
4. 忽略与作业无关的内容（励志标语、日期落款、教师签名、课程表以外的涂鸦），除非它明确是作业要求。
5. 只输出清单本身：每行一条，不要任何解释、前言、markdown、括号说明。
6. 如果照片里没有可识别的作业，只输出一行「（未识别到作业）」。"""


@router.post("/ocr/homework")
async def ocr_homework(
    file: UploadFile = File(...),
    user_id: int = Depends(require_user),
):
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        return JSONResponse({"error": "图片过大（上限 8MB）"}, status_code=400)
    ctype = (file.content_type or "").lower()
    if ctype and "image" not in ctype:
        return JSONResponse({"error": "请上传图片文件"}, status_code=400)

    b64 = base64.b64encode(data).decode("ascii")

    # Key 解析：读「个人信息」里的视觉模型配置（image_provider / image_model / image_api_key），
    # 支持 aliyun / siliconflow；都没配时回退平台 DASHSCOPE 环境变量（阿里云）。
    provider = "aliyun"
    model = "qwen-vl-max"
    api_key = None
    try:
        user_cfg = await get_user_llm_config(user_id)
    except Exception:
        logger.warning("[OCR] load user_llm_config failed user_id=%s", user_id, exc_info=True)
        user_cfg = None
    if user_cfg:
        ip = (user_cfg.get("image_provider") or "").strip().lower()
        if ip in ("aliyun", "siliconflow", "openai"):
            provider = ip
        if provider == "siliconflow":
            # Qwen2.5-VL 已被硅基流动下架（403 Model disabled），默认用 Qwen3-VL。
            model = "Qwen/Qwen3-VL-32B-Instruct"
        im = (user_cfg.get("image_model") or "").strip()
        if im:
            model = im
        ik = (user_cfg.get("image_api_key") or "").strip()
        if ik:
            api_key = ik

    messages = _build_messages(OCR_PROMPT, b64, provider, model)
    try:
        raw = await _call_llm_with_messages(
            provider, model, messages,
            temperature=0.2, max_tokens=1500, api_key=api_key,
        )
    except Exception as e:  # 含未配置 Key / 模型不可用 / 网络错误
        logger.warning("[OCR] vision LLM call failed: %s", e, exc_info=True)
        msg = str(e)
        if "api key" in msg.lower() or "invalid" in msg.lower() or "DASHSCOPE" in msg:
            return JSONResponse(
                {"error": "未配置可用的视觉模型 Key：请在「个人信息」中配置视觉模型（阿里云通义或硅基流动）并填 API Key。"},
                status_code=400,
            )
        return JSONResponse({"error": f"识别失败：{msg}"}, status_code=502)

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith("```")]
    return {"lines": lines, "model": f"{provider}/{model}"}
