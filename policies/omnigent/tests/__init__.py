def tool_call_event(name, arguments=None, state=None):
    event = {"type": "tool_call", "data": {"name": name, "arguments": arguments or {}}}
    if state is not None:
        event["context"] = {"session_state": state}
    return event


def assert_not_ask(test, decision):
    if decision is not None:
        test.assertNotEqual(decision.get("result"), "ASK")
