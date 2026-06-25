"""Discord 消息拆条/清洗器测试。fixture 全部取自 9223 真实抓到的 innerText
(非截图渲染——真实文本没有 "1." 编号、裹着 bot名/时间/标题/reaction 噪音)。

两类频道,按 kind 分派:
- summary:「日报-个股新闻」每条 `$TICKER 正文`、逗号分隔、需剔噪音头尾
- kol:「推特大v推送」每条 = 一条 KOL 推文,需清掉黄金蛙/时间/结构标签
"""

from app.services.discord_parser import parse_kol_tweet, parse_message, parse_news_summary

# 「日报-个股新闻(24h滚动)」真实 innerText(2026/06/25 5:01 那条,10 条新闻)
REAL_SUMMARY = """APP
 —
5:01 AM
Thursday, June 25, 2026 at 5:01 AM
24小时个股新闻总结
$MU 美光科技披露供应协议客户构成：4家大型客户、3家中型客户及多家汽车行业小型客户。
,
$MU 美光CEO披露战略客户协议细节，2026-2030年覆盖20% DRAM和1/3 NAND产能，预计贡献超50%收入。
,
$QCOM 高通CFO宣布年收入目标1000亿美元
,
$LLY 礼来与Abbisko达成19亿美元研发合作协议，扩大药物研发范围
,
个股新闻实时监控 • 06/24, 17:01 纽约时间
1
1
1
Add Reaction"""

# 「推特大v推送(实时)」真实 innerText
REAL_KOL = """黄金蛙🐸
APP
 —
4:15 PM
Thursday, June 25, 2026 at 4:15 PM
zerohedge (@zerohedge) ✧
印度从俄罗斯进口的石油即将创下新高
 核心观点
印度从俄罗斯进口石油量将创历史新高。
 原文链接
点击查看原推文
KOL推文实时翻译•Today at 4:15 PM"""


# ---- summary:个股新闻拆条 ----

def test_summary_splits_only_dollar_lines():
    items = parse_news_summary(REAL_SUMMARY)
    assert len(items) == 4  # 只 4 条 $ 行,噪音(标题/时间/reaction)全剔掉


def test_summary_extracts_ticker_and_text():
    items = parse_news_summary(REAL_SUMMARY)
    assert items[0]["ticker"] == "MU"
    assert items[0]["text"].startswith("美光科技披露供应协议")
    assert items[2]["ticker"] == "QCOM"


def test_summary_drops_noise_lines():
    items = parse_news_summary(REAL_SUMMARY)
    texts = " ".join(it["text"] for it in items)
    assert "24小时个股新闻总结" not in texts
    assert "Add Reaction" not in texts
    assert "个股新闻实时监控" not in texts


# ---- kol:推文清洗 ----

def test_kol_returns_single_item():
    items = parse_kol_tweet(REAL_KOL)
    assert len(items) == 1


def test_kol_keeps_author_and_body_drops_noise():
    item = parse_kol_tweet(REAL_KOL)[0]
    assert "zerohedge" in item["text"]
    assert "印度从俄罗斯进口的石油" in item["text"]
    # 噪音清掉
    assert "黄金蛙" not in item["text"]
    assert "点击查看原推文" not in item["text"]
    assert "KOL推文实时翻译" not in item["text"]


# ---- dispatch ----

def test_parse_message_dispatches_by_kind():
    assert len(parse_message(REAL_SUMMARY, "summary")) == 4
    assert len(parse_message(REAL_KOL, "kol")) == 1
