import json
import shlex
import unittest

from prepare_wow_refresh import configuration


class GatewayChecks(unittest.TestCase):
    def env(self, **changes):
        return {'WOW_REFRESH_BASE_URL': 'https://gateway.example/v1',
                'WOW_REFRESH_MODEL': 'team-model',
                'WOW_REFRESH_API_KEY': 'not-a-real-gateway-secret', **changes}

    def test_gateway_root_is_used_without_v1_and_key_is_not_emitted(self):
        for url in ['https://gateway.example/v1', 'https://gateway.example/v1/', 'https://gateway.example/', 'https://gateway.example']:
            with self.subTest(url=url):
                result = configuration(self.env(WOW_REFRESH_BASE_URL=url))
                self.assertEqual(result['base_url'], 'https://gateway.example')
                self.assertNotIn('not-a-real-gateway-secret', json.dumps(result))

    def test_claude_args_carry_model_tools_and_result_schema(self):
        words = shlex.split(configuration(self.env())['claude_args'])
        self.assertEqual(words[words.index('--model') + 1], 'team-model')
        self.assertIn('--max-turns', words)
        self.assertIn('Bash(bun:*)', words[words.index('--allowedTools') + 1])
        schema = json.loads(words[words.index('--json-schema') + 1])
        self.assertEqual(schema['required'], ['spec_id', 'outcome', 'summary', 'accepted_sources', 'unresolved_sources'])

    def test_run_is_narrow_and_cannot_spawn_agents(self):
        words = shlex.split(configuration(self.env())['claude_args'])
        self.assertLessEqual(int(words[words.index('--max-turns') + 1]), 20)
        allowed = words[words.index('--allowedTools') + 1].split(',')
        denied = words[words.index('--disallowedTools') + 1].split(',')
        self.assertFalse({'Task', 'Agent'} & set(allowed))
        self.assertTrue({'Task', 'Agent'} <= set(denied))

    def test_model_and_key_are_required_and_openai_credentials_are_ignored(self):
        with self.assertRaisesRegex(ValueError, 'WOW_REFRESH_MODEL'):
            configuration(self.env(WOW_REFRESH_MODEL=''))
        with self.assertRaisesRegex(ValueError, 'WOW_REFRESH_API_KEY'):
            configuration(self.env(WOW_REFRESH_API_KEY='', OPENAI_API_KEY='unused'))

    def test_model_cannot_inject_arguments(self):
        with self.assertRaisesRegex(ValueError, 'single model alias'):
            configuration(self.env(WOW_REFRESH_MODEL='a --dangerously-skip-permissions'))

    def test_unsafe_or_incorrect_urls_are_rejected_without_echoing_them(self):
        for url in ['http://gateway.example', 'https://user:private@gateway.example',
                    'https://gateway.example/?key=private', 'https://gateway.example/#private',
                    'https://gateway.example/v1/messages', 'https://gateway.example/v1/chat/completions',
                    'https://gateway.example:bad', 'https://gateway.example/\nprivate']:
            with self.subTest(url=url), self.assertRaises(ValueError) as error:
                configuration(self.env(WOW_REFRESH_BASE_URL=url))
            self.assertNotIn('private', str(error.exception))


if __name__ == '__main__':
    unittest.main()
