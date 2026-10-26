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
    {
        "handler": "agent_stack_policies.paths.path_guard",
        "kind": "factory",
        "name": "path_guard",
        "description": "Deny writes and shell access to forbidden paths.",
        "params_schema": {
            "type": "object",
            "properties": {
                "deny": {"type": "array", "items": {"type": "string"}},
                "allow_only": {"type": "array", "items": {"type": "string"}},
                "exempt": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.tools.deny_tools",
        "kind": "factory",
        "name": "deny_tools",
        "description": "Deny tools whose name matches a glob pattern.",
        "params_schema": {
            "type": "object",
            "properties": {"patterns": {"type": "array", "items": {"type": "string"}}},
            "required": ["patterns"],
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.merge.no_merge",
        "kind": "factory",
        "name": "no_merge",
        "description": "Deny PR merges, pushes to protected branches and force-pushes.",
        "params_schema": {
            "type": "object",
            "properties": {"protected_branches": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.unattended.deny_on_ask",
        "kind": "factory",
        "name": "deny_on_ask",
        "description": "Wrap another policy and turn its ASK verdicts into DENY.",
        "params_schema": {
            "type": "object",
            "properties": {
                "handler": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["handler"],
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.result.result_contract",
        "kind": "factory",
        "name": "result_contract",
        "description": "Require the final response to carry a valid bundle result block.",
        "params_schema": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["planner", "security", "documenter"]}},
            "required": ["kind"],
            "additionalProperties": False,
        },
    },
]
