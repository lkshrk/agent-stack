#!/usr/bin/env python3
"""Build secret-free Codex action arguments for direct OpenAI or a Responses gateway."""
import json
import os
import sys
from urllib.parse import urlparse


def configuration(env):
    args = ['--config', 'sandbox_workspace_write.network_access=true',
            '--output-schema', '.github/codex/prompts/wow-refresh-result.schema.json']
    base_url = env.get('WOW_REFRESH_BASE_URL', '').strip()
    model = env.get('WOW_REFRESH_MODEL', '').strip()
    if any(c in model for c in '\r\n'):
        raise ValueError('WOW_REFRESH_MODEL must be a single model alias.')
    if not base_url:
        if not env.get('OPENAI_API_KEY', '').strip():
            raise ValueError('Configure OPENAI_API_KEY, or WOW_REFRESH_BASE_URL plus WOW_REFRESH_API_KEY and WOW_REFRESH_MODEL for a gateway.')
        return {'mode': 'openai', 'model': model, 'codex_args': args}
    parsed = urlparse(base_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c.isspace() for c in base_url)):
        raise ValueError('WOW_REFRESH_BASE_URL must be an HTTPS API base URL without credentials, query parameters or fragments.')
    try:
        parsed.port
    except ValueError:
        raise ValueError('WOW_REFRESH_BASE_URL has an invalid port.') from None
    base_url = base_url.rstrip('/')
    if parsed.path.rstrip('/').endswith(('/responses', '/chat/completions')):
        raise ValueError('Provide the API base URL (usually ending /v1), not a /responses or /chat/completions endpoint.')
    if not model:
        raise ValueError('Set WOW_REFRESH_MODEL to the model alias exposed by your gateway.')
    if not env.get('WOW_REFRESH_GATEWAY_KEY', '').strip():
        raise ValueError('Add the WOW_REFRESH_API_KEY Actions secret for the configured gateway; OpenAI credentials are not used as a fallback.')
    provider = {
        'model_provider': 'wow_refresh_gateway',
        'model_providers.wow_refresh_gateway.name': 'WoW refresh gateway',
        'model_providers.wow_refresh_gateway.base_url': base_url,
        'model_providers.wow_refresh_gateway.env_key': 'WOW_REFRESH_GATEWAY_KEY',
        'model_providers.wow_refresh_gateway.wire_api': 'responses',
        'model_providers.wow_refresh_gateway.requires_openai_auth': False,
        'model_providers.wow_refresh_gateway.supports_websockets': False,
    }
    for key, value in provider.items():
        args += ['--config', key + '=' + json.dumps(value)]
    return {'mode': 'gateway', 'model': model, 'codex_args': args}


def main():
    try:
        result = configuration(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    # Only mode/config references are emitted. API key values never enter arguments or output.
    print('mode=' + result['mode'])
    print('model=' + result['model'])
    print('codex_args=' + json.dumps(result['codex_args']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
