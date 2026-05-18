"""Telegram 推送

只接 Telegram Bot API（最简、零依赖；其他渠道以后再加）。
配置：env 里 TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID。
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def is_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def send_telegram(text: str, parse_mode: str = "Markdown") -> dict:
    """发条消息。返回 {'ok': bool, 'detail': str | dict}.
    不抛异常 —— 调用方根据返回值决定怎么处理。
    """
    if not is_configured():
        return {"ok": False, "detail": "Telegram 未配置（缺 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）"}

    url = f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=10.0)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.is_success and data.get("ok"):
            return {"ok": True, "detail": data}
        # Telegram 返回 200 但 ok=false 是常见情况（如 chat_id 错）
        return {
            "ok": False,
            "detail": data.get("description") or resp.text or f"HTTP {resp.status_code}",
        }
    except Exception as exc:
        logger.exception("Telegram send failed")
        return {"ok": False, "detail": str(exc)}
