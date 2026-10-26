import json
import unittest

from configure_wow_refresh_provider import configuration


class ProviderChecks(unittest.TestCase):
    def gateway(self, **changes):
        return {'WOW_REFRESH_BASE_URL': 'https://gateway.example/v1/',
                'WOW_REFRESH_MODEL': 'team/model-alias',
                'WOW_REFRESH_GATEWAY_KEY': 'not-a-real-gateway-secret', **changes}

    def test_direct_mode_keeps_existing_behaviour_without_leaking_key(self):
        result = configuration({'OPENAI_API_KEY': 'not-a-real-openai-secret'})
        self.assertEqual(result['mode'], 'openai')
        self.assertNotIn('not-a-real-openai-secret', json.dumps(result))

    def test_custom_gateway_is_selected_and_uses_responses(self):
        result = configuration(self.gateway())
        text = json.dumps(result)
        self.assertEqual(result['mode'], 'gateway')
        self.assertEqual(result['model'], 'team/model-alias')
        self.assertIn('wow_refresh_gateway', text)
        self.assertIn('https://gateway.example/v1', text)
        self.assertIn('responses', text)
        self.assertIn('WOW_REFRESH_GATEWAY_KEY', text)
        self.assertNotIn('not-a-real-gateway-secret', text)
        self.assertNotIn('api.openai.com', text)

    def test_gateway_does_not_fall_back_to_openai_credentials(self):
        with self.assertRaisesRegex(ValueError, 'not used as a fallback'):
            configuration(self.gateway(WOW_REFRESH_GATEWAY_KEY='', OPENAI_API_KEY='unused'))

    def test_gateway_requires_explicit_model_alias(self):
        with self.assertRaisesRegex(ValueError, 'model alias'):
            configuration(self.gateway(WOW_REFRESH_MODEL=''))

    def test_unsafe_or_incorrect_urls_are_rejected_without_echoing_them(self):
        for url in ['http://gateway.example/v1', 'https://user:private@gateway.example/v1',
                    'https://gateway.example/v1?key=private', 'https://gateway.example/v1#private',
                    'https://gateway.example/v1/responses', 'https://gateway.example/v1/chat/completions',
                    'https://gateway.example:bad/v1', 'https://gateway.example/\nprivate']:
            with self.subTest(url=url), self.assertRaises(ValueError) as error:
                configuration(self.gateway(WOW_REFRESH_BASE_URL=url))
            self.assertNotIn('private', str(error.exception))

    def test_provider_values_are_argument_values_not_shell_code(self):
        result = configuration(self.gateway(WOW_REFRESH_BASE_URL='https://gateway.example/a"b/v1'))
        value = next(a for a in result['codex_args'] if a.startswith('model_providers.wow_refresh_gateway.base_url='))
        self.assertEqual(json.loads(value.split('=', 1)[1]), 'https://gateway.example/a"b/v1')


if __name__ == '__main__':
    unittest.main()
