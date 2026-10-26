def tool_call_event(name, arguments=None, state=None):
    return {
        "type": "tool_call",
        "target": name,
        "data": {"name": name, "arguments": arguments or {}},
        "context": {"turn_final": None},
        "session_state": state if state is not None else {},
        "llm_client": None,
    }


def assert_not_ask(test, decision):
    if decision is not None:
        test.assertNotEqual(decision.get("result"), "ASK")
