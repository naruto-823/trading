"""discord_news_worker 的消息处理 glue 测试。

验证按频道 kind 分派 → 拆条/清洗 → 每条入 news_pipeline,去重种子含 kind+ticker+正文
(防整点重叠刷屏 + 跨频道误撞)。浏览器循环/hook 属重 IO,对标 jin10 不单测。
fixture 取自 9223 真实 innerText。
"""

from unittest.mock import patch

from app.workers import discord_news_worker as dw

SUMMARY = """APP
24小时个股新闻总结
$QCOM 高通CFO宣布年收入目标1000亿美元
,
$GOOGL 谷歌Gemini 3.5 Pro发布延期至7月
,
个股新闻实时监控 • 06/24, 17:01 纽约时间
Add Reaction"""

KOL = """黄金蛙🐸
APP
4:15 PM
zerohedge (@zerohedge) ✧
印度从俄罗斯进口的石油即将创下新高
 原文链接
点击查看原推文"""


def test_summary_splits_and_routes_each_item():
    with patch.object(dw, "process_news_item", return_value="deduped") as mock_pipe:
        dw._process_message_sync(SUMMARY, "summary", "个股新闻")

    assert mock_pipe.call_count == 2
    assert all(c.kwargs["source"] == "discord" for c in mock_pipe.call_args_list)
    assert mock_pipe.call_args_list[1].kwargs["known_tickers"] == ["GOOGL"]
    # 去重种子含 kind 前缀
    assert mock_pipe.call_args_list[0].kwargs["dedup_seed"].startswith("summary|QCOM|")


def test_kol_routes_single_cleaned_item():
    with patch.object(dw, "process_news_item", return_value="sent") as mock_pipe:
        dw._process_message_sync(KOL, "kol", "推特大V")

    assert mock_pipe.call_count == 1
    call = mock_pipe.call_args_list[0]
    assert "zerohedge" in call.args[0]  # content 含作者
    assert call.kwargs["known_tickers"] is None  # KOL 无 ticker
    assert call.kwargs["dedup_seed"].startswith("kol|")


def test_blank_message_routes_nothing():
    with patch.object(dw, "process_news_item") as mock_pipe:
        dw._process_message_sync("   ", "summary", "个股新闻")
    mock_pipe.assert_not_called()
