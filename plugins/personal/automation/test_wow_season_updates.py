from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from check_wow_season_updates import active_bundle, analyze, capture, classify_url, compare, exit_status, fingerprint, main, refs, report, targets


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
            (bundle / 'index.json').write_text('{"game":"retail","season":"test"}')
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
            (bundle / 'index.json').write_text('{"game":"retail","season":"test"}')
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
                        self.assertEqual(set(data), {'schema_version', 'captured_at', 'game', 'season', 'sources'})


FIXTURES = Path(__file__).parent / 'fixtures'


def fixture(name):
    return (FIXTURES / name).read_text()


class GuideFingerprints(unittest.TestCase):
    def test_header_patch_and_date_are_metadata_not_part_of_the_hash(self):
        page = fixture('method-affliction-warlock.html')
        bumped = page.replace('Patch 12.1.5', 'Patch 12.2').replace('13th Oct, 2026', '25th Oct, 2026')
        first, second = analyze('guide', page), analyze('guide', bumped)
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1]['meta'], {'patch': '12.1.5', 'updated': '2026-10-13'})
        self.assertEqual(second[1]['meta'], {'patch': '12.2', 'updated': '2026-10-25'})

    def test_sections_that_matter_change_the_hash(self):
        page = fixture('method-affliction-warlock.html')
        for old, new in [('item=241308', 'item=241309'), ('Crit &gt; Haste', 'Haste &gt; Crit'), ('item=244007', 'item=244008')]:
            with self.subTest(old=old):
                self.assertIn(old, page)
                self.assertNotEqual(fingerprint('guide', page), fingerprint('guide', page.replace(old, new, 1)))

    def test_race_and_footer_noise_is_ignored(self):
        page = fixture('method-affliction-warlock.html')
        self.assertEqual(fingerprint('guide', page), fingerprint('guide', page.replace('<footer>', '<footer>Posted 3 days ago ')))

    def test_extract_has_only_the_gear_sections_with_item_markers(self):
        extract = analyze('guide', fixture('method-affliction-warlock.html'))[1]['extract']
        self.assertEqual(set(extract), {'stats', 'enchants', 'gems', 'consumables'})
        self.assertIn('[item:244007]', extract['enchants'])
        self.assertIn('[item:241326]', extract['consumables'])

    def test_crosscheck_and_news_sources_are_recorded_without_triggering(self):
        murlok = analyze('crosscheck', fixture('murlok-affliction-warlock.html'))
        self.assertEqual(murlok[1]['extract']['stats'], 'Haste Critical Strike Mastery Versatility')
        self.assertIn('Enchant Helm', murlok[1]['extract']['enchants'])
        news = analyze('news', fixture('blizzard-news.html'))
        self.assertEqual(news[1]['extract']['announcements'], ['Hotfixes: October 23, 2026'])
        rows = compare({'sources': {'m': {'sha256': 'a', 'kind': 'crosscheck'}}}, {'sources': {'m': {'sha256': 'b', 'kind': 'crosscheck'}}})
        self.assertEqual(rows[0]['status'], 'informational')
        self.assertEqual(exit_status(rows), 0)
        with self.assertRaises(ValueError):
            analyze('news', '<html>no announcements</html>')

    def test_sources_are_classified_by_host(self):
        self.assertEqual(classify_url('https://murlok.io/warlock/affliction/m+'), 'crosscheck')
        self.assertEqual(classify_url('https://www.method.gg/guides/x'), 'guide')
        self.assertEqual(classify_url('https://nether.wowhead.com/tooltip/item/1'), 'tooltip')


class SeasonParameters(unittest.TestCase):
    def test_active_bundle_comes_from_the_pointer_file_or_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'current.json').write_text('{"game": "retail", "season": "season-x"}')
            self.assertEqual(active_bundle(root=root), root / 'retail' / 'season-x')
            self.assertEqual(active_bundle('classic', 'season-y', root=root), root / 'classic' / 'season-y')
            self.assertEqual(active_bundle(season='season-z', root=root), root / 'retail' / 'season-z')
            with self.assertRaises(ValueError):
                active_bundle('../etc', 'x', root=root)

    def test_shipped_pointer_names_an_existing_bundle(self):
        self.assertTrue((active_bundle() / 'index.json').is_file())

    def test_trigger_sources_come_from_the_index(self):
        docs = {'catalog.json': {'records': {}}, 'spec-1.json': {'sources': ['https://murlok.io/a/b/m+']},
                'index.json': {'specs': [{'spec_id': 1, 'file': 'spec-1.json'}], 'roster_source': 'https://r.test',
                               'trigger_sources': ['https://news.test/wow']}}
        found = targets(documents=docs)
        self.assertEqual(found['https://news.test/wow']['kind'], 'news')
        self.assertEqual(found['https://murlok.io/a/b/m+'], {'kind': 'crosscheck', 'spec_ids': [1]})

    def test_no_season_or_game_is_hardcoded_in_automation(self):
        for name in ['check_wow_season_updates.py', 'prediff_wow_specs.py', 'apply_wow_season_refresh.py',
                     'enrich_wow_catalog.py', 'prepare_wow_refresh.py', 'prompts/refresh-wow-season.md']:
            self.assertNotIn('season-mn-2', (Path(__file__).parent / name).read_text(), name)


if __name__ == '__main__':
    unittest.main()
