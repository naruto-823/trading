"""辩论用的实时研究简报 —— 带 web_search 工具的 Haiku 调用

fail-soft:websearch 禁用 / 未配 key / 调用失败 → 返回 ""(辩论降级为无外部数据)。
spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md §5
"""

from __future__ import annotations

import logging

from anthropic import Anthropic

from app.config import settings

logger = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = """你是金融研究助理。用 web_search 查聚焦标的的近期实时情况:
近 1-2 周价格走势、关键催化事件、多空双方各自的论据。
输出一段 300-500 字中文研究简报,客观陈列正反两面事实,不下结论、不给买卖建议。"""


def gather_research(content: str, tickers: list[str]) -> str:
    """拉研究简报。永不抛异常 —— 失败返回 ""。"""
    if not settings.debate_websearch_enabled or not settings.anthropic_api_key:
        return ""
    focus = ", ".join(tickers) if tickers else "快讯涉及的宏观主题"
    try:
        client = Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url or None,
            timeout=45.0,
        )
        resp = client.messages.create(
            model=settings.debate_bull_model,  # 用 Haiku 跑研究
            max_tokens=900,
            system=RESEARCH_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"快讯:{content[:600]}\n\n聚焦标的:{focus}",
            }],
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": settings.debate_websearch_max_uses,
            }],
        )
        brief = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
        return brief[:1500]
    except Exception as exc:
        logger.warning("debate_research fail-soft: %s", exc)
        return ""
