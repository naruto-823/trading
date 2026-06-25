"""Discord「24小时个股新闻总结」拆条器。

bot 一条消息把 ~10 条个股新闻聚合成编号列表(`1. $TICKER 正文`);
拆成单条 `{ticker, text}` 才能逐条走评分/去重管道。
"""

from __future__ import annotations

import re

# 形如 "1. $QCOM 高通与Meta..." 的编号条目;ticker 前的 $ 可有可无
_ITEM_RE = re.compile(r"^\s*\d+\.\s*\$?([A-Za-z]+)\s+(.+?)\s*$")
# 退化匹配:整条开头的 $TICKER + 正文(跨行)
_DEGRADE_RE = re.compile(r"^\$?([A-Za-z]+)\s+(.+)$", re.DOTALL)


def parse_news_summary(text: str) -> list[dict]:
    """把一条总结消息拆成 [{ticker, text}, ...]。

    正常路径:按编号列表逐条拆。格式异常(没有编号)时 fail-soft——
    整条退化成一条,不丢消息;退化时尽量提取开头的 ticker。
    """
    items: list[dict] = []
    for line in text.splitlines():
        m = _ITEM_RE.match(line)
        if m:
            items.append({"ticker": m.group(1).upper(), "text": m.group(2).strip()})

    if not items:
        stripped = text.strip()
        if stripped:
            dm = _DEGRADE_RE.match(stripped)
            if dm:
                items.append({"ticker": dm.group(1).upper(), "text": dm.group(2).strip()})
            else:
                items.append({"ticker": None, "text": stripped})
    return items
