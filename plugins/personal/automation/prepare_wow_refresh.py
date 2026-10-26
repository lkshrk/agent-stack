#!/usr/bin/env python3
"""Validate the LiteLLM gateway settings and build secret-free Claude Code arguments."""
import json
import os
import shlex
import sys
from pathlib import Path
from urllib.parse import urlparse

SCHEMA = Path(__file__).with_name('prompts') / 'wow-refresh-result.schema.json'
TOOLS = 'Read,Write,Edit,Glob,Grep,Bash(bun:*),Bash(curl:*),Bash(python3:*),Bash(jq:*)'
DISALLOWED = 'Task,Agent,WebFetch,WebSearch'
MAX_TURNS = 32


def configuration(env):
    base_url = env.get('WOW_REFRESH_BASE_URL', '').strip()
    model = env.get('WOW_REFRESH_MODEL', '').strip()
    if not base_url or not model:
        raise ValueError('Set the WOW_REFRESH_BASE_URL and WOW_REFRESH_MODEL Actions variables.')
    if any(c.isspace() for c in model):
        raise ValueError('WOW_REFRESH_MODEL must be a single model alias.')
    parsed = urlparse(base_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c.isspace() for c in base_url)):
        raise ValueError('WOW_REFRESH_BASE_URL must be an HTTPS gateway URL without credentials, query parameters or fragments.')
    try:
        parsed.port
    except ValueError:
        raise ValueError('WOW_REFRESH_BASE_URL has an invalid port.') from None
    base_url = base_url.rstrip('/')
    if parsed.path.rstrip('/').endswith(('/messages', '/responses', '/chat/completions')):
        raise ValueError('Provide the gateway root, not an API endpoint.')
    if base_url.endswith('/v1'):
        base_url = base_url[:-3]
    if not env.get('WOW_REFRESH_API_KEY', '').strip():
        raise ValueError('Add the WOW_REFRESH_API_KEY Actions secret for the configured gateway.')
    schema = json.dumps(json.loads(SCHEMA.read_text()), separators=(',', ':'))
    args = ' '.join(['--model', shlex.quote(model), '--max-turns', str(MAX_TURNS),
                     '--allowedTools', shlex.quote(TOOLS),
                     '--disallowedTools', shlex.quote(DISALLOWED), '--json-schema', shlex.quote(schema)])
    return {'base_url': base_url, 'model': model, 'claude_args': args}


def main():
    try:
        result = configuration(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    for key in ('base_url', 'model', 'claude_args'):
        print(key + '=' + result[key])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
