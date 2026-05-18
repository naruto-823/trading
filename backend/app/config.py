from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    # 长桥 OpenAPI
    longport_app_key: str = ""
    longport_app_secret: str = ""
    longport_access_token: str = ""

    # AI API (支持 OpenAI 兼容格式)
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = "claude-sonnet-4-20250514"
    ai_provider: str = "openai"

    # Anthropic 原生协议（/v1/messages），给 briefing.py 用
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    anthropic_model: str = "claude-opus-4-7"

    # Bark iOS 推送（market-watcher 触发告警时发送）
    # device_key = Bark app 首页 URL 里 api.day.app/ 后面那段（22 字符）
    bark_device_key: str = ""
    bark_base_url: str = "https://api.day.app"

    # 新闻源 fallback 链：Finnhub → Tavily → Brave → Google News RSS
    # 没配 key 的 tier 自动跳过；Google News RSS 永远可用兜底
    finnhub_api_key: str = ""
    tavily_api_key: str = ""
    brave_api_key: str = ""

    # 金十 MCP（结构化快讯 + 财经日历），配 token 优先用，否则 fall back JS 解析
    jin10_mcp_token: str = ""
    jin10_mcp_url: str = "https://mcp.jin10.com/mcp"

    # 金十实时浏览器 worker（Playwright headless chromium 跑 jin10.com，借浏览器解码绕过 WS 加密）
    # 真亚秒级，~150MB 内存代价。和 MCP 1min 轮询并存（共用 event_notification 去重）
    jin10_browser_realtime: bool = False

    # Database
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'trading.db'}"

    def validate_longport(self) -> bool:
        placeholders = {"", "your_app_key", "your_app_secret", "your_access_token"}
        return all(
            v and v not in placeholders
            for v in [self.longport_app_key, self.longport_app_secret, self.longport_access_token]
        )

    def validate_ai(self) -> bool:
        """检查 AI API 是否已配置"""
        if self.ai_api_key and self.ai_api_key != "your_api_key":
            return True
        if self.anthropic_api_key and self.anthropic_api_key != "your_anthropic_api_key":
            return True
        return False

    def validate_anthropic(self) -> bool:
        return self.validate_ai()


settings = Settings()
