"""WebSocket 端点：实时推送报价数据

协议：
  客户端连接后发送 JSON: {"symbols": ["AAPL.US", "NVDA.US"]}
  服务端每 3 秒推送:     {"type": "quotes", "data": [...QuoteData]}
  出错时推送:            {"type": "error", "message": "..."}
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.quote import get_realtime_quotes

logger = logging.getLogger(__name__)

router = APIRouter()

PUSH_INTERVAL_SECONDS = 3
MAX_SYMBOLS_PER_CONNECTION = 50


@router.websocket("/ws/quotes")
async def quotes_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    symbols: list[str] = []

    try:
        # 等待客户端发送 symbol 列表（最多等 10 秒）
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        payload = json.loads(raw)
        symbols = [str(s).strip() for s in payload.get("symbols", []) if s]
        symbols = symbols[:MAX_SYMBOLS_PER_CONNECTION]

        if not symbols:
            await websocket.send_json({"type": "error", "message": "symbols 不能为空"})
            await websocket.close()
            return

        logger.info("WS /ws/quotes connected, symbols=%s", symbols)

        # 立即推送一次，让前端不用等第一个 interval
        await _push_quotes(websocket, symbols)

        # 持续推送
        while True:
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)
            await _push_quotes(websocket, symbols)

    except asyncio.TimeoutError:
        await websocket.send_json({"type": "error", "message": "等待 symbols 超时"})
        await websocket.close()
    except WebSocketDisconnect:
        logger.info("WS /ws/quotes disconnected, symbols=%s", symbols)
    except Exception as exc:
        logger.warning("WS /ws/quotes error: %s", exc)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


async def _push_quotes(websocket: WebSocket, symbols: list[str]) -> None:
    """在线程池中调用同步报价函数，推送结果给客户端"""
    loop = asyncio.get_event_loop()
    quotes = await loop.run_in_executor(None, get_realtime_quotes, symbols)
    await websocket.send_json({
        "type": "quotes",
        "data": [q.model_dump() for q in quotes],
    })
