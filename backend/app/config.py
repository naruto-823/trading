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

    # Quick Assess 相关性评分：LLM 判断快讯对用户持仓真实影响，过门槛才推
    # 阈值 0-100；score<阈值的不推（但仍落库，可在 dashboard 看）；0=禁用 scorer 全推
    # Haiku 4.5 单次 ~$0.001，每天 200-500 次 ≈ $0.3-0.5/day
    # （fox 需要带完整日期后缀，不是简写）
    relevance_threshold: int = 50
    relevance_model: str = "claude-haiku-4-5-20251001"

    # —— 辩论评分引擎 (debate scorer) ——
    # spec: docs/superpowers/specs/2026-05-20-debate-scorer-design.md
    debate_enabled: bool = True
    debate_bull_model: str = "claude-haiku-4-5-20251001"
    debate_bear_model: str = "claude-haiku-4-5-20251001"
    debate_judge_model: str = "claude-sonnet-4-6"
    # 升级判定:triage score 落在 [lo, hi] 临界带 → 升级辩论
    debate_escalate_score_lo: int = 35
    debate_escalate_score_hi: int = 65
    # 源 importance ≥ 此值(如 FOMC/CPI)→ 升级辩论
    debate_escalate_min_importance: int = 5
    debate_timeout_seconds: int = 90
    debate_zombie_minutes: int = 5  # debating 行超过此分钟数 → 对账强制收尾
    debate_max_workers: int = 2
    debate_websearch_enabled: bool = True
    debate_websearch_max_uses: int = 3
    debate_daily_cap: int = 0  # 0=不限;>0 时超额当天降级走 triage
    # 辩论 LLM 客户端重试次数 —— 中转代理突发限流(429)时退避重试。
    # SDK 默认 2 次扛不住;辩论是后台流程,可多retry trickle 着过。
    debate_api_max_retries: int = 6

    # —— 每小时仓位体检 worker (hourly position analysis) ——
    # spec: docs/superpowers/specs/2026-06-10-hourly-position-analysis-design.md
    hourly_analysis_enabled: bool = True
    hourly_analysis_top_n: int = 5            # 监控前 N 大重仓
    hourly_analysis_min_position_pct: float = 5.0   # 占净资产% 阈值,达标才进重仓深调
    hourly_analysis_news_per_stock: int = 3   # 每只重仓拉几条新闻
    hourly_analysis_model: str = ""           # 留空则回退 anthropic_model
    hourly_analysis_websearch_enabled: bool = True

    def hourly_model(self) -> str:
        return self.hourly_analysis_model or self.anthropic_model

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
