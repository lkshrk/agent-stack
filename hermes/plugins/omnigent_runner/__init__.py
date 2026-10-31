from agent_stack_hermes.runner import TOOL_SCHEMA, deny_kanban_terminal, handle


def register(ctx):
    ctx.register_tool("omnigent_run", "omnigent_runner", TOOL_SCHEMA, handle)
    ctx.register_hook("pre_tool_call", deny_kanban_terminal)
