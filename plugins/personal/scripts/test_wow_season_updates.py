from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from check_wow_season_updates import capture, compare, exit_status, fingerprint, main, refs, report


class UpdateChecks(unittest.TestCase):
    def test_noise_does_not_change_guide_digest(self):
        body = '<h1>Guide</h1><p>Stat priority and consumables are explained in this maintained guide. ' + 'Useful guidance. ' * 15 + '</p>'
        self.assertEqual(fingerprint('guide', '<nav>noise</nav>' + body + '<footer>old</footer>'),
                         fingerprint('guide', '<nav>new</nav>' + body + '<script>random()</script><footer>new</footer>'))
        self.assertEqual(fingerprint('guide', body + '<p>Last modified 4 days ago</p>'),
                         fingerprint('guide', body + '<p>Last modified 11 days ago</p>'))

    def test_changed_advice_and_identity_links_are_detected(self):
        body = '<h1>Stats</h1><p>' + 'Useful stat guidance. ' * 15 + '</p>'
        self.assertNotEqual(fingerprint('guide', body + '<p>Haste first</p>'),
                            fingerprint('guide', body + '<p>Mastery first</p>'))
        self.assertNotEqual(fingerprint('guide', body + '<a href="https://www.wowhead.com/item=1">Gem</a>'),
                            fingerprint('guide', body + '<a href="https://www.wowhead.com/item=2">Gem</a>'))

    def test_access_challenge_is_not_a_valid_snapshot(self):
        with self.assertRaises(ValueError):
            fingerprint('guide', '<h1>Just a moment</h1><p>Please sign in</p>')

    def test_tooltip_change_and_cosmetic_noise(self):
        a = '{"name":"Gem","tooltip":"<b>+16 Haste</b>","icon":"old"}'
        b = '{"name":"Gem","tooltip":"<span>+16 Haste</span>","icon":"new"}'
        self.assertEqual(fingerprint('tooltip', a), fingerprint('tooltip', b))
        self.assertNotEqual(fingerprint('tooltip', a), fingerprint('tooltip', b.replace('+16', '+17')))

    def test_failures_never_count_as_unchanged(self):
        old = {'sources': {'u': {'sha256': 'a', 'spec_ids': [265]}}}
        now = {'sources': {'u': {'error': 'HTTP 403', 'spec_ids': [265]}}}
        self.assertEqual(compare(old, now)[0]['status'], 'unavailable')
        self.assertEqual(old['sources']['u']['sha256'], 'a')

    def test_new_and_changed_sources_need_review(self):
        old = {'sources': {'u': {'sha256': 'a'}, 'gone': {}}}
        now = {'sources': {'u': {'sha256': 'b'}, 'new': {'sha256': 'c'}}}
        self.assertEqual([r['status'] for r in compare(old, now)], ['changed', 'baseline-needed', 'no-longer-referenced'])

    def test_known_access_gap_stays_visible_without_constant_failure(self):
        old = {'sources': {'ok': {'sha256': 'a'}, 'blocked': {'error': 'HTTP 403'}}}
        now = {'sources': {'ok': {'sha256': 'a'}, 'blocked': {'error': 'HTTP 403'}}}
        rows = compare(old, now)
        self.assertEqual(rows[1]['status'], 'unavailable')
        self.assertTrue(rows[1]['known_unavailable'])
        self.assertEqual(exit_status(rows), 0)
        old['sources']['blocked']['sha256'] = 'last-good'
        self.assertEqual(exit_status(compare(old, now)), 2)
        self.assertEqual(exit_status([{'status': 'unavailable', 'known_unavailable': True}]), 2)

    def test_affected_specs_report_and_recursive_refs(self):
        self.assertEqual(refs({'a': ['item:1', {'override': 'spell:2'}]}), {'item:1', 'spell:2'})
        text = report([{'url': 'https://example.test', 'status': 'changed', 'spec_ids': [265]}],
                      {'season': 'season-test', 'captured_at': '2026-10-04'})
        self.assertIn('265', text)
        self.assertIn('were not modified', text)

    def test_report_explains_each_refresh_gate(self):
        cases = [
            ('unchanged', False, False, False, 'no actionable source drift'),
            ('unavailable', True, False, False, '1 known monitoring gaps'),
            ('changed', False, False, True, '1 changed fingerprints'),
            ('baseline-needed', False, False, True, '1 sources needing a baseline'),
            ('no-longer-referenced', False, False, True, '1 removed source references'),
            ('unavailable', False, False, True, '1 newly unavailable sources'),
            ('unchanged', False, True, True, 'force_refresh=true requests a recommendation recheck'),
        ]
        for status, known, force, should_run, reason in cases:
            with self.subTest(status=status, known=known, force=force):
                rows = [{'url': 'ok', 'status': 'unchanged', 'spec_ids': []},
                        {'url': 'other', 'status': status, 'known_unavailable': known, 'spec_ids': [265]}]
                text = report(rows, {'season': 'test', 'captured_at': 'now'}, force)
                self.assertIn('Source detection ran', text)
                self.assertIn('gate: ' + ('RUN' if should_run else 'SKIP'), text)
                self.assertIn(reason, text)
                self.assertIn(f'force_refresh={str(force).lower()}', text)
                self.assertIn('Manual dispatch alone does not force', text)
                self.assertEqual(exit_status(rows) != 0 or force, should_run)
        text = report([{'url': 'blocked', 'status': 'unavailable', 'known_unavailable': True,
                        'spec_ids': []}], {'season': 'test', 'captured_at': 'now'})
        self.assertIn('gate: RUN', text)
        self.assertIn('all sources unavailable', text)
        self.assertIn('retrieval attempted', text)

    def test_collection_progress_is_bounded_and_only_on_stderr(self):
        wanted = {f'https://example.test/{i}': {'kind': 'tooltip', 'spec_ids': []} for i in range(101)}

        def observation(pair):
            url, info = pair
            return url, {**info, **({'error': 'HTTP 403'} if url.endswith('/0') else {'sha256': 'digest'})}

        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / 'index.json').write_text('{"season":"test"}')
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch('check_wow_season_updates.targets', return_value=wanted), \
                    patch('check_wow_season_updates.observe', side_effect=observation), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                current = capture(bundle)
        self.assertEqual(stdout.getvalue(), '')
        lines = stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 11)
        self.assertIn('101 public sources', lines[0])
        self.assertIn('101/101 completed; 100 fingerprinted; 1 unavailable;', lines[-1])
        self.assertIn('s elapsed', lines[-1])
        self.assertNotIn('digest', stderr.getvalue())
        self.assertEqual(list(current['sources']), list(wanted))

    def test_json_and_snapshot_stdout_remain_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / 'index.json').write_text('{"season":"test"}')
            wanted = {'https://example.test': {'kind': 'tooltip', 'spec_ids': [265]}}
            for option in ['--json', '--snapshot']:
                with self.subTest(option=option):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with patch('sys.argv', ['check', '--bundle', directory, option]), \
                            patch('check_wow_season_updates.targets', return_value=wanted), \
                            patch('check_wow_season_updates.observe', return_value=(
                                'https://example.test', {'kind': 'tooltip', 'spec_ids': [265], 'sha256': 'a'})), \
                            redirect_stdout(stdout), redirect_stderr(stderr):
                        self.assertEqual(main(), 0)
                    data = json.loads(stdout.getvalue())
                    self.assertIn('Source detection started', stderr.getvalue())
                    if option == '--json':
                        self.assertEqual(set(data), {'schema_version', 'current', 'rows', 'requires_refresh'})
                        self.assertTrue(data['requires_refresh'])
                        self.assertIn('requires_refresh=true', stderr.getvalue())
                    else:
                        self.assertEqual(set(data), {'schema_version', 'captured_at', 'season', 'sources'})


if __name__ == '__main__':
    unittest.main()
