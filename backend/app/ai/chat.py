"""AI 对话循环 + SSE 流式输出（支持 OpenAI 兼容 API + prompt-based tool calling）"""

import json
import re
from collections.abc import AsyncGenerator
from datetime import date

from openai import OpenAI, APIError
from sqlalchemy.orm import Session

from app.ai.tools import OPENAI_TOOL_DEFINITIONS, execute_tool
from app.config import settings

MAX_TOOL_CALLS_PER_TURN = 8

TOOL_DESCRIPTIONS = """可用工具列表（用 JSON 格式调用）：

1. get_account - 获取账户概览（净资产、现金、市值、盈亏）
   参数：无

2. list_positions - 获取当前持仓列表
   参数：market（可选，HK 或 US）

3. list_executions - 获取历史成交记录
   参数：symbol（可选）, from_date（可选，YYYY-MM-DD）, to_date（可选）, limit（可选，默认50）

4. list_orders - 获取历史订单
   参数：symbol（可选）, status（可选）, from_date（可选）, to_date（可选）

5. get_pnl_summary - 获取盈亏汇总
   参数：group_by（可选，symbol 或 market）

6. get_quote - 获取实时报价
   参数：symbols（必填，标的代码数组，如 ["AAPL.US", "700.HK"]）

7. analyze_portfolio - 持仓多维度客观分析（集中度/盈亏分布/成本结构/期权敞口/风险提示）
   参数：无
   适用场景：用户询问"分析仓位"、"仓位体检"、"集中度怎么样"、"今天哪些标的涨/跌得多"等

8. sync_now - 触发数据同步
   参数：kind（可选，all/account/positions/orders/executions）"""

SYSTEM_PROMPT = """你是 AI Trading 助手，帮助用户查看和分析他们的长桥证券账户数据。

当前日期：{today}

{tool_descriptions}

工具调用方式：当你需要查询数据时，在回复中使用以下格式调用工具：
<tool_call>
{{"name": "工具名", "input": {{参数对象}}}}
</tool_call>

你可以在一次回复中调用多个工具。每个工具调用都用 <tool_call></tool_call> 包裹。
调用工具后，系统会返回结果，你再根据结果回答用户。

规则：
- 用中文简洁回答
- 必须先调用工具获取数据，不要编造数据
- 金额显示时注意货币单位（HKD/USD）
- 百分比保留两位小数
- 你只能查询数据，不能下单或修改任何交易
- 如果数据为空，建议用户先同步数据

【重要边界】
- 严禁给出任何买卖建议、价格预测、止损止盈位等投资建议
- 当用户询问"该不该买/卖"、"建议买什么"、"现在加仓还是减仓"等主观决策时，礼貌拒绝并说明你只能基于账户数据做客观分析
- 当用户要求"分析仓位"、"仓位体检"、"风险评估"时，调用 analyze_portfolio 工具，把返回的数据用清晰的结构呈现，但只陈述事实（如"前 3 大持仓占 65%"），不输出主观判断（如"建议分散"）
- analyze_portfolio 返回的 alerts 字段是基于阈值的客观提示，不是建议；可以转述但要标注"达到阈值"而非"应该调整"
- 任何分析结尾都附上一句："以上为客观数据陈述，不构成投资建议。" """

TOOL_CALL_PATTERN = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

def _get_ai_client() -> OpenAI:
    """获取 AI 客户端"""
    api_key = settings.ai_api_key or settings.anthropic_api_key
    base_url = settings.ai_base_url or settings.anthropic_base_url or None
    return OpenAI(api_key=api_key, base_url=base_url)

def _extract_tool_calls(text: str) -> list[tuple[str, dict]]:
    """从模型输出中提取 <tool_call> 块"""
    results = []
    for match in TOOL_CALL_PATTERN.finditer(text):
        try:
            call = json.loads(match.group(1))
            name = call.get("name", "")
            tool_input = call.get("input", {})
            if name:
                results.append((name, tool_input))
        except json.JSONDecodeError:
            continue
    return results

def _strip_tool_calls(text: str) -> str:
    """移除文本中的 <tool_call> 块，返回干净的文本"""
    cleaned = TOOL_CALL_PATTERN.sub("", text).strip()
    # 也移除可能的 thinking 标签
    cleaned = re.sub(r"", "", cleaned, flags=re.DOTALL).strip()
    return cleaned

async def stream_chat(messages: list[dict], db: Session) -> AsyncGenerator[str, None]:
    """流式对话，yield SSE 格式的事件"""
    if not settings.validate_ai():
        async for event in _mock_chat(messages, db):
            yield event
        return

    client = _get_ai_client()
    system_content = SYSTEM_PROMPT.format(
        today=date.today().isoformat(),
        tool_descriptions=TOOL_DESCRIPTIONS,
    )

    api_messages: list[dict] = [{"role": "system", "content": system_content}]
    for msg in messages:
        api_messages.append({"role": msg["role"], "content": msg["content"]})

    # 使用 prompt-based tool calling（兼容性最好）
    async for event in _prompt_tool_loop(client, api_messages, db):
        yield event


async def _prompt_tool_loop(
    client: OpenAI,
    api_messages: list[dict],
    db: Session,
) -> AsyncGenerator[str, None]:
    """Prompt-based tool calling 循环"""
    tool_call_count = 0
    response = None

    while True:
        if response is None:
            try:
                response = client.chat.completions.create(
                    model=settings.ai_model,
                    max_tokens=4096,
                    messages=api_messages,
                )
            except Exception as exc:
                yield _sse_event("error", {"message": f"AI API 错误: {exc}"})
                return

        content = response.choices[0].message.content or ""
        tool_calls = _extract_tool_calls(content)

        if not tool_calls:
            # 没有工具调用，直接输出文本
            clean_text = _strip_tool_calls(content)
            if clean_text:
                yield _sse_event("text", {"content": clean_text})
            break

        # 有工具调用：执行工具，收集结果
        tool_results_text = []
        for tool_name, tool_input in tool_calls:
            tool_call_count += 1
            yield _sse_event("tool_use", {"name": tool_name, "input": tool_input})

            if tool_call_count > MAX_TOOL_CALLS_PER_TURN:
                result = json.dumps({"error": "单轮工具调用次数已达上限"}, ensure_ascii=False)
            else:
                result = execute_tool(tool_name, tool_input, db)

            yield _sse_event("tool_result", {"name": tool_name, "result": result})
            tool_results_text.append(f"工具 {tool_name} 的结果：\n{result}")

        # 将 assistant 消息和工具结果加入上下文
        api_messages.append({"role": "assistant", "content": content})
        api_messages.append({"role": "user", "content": "工具调用结果如下，请根据数据回答用户的问题：\n\n" + "\n\n".join(tool_results_text)})

        if tool_call_count > MAX_TOOL_CALLS_PER_TURN:
            yield _sse_event("text", {"content": "\n\n（已达到单轮工具调用上限）"})
            break

        # 继续对话，让模型根据工具结果生成最终回复
        response = None

    yield _sse_event("done", {})


async def _mock_chat(messages: list[dict], db: Session) -> AsyncGenerator[str, None]:
    """Mock AI 对话：无 API Key 时，根据关键词自动调用工具并生成回复"""
    import asyncio

    last_message = messages[-1]["content"] if messages else ""
    lower_msg = last_message.lower()

    tool_calls_plan: list[tuple[str, dict]] = []

    if any(kw in lower_msg for kw in ["同步", "sync", "刷新", "更新数据"]):
        tool_calls_plan.append(("sync_now", {"kind": "all"}))
    if any(kw in lower_msg for kw in ["账户", "资产", "净值", "现金", "概览"]):
        tool_calls_plan.append(("get_account", {}))
    if any(kw in lower_msg for kw in ["持仓", "仓位", "股票", "持有"]):
        tool_calls_plan.append(("list_positions", {}))
    if any(kw in lower_msg for kw in ["成交", "交易记录", "买卖"]):
        tool_calls_plan.append(("list_executions", {"limit": 10}))
    if any(kw in lower_msg for kw in ["订单", "委托"]):
        tool_calls_plan.append(("list_orders", {}))
    if any(kw in lower_msg for kw in ["盈亏", "收益", "亏损", "赚", "亏"]):
        tool_calls_plan.append(("get_pnl_summary", {"group_by": "symbol"}))
    if any(kw in lower_msg for kw in ["分析", "体检", "集中度", "敞口", "结构", "分布"]):
        tool_calls_plan.append(("analyze_portfolio", {}))
    if any(kw in lower_msg for kw in ["报价", "价格", "行情", "实时"]):
        symbols = []
        if any(kw in lower_msg for kw in ["腾讯"]):
            symbols.append("700.HK")
        if any(kw in lower_msg for kw in ["苹果", "apple"]):
            symbols.append("AAPL.US")
        if any(kw in lower_msg for kw in ["美团"]):
            symbols.append("3690.HK")
        symbols = list(dict.fromkeys(symbols)) or ["700.HK", "AAPL.US"]
        tool_calls_plan.append(("get_quote", {"symbols": symbols}))

    if not tool_calls_plan:
        tool_calls_plan.append(("get_account", {}))
        tool_calls_plan.append(("list_positions", {}))

    tool_results = []
    for tool_name, tool_input in tool_calls_plan:
        yield _sse_event("tool_use", {"name": tool_name, "input": tool_input})
        await asyncio.sleep(0.2)
        result = execute_tool(tool_name, tool_input, db)
        tool_results.append((tool_name, result))
        yield _sse_event("tool_result", {"name": tool_name, "result": result})

    reply_parts = [f"📊 **AI Trading 助手**（Mock 模式，{date.today().isoformat()}）\n"]
    for tool_name, result in tool_results:
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            continue
        if tool_name == "get_account" and "error" not in data:
            reply_parts.append(f"\n💰 **账户概览** ({data.get('currency', 'HKD')})")
            reply_parts.append(f"  - 净资产: {data.get('net_assets', 0):,.2f}")
            reply_parts.append(f"  - 总市值: {data.get('market_value', 0):,.2f}")
            reply_parts.append(f"  - 现金: {data.get('total_cash', 0):,.2f}")
        elif tool_name == "list_positions" and isinstance(data, list) and data:
            reply_parts.append(f"\n📈 **当前持仓** ({len(data)} 只)")
            for pos in data:
                pnl = pos.get("unrealized_pnl", 0)
                ratio = pos.get("unrealized_pnl_ratio", 0) * 100
                emoji = "🟢" if pnl >= 0 else "🔴"
                reply_parts.append(f"  {emoji} {pos['symbol']} | 盈亏: {pnl:+,.2f} ({ratio:+.2f}%)")

    reply_parts.append("\n---\n_⚠️ Mock 模式。配置 API Key 后可使用真实 AI 对话。_")
    yield _sse_event("text", {"content": "\n".join(reply_parts)})
    yield _sse_event("done", {})


def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"