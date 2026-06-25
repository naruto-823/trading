"""Discord「24小时个股新闻总结」拆条器 parse_news_summary 的单元测试。

bot 一条消息聚合 ~10 条 $TICKER 新闻;拆成单条才能逐条评分/去重。
重点:正确拆条数、提取 ticker、格式异常时 fail-soft 不崩不丢。
"""

from app.services.discord_parser import parse_news_summary

# 截图实采的真实消息(2026/06/24 16:01 纽约时间那条)
REAL_SUMMARY = """24小时个股新闻总结
1. $QCOM 高通与Meta达成数据中心CPU合作，Dragonfly C1000将于2028年下半年用于Meta服务器。
2. $BABA 阿里巴巴被 Anthropic 指控非法访问其 AI 模型，涉及知识产权争议
3. $BABA Anthropic指控阿里巴巴非法访问其AI模型
4. $LZ LZ宣布与 AI 搜索公司 Perplexity 达成合作协议
5. $SLV 白银价格跌50%，RSI超卖，历史数据显示低位买入时机。
6. $ALTO 特朗普政府推动15亿乙醇汽油全年销售政策，利好乙醇生产商
7. $LYV Live Nation CEO 在反垄断和解达成前一个月与特朗普总统会谈，涉及票务行业重大和解协议。
8. $GOOGL 谷歌Gemini 3.5 Pro发布延期至7月，目前在Antigravity和LMArena平台开放测试优化。
9. $QCOM 高通数据中心业务获Meta、微软采用，预计2027年Q1定制芯片业务开始贡献收入。
10. $CODX 特朗普将向国会申请超过14亿美元埃博拉病毒防控资金
个股新闻实时监控 · 06/24, 16:01 纽约时间"""


def test_splits_real_summary_into_ten_items():
    items = parse_news_summary(REAL_SUMMARY)
    assert len(items) == 10


def test_extracts_ticker_and_text():
    items = parse_news_summary(REAL_SUMMARY)
    assert items[0]["ticker"] == "QCOM"
    assert items[0]["text"].startswith("高通与Meta达成数据中心CPU合作")
    # 第 8 条 ticker 是 GOOGL(带 $ 前缀)
    assert items[7]["ticker"] == "GOOGL"


def test_fail_soft_degrades_unnumbered_message_to_single_item():
    # bot 格式偶尔变(没有编号列表)→ 不能丢,整条退化成一条
    items = parse_news_summary("$TSLA 特斯拉盘前大涨5%")
    assert len(items) == 1
    assert items[0]["ticker"] == "TSLA"
    assert "特斯拉盘前大涨" in items[0]["text"]


def test_empty_or_blank_returns_empty():
    # 空/纯空白不该退化成噪音条目
    assert parse_news_summary("") == []
    assert parse_news_summary("   \n  ") == []
