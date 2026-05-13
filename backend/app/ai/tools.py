"""AI tool use 定义 + 映射到 services（支持 OpenAI function calling 格式）"""

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.longbridge.sync import sync_all, sync_account, sync_positions, sync_orders, sync_executions
from app.services.account import get_latest_account
from app.services.executions import list_executions
from app.services.orders import list_orders
from app.services.pnl import get_pnl_summary
from app.services.portfolio_analysis import analyze_portfolio
from app.services.positions import list_positions
from app.services.quote import get_realtime_quotes

TOOL_DEFINITIONS = [
    {
        "name": "get_account",
        "description": "获取最新的账户概览信息，包括净资产、现金、市值、今日盈亏、总盈亏等",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_positions",
        "description": "获取当前持仓列表，包括每个标的的成本价、现价、浮动盈亏等。可按市场过滤",
        "input_schema": {
            "type": "object",
            "properties": {
                "market": {
                    "type": "string",
                    "description": "市场过滤，如 HK 或 US，不传则返回全部",
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_executions",
        "description": "获取历史成交记录。可按标的、日期范围过滤",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "标的代码过滤，如 700.HK"},
                "from_date": {"type": "string", "description": "开始日期，格式 YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "结束日期，格式 YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "返回条数限制，默认 50"},
            },
            "required": [],
        },
    },
    {
        "name": "list_orders",
        "description": "获取历史订单记录。可按标的、状态、日期范围过滤",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "标的代码过滤"},
                "status": {"type": "string", "description": "订单状态过滤"},
                "from_date": {"type": "string", "description": "开始日期，格式 YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "结束日期，格式 YYYY-MM-DD"},
            },
            "required": [],
        },
    },
    {
        "name": "get_pnl_summary",
        "description": "获取盈亏汇总，可按标的或市场分组",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_by": {
                    "type": "string",
                    "enum": ["symbol", "market"],
                    "description": "分组方式：symbol（按标的）或 market（按市场）",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_quote",
        "description": "获取标的的实时报价，包括最新价、涨跌幅、成交量等",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "标的代码列表，如 ['AAPL.US', '700.HK']",
                },
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "analyze_portfolio",
        "description": (
            "对当前持仓进行多维度客观分析（仅做事实统计，不构成任何投资建议）。"
            "包含：summary（市值/货币/市场分布）、concentration（前 N 大持仓集中度）、"
            "pnl_distribution（盈亏分布、单标的最大盈/亏、当日涨跌幅前列）、"
            "cost_structure（成本占比 vs 市值占比 漂移）、derivatives（期权多空敞口）、"
            "alerts（达到阈值的客观风险提示）。"
            "适用于用户询问 '分析我的仓位'、'仓位体检'、'集中度怎么样'、'今天哪些标的涨/跌得多' 等场景。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "sync_now",
        "description": "触发数据同步，从长桥拉取最新数据。建议在数据可能过期时调用",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["all", "account", "positions", "orders", "executions"],
                    "description": "同步类型，默认 all",
                },
            },
            "required": [],
        },
    },
]

# OpenAI function calling 格式（从 Anthropic 格式自动转换）
OPENAI_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }
    for tool in TOOL_DEFINITIONS
]

SYNC_HANDLERS = {
    "all": sync_all,
    "account": sync_account,
    "positions": sync_positions,
    "orders": sync_orders,
    "executions": sync_executions,
}


def execute_tool(tool_name: str, tool_input: dict, db: Session) -> str:
    """执行工具调用，返回 JSON 字符串结果"""
    try:
        if tool_name == "get_account":
            result = get_latest_account(db)
            if result is None:
                return json.dumps({"error": "暂无账户数据，请先同步"}, ensure_ascii=False)
            return json.dumps(result.model_dump(), ensure_ascii=False, default=str)

        elif tool_name == "list_positions":
            market = tool_input.get("market")
            items = list_positions(db, market=market)
            return json.dumps([item.model_dump() for item in items], ensure_ascii=False, default=str)

        elif tool_name == "list_executions":
            from_date = _parse_date(tool_input.get("from_date"))
            to_date = _parse_date(tool_input.get("to_date"))
            limit = tool_input.get("limit", 50)
            items, total = list_executions(
                db,
                symbol=tool_input.get("symbol"),
                from_date=from_date,
                to_date=to_date,
                size=limit,
            )
            return json.dumps({"items": [i.model_dump() for i in items], "total": total}, ensure_ascii=False, default=str)

        elif tool_name == "list_orders":
            from_date = _parse_date(tool_input.get("from_date"))
            to_date = _parse_date(tool_input.get("to_date"))
            items, total = list_orders(
                db,
                symbol=tool_input.get("symbol"),
                status=tool_input.get("status"),
                from_date=from_date,
                to_date=to_date,
            )
            return json.dumps({"items": [i.model_dump() for i in items], "total": total}, ensure_ascii=False, default=str)

        elif tool_name == "get_pnl_summary":
            group_by = tool_input.get("group_by", "symbol")
            items = get_pnl_summary(db, group_by=group_by)
            return json.dumps([i.model_dump() for i in items], ensure_ascii=False, default=str)

        elif tool_name == "get_quote":
            symbols = tool_input.get("symbols", [])
            if not symbols:
                return json.dumps({"error": "symbols 不能为空"}, ensure_ascii=False)
            quotes = get_realtime_quotes(symbols)
            return json.dumps([q.model_dump() for q in quotes], ensure_ascii=False, default=str)

        elif tool_name == "analyze_portfolio":
            result = analyze_portfolio(db)
            return json.dumps(result, ensure_ascii=False, default=str)

        elif tool_name == "sync_now":
            kind = tool_input.get("kind", "all")
            handler = SYNC_HANDLERS.get(kind)
            if not handler:
                return json.dumps({"error": f"不支持的同步类型: {kind}"}, ensure_ascii=False)
            result = handler(db)
            if isinstance(result, list):
                return json.dumps(
                    [{"kind": r.kind, "status": r.status, "rows_written": r.rows_written} for r in result],
                    ensure_ascii=False,
                )
            return json.dumps({"kind": result.kind, "status": result.status, "rows_written": result.rows_written}, ensure_ascii=False)

        else:
            return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
