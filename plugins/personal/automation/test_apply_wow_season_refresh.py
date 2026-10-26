import copy
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from apply_wow_season_refresh import drop_negligible, load_bundle, validate


def fixture():
    day = date.today().isoformat()
    guide = 'https://www.method.gg/guides/example'
    scope = {'schema_version': 1, 'game': 'retail', 'season': 'season-test', 'verified_at': day}
    old = {
        'index.json': {**scope, 'roster_source': 'https://www.raidbots.com/static/data/live/talents.json', 'spec_count': 1, 'catalogue_record_count': 1,
                       'specs': [{'spec_id': 265, 'file': 'spec-265.json'}]},
        'catalog.json': {**scope, 'records': {'item:1': {'item_id': 1, 'name': 'Gem',
                         'status': 'verified', 'sources': ['https://www.wowhead.com/item=1'],
                         'verified_at': day, 'identity_verified_at': day}}},
        'spec-265.json': {**scope, 'spec_id': 265, 'status': 'verified', 'verified_at': day,
                          'sources': [{'url': guide}], 'stat_guidance': {'baseline': 'Haste first'},
                          'recommendations': {'gems': {'default': 'item:1'}, 'enchants': {'default': 'item:1'}, 'consumables': {'default': 'item:1'}}},
        'source-watch.json': {'captured_at': 'old', 'sources': {guide: {'sha256': 'a' * 64, 'kind': 'guide', 'spec_ids': [265]}}}
    }
    observation = {'current': {'season': 'season-test', 'captured_at': 'new',
                              'sources': {guide: {'sha256': 'b' * 64, 'kind': 'guide', 'spec_ids': [265]}}}}
    result = {'status': 'complete', 'summary': 'Updated sourced stat guidance',
              'accepted_sources': [guide], 'unresolved_sources': []}
    return old, observation, result


class PublishChecks(unittest.TestCase):
    def replacement(self):
        old, observation, result = fixture()
        original = result['accepted_sources'][0]
        observation['current']['sources'][original]['error'] = 'HTTP 403'
        replacement = 'https://www.method.gg/guides/replacement'
        candidate = copy.deepcopy(old)
        candidate['spec-265.json']['sources'] = [replacement]
        candidate['spec-265.json']['stat_guidance']['baseline'] = 'Crit first'
        result.update(status='partial', accepted_sources=[replacement], unresolved_sources=[original])
        return old, candidate, observation, result, replacement

    @patch('apply_wow_season_refresh.observe')
    def test_new_replacement_is_independently_observed_and_published(self, observe):
        old, candidate, observation, result, url = self.replacement()
        evidence = {'kind': 'guide', 'spec_ids': [265], 'sha256': 'c' * 64}
        observe.return_value = (url, evidence)
        updated = validate(old, candidate, result, observation)
        observe.assert_called_once_with((url, {'kind': 'guide', 'spec_ids': [265]}))
        self.assertEqual(updated['source-watch.json']['sources'][url], evidence)
        self.assertEqual(updated['spec-265.json']['stat_guidance']['baseline'], 'Crit first')
        self.assertNotIn(url, observation['current']['sources'])
        self.assertNotIn(url, old['source-watch.json']['sources'])

    @patch('apply_wow_season_refresh.observe')
    def test_unavailable_replacement_keeps_old_data_and_fingerprints(self, observe):
        old, candidate, observation, result, url = self.replacement()
        baseline = copy.deepcopy(old)
        observe.return_value = (url, {'kind': 'guide', 'spec_ids': [265], 'error': 'HTTP 403'})
        with self.assertRaisesRegex(ValueError, 'own successfully checked guide'):
            validate(old, candidate, result, observation)
        self.assertEqual(old, baseline)
        self.assertNotIn(url, observation['current']['sources'])

    @patch('apply_wow_season_refresh.observe')
    def test_existing_guide_newly_cited_for_a_spec_is_rechecked(self, observe):
        old, candidate, observation, result, url = self.replacement()
        for docs in (old, candidate):
            docs['index.json']['spec_count'] = 2
            docs['index.json']['specs'].append({'spec_id': 266, 'file': 'spec-266.json'})
            docs['spec-266.json'] = {**copy.deepcopy(old['spec-265.json']), 'spec_id': 266, 'sources': [url]}
        observation['current']['sources'][url] = {'kind': 'guide', 'spec_ids': [266], 'sha256': 'c' * 64}
        evidence = {'kind': 'guide', 'spec_ids': [265, 266], 'sha256': 'c' * 64}
        observe.return_value = (url, evidence)
        updated = validate(old, candidate, result, observation)
        observe.assert_called_once_with((url, {'kind': 'guide', 'spec_ids': [265, 266]}))
        self.assertEqual(updated['spec-265.json']['stat_guidance']['baseline'], 'Crit first')
        self.assertEqual(observation['current']['sources'][url]['spec_ids'], [266])

    @patch('apply_wow_season_refresh.observe')
    def test_unrelated_acknowledgement_is_not_fetched(self, observe):
        old, observation, result = fixture()
        result['accepted_sources'] = ['https://example.test/unrelated']
        observation['current']['sources'][result['accepted_sources'][0]] = {
            'kind': 'guide', 'spec_ids': [265], 'sha256': 'c' * 64}
        with self.assertRaisesRegex(ValueError, 'unrelated source'):
            validate(old, old, result, observation)
        observe.assert_not_called()

    @patch('apply_wow_season_refresh.observe')
    def test_replacement_requires_public_https_before_fetch(self, observe):
        for url in ('http://example.test/guide', 'https://user:password@example.test/guide'):
            old, candidate, observation, result, _ = self.replacement()
            candidate['spec-265.json']['sources'] = [url]
            result['accepted_sources'] = [url]
            with self.assertRaisesRegex(ValueError, 'public HTTPS'):
                validate(old, candidate, result, observation)
        observe.assert_not_called()

    @patch('apply_wow_season_refresh.observe')
    def test_unaccepted_new_guide_is_not_fetched(self, observe):
        old, candidate, observation, result, url = self.replacement()
        result['accepted_sources'] = []
        with self.assertRaisesRegex(ValueError, 'source acknowledgement'):
            validate(old, candidate, result, observation)
        observe.assert_not_called()

    @patch('apply_wow_season_refresh.observe')
    def test_replacement_evidence_cannot_authorize_another_spec(self, observe):
        old, candidate, observation, result, url = self.replacement()
        for docs in (old, candidate):
            docs['index.json']['spec_count'] = 2
            docs['index.json']['specs'].append({'spec_id': 266, 'file': 'spec-266.json'})
            docs['spec-266.json'] = {**copy.deepcopy(old['spec-265.json']), 'spec_id': 266}
        candidate['spec-266.json']['stat_guidance']['baseline'] = 'Unsupported change'
        observe.return_value = (url, {'kind': 'guide', 'spec_ids': [265], 'sha256': 'c' * 64})
        with self.assertRaisesRegex(ValueError, 'spec-266.json'):
            validate(old, candidate, result, observation)
        observe.assert_called_once_with((url, {'kind': 'guide', 'spec_ids': [265]}))

    def test_real_recommendation_change_and_fingerprint_are_applied(self):
        old, observation, result = fixture()
        candidate = copy.deepcopy(old)
        candidate['spec-265.json']['stat_guidance']['baseline'] = 'Crit first'
        updated = validate(old, candidate, result, observation)
        self.assertEqual(updated['spec-265.json']['stat_guidance']['baseline'], 'Crit first')
        self.assertEqual(updated['source-watch.json']['captured_at'], 'new')
        self.assertEqual(old['spec-265.json']['stat_guidance']['baseline'], 'Haste first')

    def test_blocked_deleted_broken_and_wrong_season_rejected(self):
        old, observation, result = fixture()
        with self.assertRaises(ValueError):
            validate(old, old, {**result, 'status': 'blocked'}, observation)
        for mutation in [lambda d: d.pop('spec-265.json'),
                         lambda d: d['spec-265.json']['recommendations']['gems'].update(default='item:999'),
                         lambda d: d['index.json'].update(season='next-season'),
                         lambda d: d['source-watch.json'].update(captured_at='agent-approved')]:
            candidate = copy.deepcopy(old)
            mutation(candidate)
            with self.assertRaises(ValueError):
                validate(old, candidate, result, observation)

    def test_unavailable_source_cannot_be_accepted(self):
        old, observation, result = fixture()
        url = result['accepted_sources'][0]
        observation['current']['sources'][url] = {'error': 'HTTP 403'}
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            validate(old, old, result, observation)

    def test_unrelated_item_cannot_replace_blocked_spec_advice(self):
        old, observation, result = fixture()
        guide = result['accepted_sources'][0]
        observation['current']['sources'][guide] = {'error': 'HTTP 403', 'kind': 'guide', 'spec_ids': [265]}
        item = 'https://nether.wowhead.com/tooltip/item/1'
        observation['current']['sources'][item] = {'sha256': 'c' * 64, 'kind': 'tooltip', 'spec_ids': [265]}
        result.update(status='partial', accepted_sources=[item], unresolved_sources=[guide])
        candidate = copy.deepcopy(old)
        candidate['spec-265.json']['stat_guidance']['baseline'] = 'Invented replacement'
        with self.assertRaisesRegex(ValueError, 'own successfully checked guide'):
            validate(old, candidate, result, observation)

    def test_changed_item_requires_independent_identity_check(self):
        old, observation, result = fixture()
        candidate = copy.deepcopy(old)
        candidate['catalog.json']['records']['item:1']['effect'] = 'Updated sourced effect'
        with self.assertRaisesRegex(ValueError, 'identity not verified'):
            validate(old, candidate, result, observation)
        self.assertIn('catalog.json', validate(old, candidate, result, observation, {'item:1': {'name': 'Gem'}}))

    def test_unhandled_fingerprint_is_preserved(self):
        old, observation, result = fixture()
        result.update(status='partial', accepted_sources=[], unresolved_sources=result['accepted_sources'])
        updated = validate(old, old, result, observation)
        self.assertEqual(updated['source-watch.json'], old['source-watch.json'])

    def test_unresolved_note_can_be_added_without_replacing_advice(self):
        old, observation, result = fixture()
        result.update(status='partial', accepted_sources=[], unresolved_sources=result['accepted_sources'])
        candidate = copy.deepcopy(old)
        candidate['spec-265.json']['unresolved'] = ['Guide temporarily unavailable; prior advice retained.']
        updated = validate(old, candidate, result, observation)
        self.assertEqual(updated['spec-265.json']['stat_guidance'], old['spec-265.json']['stat_guidance'])
        self.assertEqual(updated['spec-265.json']['verified_at'], old['spec-265.json']['verified_at'])

    @patch('apply_wow_season_refresh.observe')
    def test_no_churn_when_source_is_unchanged(self, observe):
        old, observation, result = fixture()
        observation['current']['sources'] = copy.deepcopy(old['source-watch.json']['sources'])
        self.assertEqual(validate(old, old, result, observation), old)
        observe.assert_not_called()

    def test_unreferenced_fingerprints_do_not_trigger_endless_refreshes(self):
        old, observation, result = fixture()
        old['source-watch.json']['sources']['https://example.test/obsolete'] = {'sha256': 'c' * 64}
        updated = validate(old, old, result, observation)
        self.assertNotIn('https://example.test/obsolete', updated['source-watch.json']['sources'])

    def test_bookkeeping_only_changes_are_dropped_but_fingerprints_advance(self):
        old, observation, result = fixture()
        candidate = copy.deepcopy(old)
        candidate['spec-265.json']['verified_at'] = '2000-01-01'
        candidate['spec-265.json']['sources'] = [{'url': result['accepted_sources'][0], 'updated': '2000-01-01'}]
        candidate['spec-265.json']['stat_guidance']['baseline'] = ' haste  first. '
        candidate['catalog.json']['records']['item:1']['verified_at'] = '2000-01-01'
        candidate['catalog.json']['verified_at'] = '2000-01-01'
        kept = drop_negligible(old, validate(old, candidate, result, observation, {'item:1': {'name': 'Gem'}}))
        for name in ['spec-265.json', 'catalog.json', 'index.json']:
            self.assertEqual(kept[name], old[name])
        self.assertEqual(kept['source-watch.json']['captured_at'], 'new')

    def test_meaningful_change_keeps_its_bookkeeping(self):
        old, observation, result = fixture()
        candidate = copy.deepcopy(old)
        candidate['spec-265.json']['stat_guidance']['baseline'] = 'Haste > Crit'
        candidate['spec-265.json']['verified_at'] = '2000-01-01'
        kept = drop_negligible(old, validate(old, candidate, result, observation))
        self.assertEqual(kept['spec-265.json'], candidate['spec-265.json'])

    def test_only_unchanged_catalogue_records_are_reverted(self):
        old, observation, result = fixture()
        old['catalog.json']['records']['item:2'] = {**old['catalog.json']['records']['item:1'], 'item_id': 2}
        old['index.json']['catalogue_record_count'] = 2
        candidate = copy.deepcopy(old)
        candidate['catalog.json']['records']['item:1']['effect'] = 'New effect'
        candidate['catalog.json']['records']['item:2']['verified_at'] = '2000-01-01'
        kept = drop_negligible(old, validate(old, candidate, result, observation, {'item:1': {'name': 'Gem'}, 'item:2': {'name': 'Gem'}}))
        self.assertEqual(kept['catalog.json']['records']['item:1']['effect'], 'New effect')
        self.assertEqual(kept['catalog.json']['records']['item:2'], old['catalog.json']['records']['item:2'])

    def test_candidate_cannot_smuggle_code_or_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'evil.py').write_text('print("no")')
            with self.assertRaises(ValueError):
                load_bundle(root)
            (root / 'evil.py').unlink()
            (root / 'catalog.json').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                load_bundle(root)

    def test_cli_writes_actual_bundle_files(self):
        old, observation, result = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, candidate = root / 'base', root / 'candidate'
            base.mkdir(); candidate.mkdir()
            for name, doc in old.items():
                (base / name).write_text(json.dumps(doc))
                (candidate / name).write_text(json.dumps(doc))
            doc = copy.deepcopy(old['spec-265.json'])
            doc['stat_guidance']['baseline'] = 'Crit first'
            (candidate / 'spec-265.json').write_text(json.dumps(doc))
            run = self.run_cli(root, base, candidate, result, observation)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads((base / 'spec-265.json').read_text())['stat_guidance']['baseline'], 'Crit first')
            self.assertTrue(json.loads(run.stdout)['release'])

    def test_cli_fingerprint_only_refresh_is_not_a_release(self):
        old, observation, result = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, candidate = root / 'base', root / 'candidate'
            base.mkdir(); candidate.mkdir()
            for name, doc in old.items():
                (base / name).write_text(json.dumps(doc))
                (candidate / name).write_text(json.dumps(doc))
            doc = copy.deepcopy(old['spec-265.json'])
            doc['verified_at'] = '2000-01-01'
            (candidate / 'spec-265.json').write_text(json.dumps(doc))
            run = self.run_cli(root, base, candidate, result, observation)
            self.assertEqual(run.returncode, 0, run.stderr)
            output = json.loads(run.stdout)
            self.assertEqual((output['changed_files'], output['release']), (['source-watch.json'], False))
            self.assertEqual(json.loads((base / 'spec-265.json').read_text()), old['spec-265.json'])

    def run_cli(self, root, base, candidate, result, observation):
        (root / 'result.json').write_text(json.dumps(result))
        (root / 'observation.json').write_text(json.dumps(observation))
        return subprocess.run([sys.executable, str(Path(__file__).with_name('apply_wow_season_refresh.py')),
                               '--bundle', str(base), '--candidate', str(candidate),
                               '--result', str(root / 'result.json'), '--observation', str(root / 'observation.json')],
                              capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
