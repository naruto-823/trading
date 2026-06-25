"""Discord 个股新闻 / KOL 推文消息解析器,按频道类型分派。

两类频道(均为黄金蛙 bot 发,innerText 裹着 bot名/时间/结构标签噪音):
- summary「日报-个股新闻」: 一条消息聚合多条 `$TICKER 正文`(逗号分隔),拆成单条
- kol「推特大v推送」: 一条消息 = 一条 KOL 推文,清掉噪音留「作者 + 正文」

parse_message(text, kind) 按 kind 分派到对应解析器。返回 [{ticker, text}, ...]。
"""

from __future__ import annotations

import re

# summary 频道:以 $TICKER 开头的新闻行(噪音行不以 $ 开头,自动跳过)
_DOLLAR_RE = re.compile(r"^\$([A-Za-z]+)\s+(.+)$")

# kol 频道:作者行形如 "zerohedge (@zerohedge) ✧"
_AUTHOR_RE = re.compile(r"@\w+")
# kol 正文里要剔除的结构标签/bot 噪音(按行首匹配)
_KOL_NOISE_PREFIXES = (
    "黄金蛙", "APP", "—", "原文链接", "点击查看原推文", "KOL推文实时翻译", "核心观点",
)


def parse_news_summary(text: str) -> list[dict]:
    """个股新闻:逐行抽取 `$TICKER 正文`,噪音行(标题/时间/reaction)自动剔除。"""
    items: list[dict] = []
    for raw in text.splitlines():
        m = _DOLLAR_RE.match(raw.strip())
        if m:
            items.append({"ticker": m.group(1).upper(), "text": m.group(2).strip()})
    return items


def parse_kol_tweet(text: str) -> list[dict]:
    """KOL 推文:清掉 bot名/时间/结构标签,留「作者 + 正文」,返回单条(无 ticker)。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    author: str | None = None
    body: list[str] = []
    found_author = False

    for line in lines:
        if not found_author:
            # 作者行(含 @handle)之前全是头部噪音(黄金蛙/APP/时间),跳过
            if _AUTHOR_RE.search(line):
                name = line.split("(@")[0].strip() or _AUTHOR_RE.search(line).group(0)
                author = name.replace("✧", "").strip()
                found_author = True
            continue
        # 作者行之后:剔除结构标签噪音,其余作正文
        if any(line.startswith(p) for p in _KOL_NOISE_PREFIXES):
            continue
        body.append(line)

    out = ((f"{author}: " if author else "") + " ".join(body)).strip()
    return [{"ticker": None, "text": out}] if out else []


def parse_message(text: str, kind: str) -> list[dict]:
    """按频道类型分派:kind="kol" 走推文清洗,否则走个股新闻拆条。"""
    if kind == "kol":
        return parse_kol_tweet(text)
    return parse_news_summary(text)
