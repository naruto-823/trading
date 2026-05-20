"""辩论评分人工评测 —— 非 CI,本地手跑

用法(在 backend/ 下):python scripts/eval_debate.py
从 event_notification 表取最近 5 条已评分快讯,各跑一次 run_debate,打印 verdict 对眼。
"""

from app.db import SessionLocal
from app.models.event_notification import EventNotification
from app.services.debate_scorer import build_position_context, run_debate


def main() -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(EventNotification)
            .filter(EventNotification.relevance_score.isnot(None))
            .order_by(EventNotification.notified_at.desc())
            .limit(5)
            .all()
        )
    finally:
        db.close()

    if not rows:
        print("event_notification 表无已评分快讯,跳过")
        return

    for ev in rows:
        triage = {
            "relevance": ev.relevance or "indirect",
            "score": ev.relevance_score or 0,
            "sentiment": ev.sentiment or "neutral",
            "direction": ev.direction or "neutral",
            "confidence": ev.confidence or 50,
            "affected_tickers": [ev.symbol] if ev.symbol else [],
            "reason": ev.relevance_reason or "",
            "model": "eval",
        }
        ctx = build_position_context(triage["affected_tickers"])
        print("=" * 70)
        print(f"快讯: {ev.title[:70]}")
        print(f"triage: score={triage['score']} dir={triage['direction']}")
        verdict = run_debate(ev.body, triage, ctx)
        print(f"辩论 verdict: score={verdict['score']} dir={verdict['direction']} "
              f"winning={verdict['winning_side']} model={verdict['model']}")
        print(f"  多: {verdict['bull_case'][:100]}")
        print(f"  空: {verdict['bear_case'][:100]}")
        print(f"  判官: {verdict['judge_reasoning'][:140]}")


if __name__ == "__main__":
    main()
