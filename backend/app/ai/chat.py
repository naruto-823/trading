"""AI 对话循环 + SSE 流式输出

两条通道：
1. Anthropic 原生协议（优先，走 claude-opus-4-7 等）：native tool use
2. OpenAI 兼容协议（fallback，给 ideaLAB qwen 等用）：prompt-based tool calling
"""

import json
import re
from collections.abc import AsyncGenerator
from datetime import date

from anthropic import Anthropic
from openai import OpenAI
from sqlalchemy.orm import Session

from app.ai.tools import TOOL_DEFINITIONS, execute_tool
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

_BASE_RULES = """规则：
- 用中文简洁回答
- 必须先调用工具获取数据，不要编造数据
- 金额显示时注意货币单位（HKD/USD）
- 百分比保留两位小数
- 你是只读助手，能查询数据但无法下单或修改任何交易（这是技术限制，不是规则）
- 如果数据为空，建议用户先同步数据

用户就是账户本人，已签知情免责声明，你可以放心给方向性观点和建议：
- 用户问"该不该买/卖/加仓/减仓"时，结合持仓数据、盈亏、市场背景给出明确判断，可以具体到价位或事件
- 用户问"分析仓位"时，调用 analyze_portfolio 工具，除了客观数据，也可以基于集中度、敞口等给出主观判断（如"前 3 大占 65%，集中度偏高，建议分散到 5-7 只")
- 不要"投资有风险"这种万金油废话；要么不说，要么给具体方向
- 给完建议在结尾附一句："以上仅供个人参考，不构成正式投资建议。" """

# Anthropic 原生：tools 通过 SDK 传，不需要在 prompt 里描述
SYSTEM_PROMPT_NATIVE = "你是 AI Trading 助手，帮助用户查看和分析他们的长桥证券账户数据。\n\n当前日期：{today}\n\n" + _BASE_RULES

# OpenAI 兼容路径：tools 用 prompt 注入 + <tool_call> 标签解析
SYSTEM_PROMPT_PROMPT_BASED = """你是 AI Trading 助手，帮助用户查看和分析他们的长桥证券账户数据。

当前日期：{today}

{tool_descriptions}

工具调用方式：当你需要查询数据时，在回复中使用以下格式调用工具：
<tool_call>
{{"name": "工具名", "input": {{参数对象}}}}
</tool_call>

你可以在一次回复中调用多个工具。每个工具调用都用 <tool_call></tool_call> 包裹。
调用工具后，系统会返回结果，你再根据结果回答用户。

""" + _BASE_RULES

TOOL_CALL_PATTERN = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

def _get_ai_client() -> OpenAI:
    """获取 AI 客户端（OpenAI 兼容；只用 ai_* 配置，不 fallback 到 anthropic_*——
    anthropic_* 是给 briefing.py 的原生 Anthropic 协议用的，协议不同不能混。)"""
    return OpenAI(api_key=settings.ai_api_key, base_url=settings.ai_base_url or None)

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
    """流式对话，yield SSE 格式的事件。
    路由：anthropic_api_key 配置时走原生 tool use；否则走 OpenAI 兼容的 prompt-based；都没有就 mock。
    """
    today = date.today().isoformat()
    user_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    if settings.anthropic_api_key:
        async for event in _anthropic_tool_loop(user_messages, today, db):
            yield event
        return

    if settings.validate_ai():
        system_content = SYSTEM_PROMPT_PROMPT_BASED.format(
            today=today,
            tool_descriptions=TOOL_DESCRIPTIONS,
        )
        api_messages: list[dict] = [{"role": "system", "content": system_content}]
        api_messages.extend(user_messages)
        async for event in _prompt_tool_loop(_get_ai_client(), api_messages, db):
            yield event
        return

    async for event in _mock_chat(messages, db):
        yield event


async def _anthropic_tool_loop(
    user_messages: list[dict],
    today: str,
    db: Session,
) -> AsyncGenerator[str, None]:
    """Anthropic 原生 tool use 循环。每轮调一次 messages.create，处理 tool_use 块，回填 tool_result 再循环。"""
    client = Anthropic(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )
    system = SYSTEM_PROMPT_NATIVE.format(today=today)
    messages: list[dict] = list(user_messages)
    tool_call_count = 0

    while True:
        try:
            resp = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=4096,
                system=system,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )
        except Exception as exc:
            yield _sse_event("error", {"message": f"AI API 错误: {exc}"})
            return

        # 拆出 text + tool_use 块；text 立即吐给前端
        text_parts: list[str] = []
        tool_uses: list = []
        for block in resp.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_uses.append(block)
        text = "".join(text_parts).strip()
        if text:
            yield _sse_event("text", {"content": text})

        # 模型结束，或者本轮没有 tool_use：收尾
        if resp.stop_reason != "tool_use" or not tool_uses:
            break

        # 把 assistant 的完整 content（含 tool_use blocks）写回上下文，Anthropic 要求格式精确
        messages.append({
            "role": "assistant",
            "content": [_block_to_dict(b) for b in resp.content],
        })

        # 执行所有 tool_use，攒成一条 tool_result user message
        tool_results: list[dict] = []
        hit_limit = False
        for tu in tool_uses:
            tool_call_count += 1
            yield _sse_event("tool_use", {"name": tu.name, "input": tu.input})

            if tool_call_count > MAX_TOOL_CALLS_PER_TURN:
                result = json.dumps({"error": "单轮工具调用次数已达上限"}, ensure_ascii=False)
                hit_limit = True
            else:
                result = execute_tool(tu.name, tu.input, db)

            yield _sse_event("tool_result", {"name": tu.name, "result": result})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

        if hit_limit:
            yield _sse_event("text", {"content": "\n\n（已达到单轮工具调用上限）"})
            break

    yield _sse_event("done", {})


def _block_to_dict(block) -> dict:
    """Anthropic content block → 回传给 messages.create 的 dict 格式。"""
    btype = getattr(block, "type", "")
    if btype == "text":
        return {"type": "text", "text": block.text}
    if btype == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    # 其他类型不在我们当前流程里，保险起见原样回传字段
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {"type": btype}


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