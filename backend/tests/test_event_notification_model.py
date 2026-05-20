from datetime import datetime

from app.models.event_notification import EventNotification


def test_event_notification_debate_json_roundtrips(db_session):
    ev = EventNotification(
        id="t1",
        event_hash="h1",
        notified_at=datetime.utcnow(),
        importance="high",
        title="测试快讯",
        body="正文",
        push_status="debating",
        debate_json='{"winning_side": "bull"}',
    )
    db_session.add(ev)
    db_session.commit()

    got = db_session.query(EventNotification).filter_by(id="t1").first()
    assert got.push_status == "debating"
    assert got.debate_json == '{"winning_side": "bull"}'
