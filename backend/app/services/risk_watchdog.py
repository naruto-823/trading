"""External watchdog state machine; contains no account data or credentials."""


def advance(previous: dict, status: str, send) -> dict:
    state = dict(previous)
    state["failures"] = 0 if status == "healthy" else state.get("failures", 0) + 1
    if status == "healthy" and state.get("alerted"):
        if send("🟢 持仓监控已恢复", "外部探针已确认采集与推送配置恢复正常；不代表已核对手机通知权限。"):
            state["alerted"] = False
    elif (
        status != "healthy"
        and state["failures"] >= 2
        and (not state.get("alerted") or (status == "down" and state.get("alerted_status") == "partial"))
    ):
        reason = (
            "账户采集正常，但行情或流水覆盖受限" if status == "partial" else "服务器、账户采集或推送通道未通过健康检查"
        )
        if send("🔴 持仓监控需要检查", reason + "。连续两次检查异常；请勿依赖当前自动告警覆盖。未自动交易。"):
            state["alerted"] = True
            state["alerted_status"] = status
    return state
