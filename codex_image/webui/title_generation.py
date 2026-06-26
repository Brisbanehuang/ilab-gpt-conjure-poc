from __future__ import annotations

import re
from typing import Any

import httpx

from codex_image.webui.omni_session import DEFAULT_TITLE_MODEL


SYSTEM_PROMPT = "你是图像生成任务标题助手。根据用户的生图提示词，返回一个10个中文字符以内的短标题。只返回标题，不要解释，不要标点。"
TITLE_TIMEOUT_SECONDS = 60.0


def normalize_generated_title(value: Any, *, max_chars: int = 10) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    text = text.strip(" \t\r\n\"'“”‘’《》<>【】[]()（）{}")
    text = re.sub(r"[。！？!?,，、；;：:]+$", "", text)
    text = text.strip(" \t\r\n\"'“”‘’《》<>【】[]()（）{}")
    return text[:max_chars]


def fallback_title(prompt: Any, *, max_chars: int = 9) -> str:
    text = " ".join(str(prompt or "").split()).strip()
    if not text:
        return "未命名"
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + "…"


async def generate_task_title(config: Any, selected_key: dict[str, Any] | None, prompt: str) -> str:
    if not selected_key or selected_key.get("supports_title_model") is False:
        return fallback_title(prompt)
    api_key = str(selected_key.get("key") or "").strip()
    if not api_key:
        return fallback_title(prompt)
    try:
        async with httpx.AsyncClient(timeout=TITLE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{str(config.base_url).rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": DEFAULT_TITLE_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": str(prompt or "")},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 24,
                },
            )
        if response.status_code < 200 or response.status_code >= 300:
            return fallback_title(prompt)
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else []
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") if isinstance(first, dict) else {}
        title = normalize_generated_title(message.get("content") if isinstance(message, dict) else "")
        return title or fallback_title(prompt)
    except Exception:
        return fallback_title(prompt)
