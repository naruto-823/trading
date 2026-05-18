"""Bark iOS 推送

为啥用 Bark：
- iOS 系统级推送，锁屏可见，不用打开任何 app
- 国内直连官方公服，不需要科学上网
- 配置只要 1 个 key
- 服务端开源可自部署

API: POST {base_url}/push 带 JSON
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.bark_device_key)


def send_bark(
    title: str,
    body: str,
    *,
    group: str = "trading-alerts",
    sound: str = "bell",
    level: str = "active",
    url: str | None = None,
) -> dict:
    """发条 Bark 推送。返回 {'ok': bool, 'detail': str | dict}。
    不抛异常 —— 调用方根据返回值决定怎么处理。

    level:
    - active（默认）：普通通知，有声有 banner
    - timeSensitive：突破"专注"模式，给真正紧急的告警
    - critical：突破静音，慎用
    """
    if not is_configured():
        return {"ok": False, "detail": "Bark 未配置（缺 BARK_DEVICE_KEY）"}

    push_url = f"{settings.bark_base_url.rstrip('/')}/push"
    payload: dict = {
        "device_key": settings.bark_device_key,
        "title": title,
        "body": body,
        "group": group,
        "sound": sound,
        "level": level,
    }
    if url:
        payload["url"] = url

    try:
        resp = httpx.post(push_url, json=payload, timeout=8.0)
        try:
            data = resp.json()
        except Exception:
            data = {}
        # Bark 成功返回 {"code": 200, "message": "success", ...}
        if resp.is_success and data.get("code") == 200:
            return {"ok": True, "detail": data}
        return {
            "ok": False,
            "detail": data.get("message") or resp.text or f"HTTP {resp.status_code}",
        }
    except Exception as exc:
        logger.exception("Bark send failed")
        return {"ok": False, "detail": str(exc)}
