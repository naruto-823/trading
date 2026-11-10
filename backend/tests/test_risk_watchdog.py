from app.services.risk_watchdog import advance


def test_two_failures_alert_once_and_recovery_notifies_once():
    pushes = []

    def send(*args):
        pushes.append(args)
        return True

    state = advance({}, "down", send)
    assert not pushes
    state = advance(state, "down", send)
    assert len(pushes) == 1 and state["alerted"]
    state = advance(state, "down", send)
    assert len(pushes) == 1
    state = advance(state, "healthy", send)
    assert len(pushes) == 2 and not state["alerted"]
    advance(state, "healthy", send)
    assert len(pushes) == 2


def test_failed_notification_is_retried_and_false_recovery_is_avoided():
    state = advance({"failures": 1}, "partial", lambda *a: False)
    assert not state.get("alerted")
    state = advance(state, "healthy", lambda *a: (_ for _ in ()).throw(AssertionError("no recovery")))
    assert state["failures"] == 0
    state = advance({"failures": 1}, "down", lambda *a: True)
    state = advance(state, "healthy", lambda *a: False)
    assert state["alerted"]
    state = advance(state, "healthy", lambda *a: True)
    assert not state["alerted"]


def test_server_down_escalates_previous_partial_coverage_alert():
    pushes = []

    def send(*args):
        pushes.append(args)
        return True

    state = advance({"failures": 1}, "partial", send)
    assert len(pushes) == 1
    state = advance(state, "down", send)
    assert len(pushes) == 2 and state["alerted_status"] == "down"
