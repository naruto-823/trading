"""discord_news_worker 的消息处理 glue 测试。

验证:一条 bot「24h总结」进来 → 拆成多条 → 每条带正确 ticker 入 news_pipeline,
去重种子基于「ticker+正文」(防整点重叠刷屏)。浏览器循环/hook 属重 IO,
对标 jin10 不单测。
"""

from unittest.mock import patch

from app.workers import discord_news_worker as dw

SUMMARY = """24小时个股新闻总结
1. $QCOM 高通与Meta达成数据中心CPU合作。
2. $GOOGL 谷歌Gemini 3.5 Pro发布延期至7月。"""


def test_splits_and_routes_each_item_to_pipeline():
    with patch.object(dw, "process_news_item", return_value="deduped") as mock_pipe:
        dw._process_message_sync(SUMMARY)

    assert mock_pipe.call_count == 2
    # 每条都标 source=discord
    assert all(c.kwargs["source"] == "discord" for c in mock_pipe.call_args_list)
    # 第二条 ticker 应作为 known_tickers 传入
    assert mock_pipe.call_args_list[1].kwargs["known_tickers"] == ["GOOGL"]
    # 去重种子基于 ticker+正文,而非消息 id
    seed = mock_pipe.call_args_list[0].kwargs["dedup_seed"]
    assert seed.startswith("QCOM|")


def test_blank_message_routes_nothing():
    with patch.object(dw, "process_news_item") as mock_pipe:
        dw._process_message_sync("   ")
    mock_pipe.assert_not_called()
