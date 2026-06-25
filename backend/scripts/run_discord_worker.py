"""独立常驻 Discord 新闻 worker(不嵌 uvicorn,避开 lifespan fire-and-forget 坑)。
用法: DISCORD_NEWS_REALTIME=1 uv run python -m scripts.run_discord_worker
依赖 automation/discord_login.sh 已起 9223 调试 Chrome 且已登录。
"""
import asyncio
import logging

from app.workers import discord_news_worker as dw

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

if __name__ == "__main__":
    dw._state["enabled"] = True
    try:
        asyncio.run(dw._run_loop())
    except KeyboardInterrupt:
        print("stopped")
