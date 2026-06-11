from types import SimpleNamespace
from unittest.mock import patch

from app.services import position_analysis as pa


def _pos(symbol, mv, currency="USD", qty=10):
    return SimpleNamespace(
        symbol=symbol, name=symbol, quantity=qty, cost_price=1.0,
        current_price=1.0, market_value=mv, currency=currency,
        unrealized_pnl=0.0, unrealized_pnl_ratio=0.0, day_pnl_ratio=0.0,
    )


def test_parse_option_parses_contract():
    o = pa._parse_option("MSFT260618C440000.US")
    assert o == {"underlying": "MSFT.US", "type": "call", "strike": 440.0, "expiry": "2026-06-18"}
    o2 = pa._parse_option("META260702P575000.US")
    assert o2["type"] == "put" and o2["strike"] == 575.0 and o2["underlying"] == "META.US"
    assert pa._parse_option("AAPL.US") is None  # 正股不是期权


def test_select_includes_all_positions_sorted_desc():
    account = SimpleNamespace(net_assets=1000.0)
    positions = [_pos("CCC.US", 50), _pos("AAA.US", 600), _pos("BBB.US", 300), _pos("DDD.US", 10)]
    with patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v):
        out = pa.select_positions_for_analysis(positions, account, db=None)
    # 全部保留(含占比 1% 的 DDD),按 |HKD 市值| 降序
    assert [p["symbol"] for p in out] == ["AAA.US", "BBB.US", "CCC.US", "DDD.US"]
    assert out[0]["占净资产%"] == 60.0


def test_select_keeps_and_parses_options():
    account = SimpleNamespace(net_assets=1000.0)
    positions = [_pos("AAA.US", 600), _pos("META260702P575000.US", 200, qty=-1)]
    with patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v):
        out = pa.select_positions_for_analysis(positions, account, db=None)
    syms = [p["symbol"] for p in out]
    assert "META260702P575000.US" in syms  # 期权不再被剔除
    opt = next(p for p in out if p["symbol"] == "META260702P575000.US")
    assert opt["是否期权"] is True
    assert opt["期权"]["标的"] == "META.US"
    assert opt["期权"]["方向"] == "put"
    assert opt["期权"]["持仓方向"] == "卖出short"  # qty<0


def test_distinct_underlyings_dedupes_option_to_stock():
    # 正股 GOOG.US 和 GOOG put 应归并到同一个标的 GOOG.US
    positions = [
        {"symbol": "GOOG.US", "name": "Alphabet", "是否期权": False},
        {"symbol": "GOOG260717P335000.US", "是否期权": True, "期权": {"标的": "GOOG.US"}},
        {"symbol": "AAPL.US", "name": "Apple", "是否期权": False},
    ]
    u = pa._distinct_underlyings(positions)
    assert list(u.keys()) == ["GOOG.US", "AAPL.US"]  # 去重,GOOG 只一条
    assert u["GOOG.US"] == "Alphabet"  # 正股名优先


def test_parse_analysis_json_plain():
    raw = '{"overall_stance": "持", "per_position": [], "alerts": ["a"], "summary": "s"}'
    out = pa._parse_analysis_json(raw)
    assert out["summary"] == "s"
    assert out["alerts"] == ["a"]


def test_parse_analysis_json_strips_code_fence():
    raw = '```json\n{"summary": "x", "alerts": [], "per_position": [], "overall_stance": "攻"}\n```'
    out = pa._parse_analysis_json(raw)
    assert out["summary"] == "x"
    assert out["overall_stance"] == "攻"


def test_parse_analysis_json_invalid_returns_degraded():
    out = pa._parse_analysis_json("not json at all")
    assert out["degraded"] is True
    assert "解析失败" in out["summary"]
    assert out["per_position"] == []


def test_call_ai_fail_soft_on_exception():
    account = SimpleNamespace(net_assets=1000.0, market_value=900.0,
                             total_cash=100.0, day_pnl=5.0, buy_power=200.0)
    heavy = [{"symbol": "AAA.US", "占净资产%": 60.0}]
    # 让 Anthropic client 构造即抛 → 命中 except 分支
    with patch.object(pa, "Anthropic", side_effect=RuntimeError("boom")):
        out = pa._call_ai(account, heavy, market_ctx={}, news_by_symbol={}, research="")
    assert out["degraded"] is True
    assert "降级" in out["summary"]


def test_build_push_contains_assets_summary_and_alerts():
    account = SimpleNamespace(net_assets=1234567.0, day_pnl=-8900.0)
    analysis = {"summary": "整体持有,MSFT 趋势完好", "alerts": ["NVDA 财报临近", "GOOG 反垄断进展"]}
    title, body = pa._build_push(analysis, account)
    assert "仓位体检" in title
    assert "1,234,567" in title          # 净资产带千分位
    assert "整体持有" in body             # summary 进正文
    assert "NVDA 财报临近" in body         # alert 前两条进正文
    assert "GOOG 反垄断进展" in body


from app.models.position_analysis_report import PositionAnalysisReport


def test_generate_persists_report_and_pushes_even_when_research_fails(db_session):
    account = SimpleNamespace(net_assets=1000.0, market_value=900.0,
                             total_cash=100.0, day_pnl=5.0, buy_power=200.0)
    positions = [_pos("AAA.US", 600)]
    analysis = {"overall_stance": "持", "per_position": [],
                "alerts": ["x"], "summary": "持有 AAA"}

    with patch.object(pa, "list_positions", return_value=positions), \
         patch.object(pa, "get_latest_account", return_value=account), \
         patch.object(pa.fx_service, "to_hkd", side_effect=lambda v, ccy, db=None: v), \
         patch.object(pa, "_collect_market_data", side_effect=RuntimeError("news down")), \
         patch.object(pa, "_gather_research_tavily", side_effect=RuntimeError("ws down")), \
         patch.object(pa, "_call_ai", return_value=analysis), \
         patch.object(pa, "send_bark", return_value={"ok": True, "detail": "ok"}) as mock_bark:
        out = pa.generate_hourly_analysis(db_session)

    # 报告落库
    rows = db_session.query(PositionAnalysisReport).all()
    assert len(rows) == 1
    assert rows[0].research_brief == ""        # 调研失败 → 空,但不崩
    assert rows[0].push_status == "sent"
    mock_bark.assert_called_once()
    assert out["summary"] == "持有 AAA"


def test_generate_no_positions_pushes_degraded(db_session):
    with patch.object(pa, "list_positions", return_value=[]), \
         patch.object(pa, "get_latest_account", return_value=None), \
         patch.object(pa, "send_bark", return_value={"ok": True, "detail": "ok"}) as mock_bark:
        out = pa.generate_hourly_analysis(db_session)
    rows = db_session.query(PositionAnalysisReport).all()
    assert len(rows) == 1
    assert rows[0].degraded is True
    mock_bark.assert_called_once()
    assert "暂无" in out["summary"]


def test_get_latest_report_returns_most_recent(db_session):
    from datetime import datetime, timedelta
    old = PositionAnalysisReport(generated_at=datetime.utcnow() - timedelta(hours=1), summary="旧")
    new = PositionAnalysisReport(generated_at=datetime.utcnow(), summary="新")
    db_session.add_all([old, new])
    db_session.commit()
    got = pa.get_latest_report(db_session)
    assert got["summary"] == "新"


# —— Tavily 深度调研(替代死掉的 Anthropic web_search) ——

def test_gather_research_tavily_no_key_returns_empty():
    with patch.object(pa.settings, "tavily_api_key", ""):
        assert pa._gather_research_tavily([{"symbol": "NVDA.US", "name": "Nvidia"}]) == ""


def test_gather_research_tavily_builds_brief_from_results():
    from unittest.mock import MagicMock
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"results": [
        {"title": "NVDA hits new high", "content": "Strong demand for AI chips drives rally."},
        {"title": "Analysts raise NVDA target", "content": "Price target lifted to 200."},
    ]}
    fake_client = MagicMock()
    fake_client.post.return_value = fake_resp
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    with patch.object(pa.settings, "tavily_api_key", "tvly-xxx"), \
         patch.object(pa.settings, "hourly_analysis_research_results", 4), \
         patch.object(pa.httpx, "Client", return_value=fake_client):
        brief = pa._gather_research_tavily([{"symbol": "NVDA.US", "name": "Nvidia"}])
    assert "【NVDA.US】" in brief
    assert "NVDA hits new high" in brief
    assert "AI chips" in brief


def test_gather_research_tavily_failsoft_skips_failed_symbol():
    from unittest.mock import MagicMock
    fake_client = MagicMock()
    fake_client.post.side_effect = RuntimeError("network down")
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    with patch.object(pa.settings, "tavily_api_key", "tvly-xxx"), \
         patch.object(pa.httpx, "Client", return_value=fake_client):
        brief = pa._gather_research_tavily([{"symbol": "NVDA.US", "name": "Nvidia"}])
    assert brief == ""  # 搜索失败 → 跳过该只,不抛


# —— 外部(Claude Code)报告 ingest ——

def test_ingest_external_report_persists_and_pushes(db_session):
    with patch.object(pa, "get_latest_account", return_value=SimpleNamespace(net_assets=100.0, day_pnl=1.0)), \
         patch.object(pa, "send_bark", return_value={"ok": True, "detail": "ok"}) as mock_bark:
        out = pa.ingest_external_report(
            db_session,
            summary="纳指走强,MSFT 加仓",
            report_markdown="# 详尽报告\n\n## MSFT\n深度分析...",
            alerts=["META 575P 本周到期"],
        )
    from app.models.position_analysis_report import PositionAnalysisReport
    rows = db_session.query(PositionAnalysisReport).all()
    assert len(rows) == 1
    assert out["push_status"] == "sent"
    assert out["analysis"]["report_markdown"].startswith("# 详尽报告")
    assert out["summary"] == "纳指走强,MSFT 加仓"
    mock_bark.assert_called_once()


def test_ingest_external_report_no_push(db_session):
    with patch.object(pa, "get_latest_account", return_value=None), \
         patch.object(pa, "send_bark") as mock_bark:
        out = pa.ingest_external_report(
            db_session, summary="s", report_markdown="md", push=False,
        )
    assert out["push_status"] == "skipped"
    mock_bark.assert_not_called()


def test_ingest_uses_bark_body_as_push_body(db_session):
    captured = {}
    def fake_bark(title, body, **kw):
        captured["body"] = body
        return {"ok": True, "detail": "ok"}
    with patch.object(pa, "get_latest_account", return_value=SimpleNamespace(net_assets=100.0, day_pnl=1.0)), \
         patch.object(pa, "send_bark", side_effect=fake_bark):
        pa.ingest_external_report(
            db_session, summary="短摘要",
            report_markdown="# 全文", bark_body="【总览】详细全文信息……",
        )
    assert captured["body"] == "【总览】详细全文信息……"  # 用 bark_body 而非短摘要
