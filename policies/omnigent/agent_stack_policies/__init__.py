POLICY_REGISTRY: list[dict] = [
    {
        "handler": "agent_stack_policies.spawn.max_dispatches_per_session",
        "kind": "factory",
        "name": "max_dispatches_per_session",
        "description": "Deny session dispatches beyond a per-session cap.",
        "params_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1},
                "dispatch_tools": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
    },
]
