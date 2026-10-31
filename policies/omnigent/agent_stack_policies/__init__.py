POLICY_REGISTRY: list[dict] = [
    {
        "handler": "agent_stack_policies.spawn.max_dispatches_per_session",
        "kind": "factory",
        "name": "max_dispatches_per_session",
        "description": (
            "Deny session dispatches beyond a per-session cap, and count a collection when a collect_tools "
            "tool_result carries a completed reply from collect_from, capped at the dispatches."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1},
                "dispatch_tools": {"type": "array", "items": {"type": "string"}},
                "collect_tools": {"type": "array", "items": {"type": "string"}},
                "collect_from": {"type": "string"},
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.paths.path_guard",
        "kind": "factory",
        "name": "path_guard",
        "description": "Deny writes, apply_patch changes and shell access to forbidden paths.",
        "params_schema": {
            "type": "object",
            "properties": {
                "deny": {"type": "array", "items": {"type": "string"}},
                "allow_only": {"type": "array", "items": {"type": "string"}},
                "exempt": {"type": "array", "items": {"type": "string"}},
                "startup_files": {"type": "array", "items": {"type": "string"}},
                "startup_home": {"type": "string"},
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
        "description": (
            "Deny PR merges, pushes to protected branches, tag and ref pushes, force-pushes and pushes "
            "without an explicit destination."
        ),
        "params_schema": {
            "type": "object",
            "properties": {"protected_branches": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.agents.no_nested_agents",
        "kind": "factory",
        "name": "no_nested_agents",
        "description": (
            "Deny shell commands that start an agent CLI non-interactively (claude -p, codex exec, "
            "omp -p, opencode run), apply_patch through the shell and scripts piped into a shell."
        ),
        "params_schema": {
            "type": "object",
            "properties": {"extra_commands": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.loop.detect_loop",
        "kind": "factory",
        "name": "detect_loop",
        "description": (
            "Deny a tool call repeated with identical arguments a threshold number of times "
            "back to back (consecutive repeats only); window bounds the stored history."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "window": {"type": "integer", "minimum": 2},
                "threshold": {"type": "integer", "minimum": 2},
                "exempt_tools": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.unattended.deny_on_ask",
        "kind": "factory",
        "name": "deny_on_ask",
        "description": (
            "Wrap another policy and turn its ASK verdicts into DENY, decoding JSON-string tool arguments; "
            "join_list_commands hands it list-form shell commands as one string."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "handler": {"type": "string"},
                "arguments": {"type": "object"},
                "join_list_commands": {"type": "boolean"},
            },
            "required": ["handler"],
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.vcs.docs_vcs",
        "kind": "factory",
        "name": "docs_vcs",
        "description": (
            "Allowlist git and gh in shell commands: read-only verbs, `git add` of doc paths, "
            "`git commit -m`, `git push origin HEAD:<branch>`, gh pr reads and read-only gh api."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "allow_paths": {"type": "array", "items": {"type": "string"}},
                "deny": {"type": "array", "items": {"type": "string"}},
                "exempt": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["allow_paths"],
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.readonly.read_only_shell",
        "kind": "factory",
        "name": "read_only_shell",
        "description": (
            "Allow only listed read commands in the shell (rg, cat, sed -n ranges, git read verbs and similar), "
            "with no environment assignments and no output redirection to files; exact_commands are allowed verbatim."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "extra_commands": {"type": "array", "items": {"type": "string"}},
                "exact_commands": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.readonly.git_verbs",
        "kind": "factory",
        "name": "git_verbs",
        "description": "Allow git in the shell only with read verbs plus extra_verbs (for example fetch, worktree).",
        "params_schema": {
            "type": "object",
            "properties": {"extra_verbs": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.process_secrets.no_process_secrets",
        "kind": "factory",
        "name": "no_process_secrets",
        "description": "Best-effort: deny shell commands that read /proc, the Codex home or the environment.",
        "params_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "handler": "agent_stack_policies.secret_files.no_secret_reads",
        "kind": "factory",
        "name": "no_secret_reads",
        "description": (
            "Best-effort: deny shell commands that mention secret_dir other than as `GH_TOKEN_FILE=<token_file>` "
            "right before gh, and gh auth, config, alias, extension and --web."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "secret_dir": {"type": "string"},
                "token_file": {"type": "string"},
            },
            "required": ["secret_dir", "token_file"],
            "additionalProperties": False,
        },
    },
    {
        "handler": "agent_stack_policies.result.result_contract",
        "kind": "factory",
        "name": "result_contract",
        "description": (
            "Require the final response to carry a valid bundle result block; with await_dispatches, "
            "a turn that ends while a dispatched sub-agent's result is uncollected may end without one."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["planner", "security", "documenter"]},
                "await_dispatches": {"type": "boolean"},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
    },
]
