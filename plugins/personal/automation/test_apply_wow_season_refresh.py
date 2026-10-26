import copy
from datetime import date
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from apply_wow_season_refresh import drop_negligible, load_bundle, main as apply_main, merge_parts, validate
from check_wow_season_updates import analyze


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


LIVE_PAGE = (Path(__file__).parent / 'fixtures/method-affliction-warlock.html').read_text()
LIVE_GUIDE = 'https://www.method.gg/guides/affliction-warlock/stats-races-and-consumables'


def live_guide(url=LIVE_GUIDE):
    digest, extra = analyze('guide', LIVE_PAGE)
    return {'kind': 'guide', 'spec_ids': [265], 'sha256': digest, **extra}


def spec_fixture(flask='item:241326'):
    old, observation, result = fixture()
    scope = {k: old['spec-265.json'][k] for k in ['schema_version', 'game', 'season', 'verified_at', 'status']}
    setup = {**scope, 'spec_id': 265, 'verified_patch': '12.1', 'sources': [{'url': LIVE_GUIDE, 'updated': '2026-08-12'}],
             'stat_guidance': {'single_target': 'Method: Crit > Haste = Mastery > Versatility.'},
             'recommendations': {'gems': {'default': 'item:240967'}, 'enchants': {'head': 'item:244007'}, 'consumables': {'flask': flask}}}
    record = old['catalog.json']['records']['item:1']
    for key in ['item:241326', 'item:241324', 'item:240967', 'item:244007']:
        old['catalog.json']['records'][key] = {**record, 'item_id': int(key.split(':')[1]), 'name': 'Flask'}
    old['catalog.json']['records']['item:1'] = record
    old['index.json']['catalogue_record_count'] = 5
    old['spec-265.json'] = setup
    old['source-watch.json'] = {'captured_at': 'old', 'sources': {LIVE_GUIDE: {'kind': 'guide', 'spec_ids': [265], 'sha256': 'a' * 64}}}
    observation['current']['sources'] = {LIVE_GUIDE: live_guide()}
    return old, observation


class PipelineRuns(unittest.TestCase):
    def run_pipeline(self, old, observation, parts=(), fetched=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, parts_dir = root / 'base', root / 'parts'
            base.mkdir(); parts_dir.mkdir()
            for name, doc in old.items():
                (base / name).write_text(json.dumps(doc))
            for number, (result, edits) in enumerate(parts):
                folder = parts_dir / f'wow-spec-{result["spec_id"]}-{number}'
                (folder / 'candidate').mkdir(parents=True)
                for name, doc in old.items():
                    doc = edits.get(name, doc)
                    (folder / 'candidate' / name).write_text(json.dumps(doc))
                (folder / 'result.json').write_text(json.dumps(result))
            (root / 'observation.json').write_text(json.dumps(observation))
            argv = ['--bundle', str(base), '--parts', str(parts_dir), '--observation', str(root / 'observation.json'), '--no-enrich']
            with patch('apply_wow_season_refresh.fetch_guides', return_value=fetched if fetched is not None else observation['current']['sources']), \
                    patch('sys.stdout', new_callable=io.StringIO):
                output = apply_main(argv)
            return output, {n.name: json.loads(n.read_text()) for n in base.iterdir()}

    def part(self, outcome, **changes):
        return {'spec_id': 265, 'outcome': outcome, 'summary': 'checked', 'accepted_sources': [LIVE_GUIDE],
                'unresolved_sources': [], **changes}

    def test_unchanged_advice_advances_patch_date_and_fingerprint_without_claude(self):
        old, observation = spec_fixture()
        output, saved = self.run_pipeline(old, observation)
        self.assertEqual(output['advanced'], [265])
        self.assertFalse(output['release'])
        setup = saved['spec-265.json']
        self.assertEqual((setup['verified_patch'], setup['verified_at']), ('12.1.5', date.today().isoformat()))
        self.assertEqual(setup['sources'][0]['updated'], '2026-10-13')
        self.assertEqual(setup['recommendations'], old['spec-265.json']['recommendations'])
        self.assertEqual(saved['source-watch.json']['sources'][LIVE_GUIDE]['sha256'], observation['current']['sources'][LIVE_GUIDE]['sha256'])
        self.assertEqual(saved['source-watch.json']['sources'][LIVE_GUIDE]['meta']['patch'], '12.1.5')
        self.assertNotIn('extract', saved['source-watch.json']['sources'][LIVE_GUIDE])
        self.assertEqual(saved['index.json']['verified_patch'], '12.1.5')

    def test_a_spec_that_differs_from_its_guide_is_not_advanced_without_a_claude_result(self):
        old, observation = spec_fixture(flask='item:241324')
        output, saved = self.run_pipeline(old, observation)
        self.assertEqual((output['advanced'], output['changed_files']), ([], []))
        self.assertEqual(saved['spec-265.json'], old['spec-265.json'])

    def test_settled_spec_without_drift_makes_no_change(self):
        old, observation = spec_fixture()
        _, settled = self.run_pipeline(old, observation)
        output, _ = self.run_pipeline(settled, observation)
        self.assertEqual(output['changed_files'], [])

    def test_claude_update_is_applied_and_validated_per_spec(self):
        old, observation = spec_fixture(flask='item:241324')
        edited = copy.deepcopy(old['spec-265.json'])
        edited['recommendations']['consumables']['flask'] = 'item:241326'
        output, saved = self.run_pipeline(old, observation, [(self.part('updated'), {'spec-265.json': edited})])
        self.assertEqual(output['ai_updated'], [265])
        self.assertEqual(saved['spec-265.json']['verified_patch'], '12.1.5')
        self.assertEqual(saved['spec-265.json']['sources'][0]['updated'], '2026-10-13')
        self.assertTrue(output['release'])
        self.assertEqual(saved['spec-265.json']['recommendations']['consumables']['flask'], 'item:241326')

    def test_claude_unchanged_outcome_advances_verification_but_rejects_edits(self):
        old, observation = spec_fixture(flask='item:241324')
        output, saved = self.run_pipeline(old, observation, [(self.part('unchanged'), {})])
        self.assertEqual(output['advanced'], [265])
        self.assertEqual(saved['spec-265.json']['recommendations'], old['spec-265.json']['recommendations'])
        self.assertEqual(saved['spec-265.json']['verified_patch'], '12.1.5')
        sneaky = copy.deepcopy(old['spec-265.json'])
        sneaky['stat_guidance']['single_target'] = 'Haste first'
        output, saved = self.run_pipeline(old, observation, [(self.part('unchanged'), {'spec-265.json': sneaky})])
        self.assertIn('unchanged outcome must not edit', output['rejected']['265'])
        self.assertEqual(saved['spec-265.json']['recommendations'], old['spec-265.json']['recommendations'])
        self.assertEqual(saved['spec-265.json']['verified_patch'], '12.1')

    def test_unresolved_result_keeps_the_old_spec_and_fingerprint(self):
        old, observation = spec_fixture(flask='item:241324')
        edited = copy.deepcopy(old['spec-265.json'])
        edited['stat_guidance']['single_target'] = 'Guess'
        part = self.part('unresolved', accepted_sources=[], unresolved_sources=[LIVE_GUIDE])
        output, saved = self.run_pipeline(old, observation, [(part, {'spec-265.json': edited})])
        self.assertEqual(output['status'], 'partial')
        self.assertEqual(saved['spec-265.json'], old['spec-265.json'])
        self.assertEqual(saved['source-watch.json']['sources'][LIVE_GUIDE]['sha256'], 'a' * 64)
        self.assertEqual(saved['spec-265.json']['verified_patch'], '12.1')

    def test_a_run_cannot_edit_another_spec(self):
        old, observation = spec_fixture()
        other = copy.deepcopy(old['spec-265.json'])
        old['spec-266.json'] = {**other, 'spec_id': 266}
        old['index.json']['specs'].append({'spec_id': 266, 'file': 'spec-266.json'})
        old['index.json']['spec_count'] = 2
        tampered = {**old['spec-266.json'], 'stat_guidance': {'x': 'y'}}
        output, saved = self.run_pipeline(old, observation, [(self.part('updated'), {'spec-266.json': tampered})])
        self.assertIn('outside its scope', output['rejected']['265'])
        self.assertEqual(saved['spec-266.json']['stat_guidance'], old['spec-266.json']['stat_guidance'])

    def two_specs(self):
        old, observation = spec_fixture(flask='item:241324')
        old['spec-266.json'] = {**copy.deepcopy(old['spec-265.json']), 'spec_id': 266,
                                'recommendations': {**old['spec-265.json']['recommendations'],
                                                    'consumables': {'flask': 'item:241326'}}}
        old['index.json']['specs'].append({'spec_id': 266, 'file': 'spec-266.json'})
        old['index.json']['spec_count'] = 2
        observation['current']['sources'][LIVE_GUIDE]['spec_ids'] = [265, 266]
        return old, observation

    def test_one_rejected_part_does_not_block_other_updates_or_advances(self):
        old, observation = self.two_specs()
        fixed = copy.deepcopy(old['spec-265.json'])
        fixed['recommendations']['consumables']['flask'] = 'item:241326'
        bad = {**self.part('updated', spec_id=266), 'accepted_sources': [LIVE_GUIDE]}
        wrecked = {**old['index.json'], 'spec_count': 9}
        output, saved = self.run_pipeline(old, observation, [(self.part('updated'), {'spec-265.json': fixed}),
                                                             (bad, {'index.json': wrecked})])
        self.assertEqual(output['ai_updated'], [265])
        self.assertEqual(list(output['rejected']), ['266'])
        self.assertEqual(saved['spec-265.json']['recommendations']['consumables']['flask'], 'item:241326')
        self.assertTrue(any('rejected' in note for note in saved['spec-266.json']['unresolved']))
        self.assertEqual(saved['spec-266.json']['verified_patch'], '12.1')
        self.assertIn('266', output['summary'])

    def test_malformed_part_is_rejected_not_fatal(self):
        old, observation = self.two_specs()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'base').mkdir(); (root / 'parts' / 'wow-spec-266' / 'candidate').mkdir(parents=True)
            for name, doc in old.items():
                (root / 'base' / name).write_text(json.dumps(doc))
            (root / 'parts' / 'wow-spec-266' / 'candidate' / 'evil.py').write_text('x')
            (root / 'parts' / 'wow-spec-266' / 'result.json').write_text(json.dumps(self.part('updated', spec_id=266)))
            (root / 'observation.json').write_text(json.dumps(observation))
            with patch('apply_wow_season_refresh.fetch_guides', return_value=observation['current']['sources']), \
                    patch('sys.stdout', new_callable=io.StringIO):
                output = apply_main(['--bundle', str(root / 'base'), '--parts', str(root / 'parts'),
                                     '--observation', str(root / 'observation.json'), '--no-enrich'])
        self.assertIn('Unexpected candidate path', output['rejected']['266'])
        self.assertEqual(output['advanced'], [])

    def test_tooltip_lookup_failure_rejects_only_that_part(self):
        old, observation = spec_fixture(flask='item:241324')
        edited = copy.deepcopy(old)
        edited['catalog.json']['records']['item:999'] = {**old['catalog.json']['records']['item:1'], 'item_id': 999, 'name': 'New'}
        edited['spec-265.json']['recommendations']['consumables']['flask'] = 'item:999'
        edited['index.json']['catalogue_record_count'] = 6
        down = subprocess.CompletedProcess([], 22, '', 'curl: (22) 404')
        with patch('apply_wow_season_refresh.subprocess.run', return_value=down):
            output, saved = self.run_pipeline(old, observation, [(self.part('updated'), edited)])
        self.assertIn('Tooltip lookup failed', output['rejected']['265'])
        self.assertEqual(saved['spec-265.json']['recommendations'], old['spec-265.json']['recommendations'])
        self.assertNotIn('item:999', saved['catalog.json']['records'])

    def test_existing_catalogue_records_only_gain_enrichment_fields(self):
        old, observation = spec_fixture(flask='item:241324')
        old['catalog.json']['records']['item:241326']['effect'] = None
        proof = {'item:241326': {'name': 'Flask', 'url': 'https://nether.wowhead.com/tooltip/item/241326', 'sha256': 'f' * 64}}
        for label, edit, accepted in [('rename', {'name': 'Renamed'}, False), ('sources', {'sources': ['https://x.test/']}, False),
                                      ('fill', {'effect': 'Increases Haste'}, True)]:
            for outcome in ('updated', 'unchanged'):
                with self.subTest(label=label, outcome=outcome):
                    edited = copy.deepcopy(old)
                    edited['catalog.json']['records']['item:241326'].update(edit)
                    with patch('apply_wow_season_refresh.identity_checks', return_value=proof):
                        output, _ = self.run_pipeline(old, observation, [(self.part(outcome), edited)])
                    self.assertEqual('265' in output['rejected'], not accepted)
                    if not accepted:
                        self.assertIn('enrichment fields', output['rejected']['265'])

    def run_raw_parts(self, old, observation, raw):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'base').mkdir()
            for name, doc in old.items():
                (root / 'base' / name).write_text(json.dumps(doc))
            for folder, (result_text, edits) in raw.items():
                (root / 'parts' / folder / 'candidate').mkdir(parents=True)
                for name, doc in old.items():
                    (root / 'parts' / folder / 'candidate' / name).write_text(json.dumps(edits.get(name, doc)))
                (root / 'parts' / folder / 'result.json').write_text(result_text)
            (root / 'observation.json').write_text(json.dumps(observation))
            with patch('apply_wow_season_refresh.fetch_guides', return_value=observation['current']['sources']), \
                    patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
                return apply_main(['--bundle', str(root / 'base'), '--parts', str(root / 'parts'),
                                   '--observation', str(root / 'observation.json'), '--no-enrich'])

    def test_any_kind_of_malformed_part_is_rejected_without_aborting(self):
        old, observation = self.two_specs()
        broken = {'spec-266.json': ['not', 'a', 'dict']}
        output = self.run_raw_parts(old, observation, {
            'wow-spec-1': ('[]', {}),
            'wow-spec-2': ('not json', {}),
            'wow-spec-3': (json.dumps(self.part('updated', spec_id=266)), broken),
            'wow-spec-4': (json.dumps({'spec_id': 265, 'outcome': 'updated', 'summary': 5}), {}),
        })
        self.assertEqual(len(output['rejected']), 4)
        self.assertEqual(output['ai_updated'], [])

    def test_missing_curl_rejects_only_the_part(self):
        old, observation = spec_fixture(flask='item:241324')
        edited = copy.deepcopy(old)
        edited['catalog.json']['records']['item:999'] = {**old['catalog.json']['records']['item:1'], 'item_id': 999, 'name': 'New'}
        edited['index.json']['catalogue_record_count'] = 6
        with patch('apply_wow_season_refresh.subprocess.run', side_effect=FileNotFoundError('curl')):
            output, _ = self.run_pipeline(old, observation, [(self.part('updated'), edited)])
        self.assertIn('Tooltip lookup unavailable', output['rejected']['265'])

    def shared_record_parts(self, second_name):
        old, observation = self.two_specs()
        parts = []
        for sid, name in [(265, 'Shared Flask'), (266, second_name)]:
            edited = copy.deepcopy(old)
            edited['catalog.json']['records']['item:999'] = {**old['catalog.json']['records']['item:1'], 'item_id': 999, 'name': name}
            edited['index.json']['catalogue_record_count'] = 6
            edited[f'spec-{sid}.json']['recommendations']['consumables']['flask'] = 'item:999'
            parts.append((self.part('updated', spec_id=sid), edited))

        def proofs(old_docs, candidate):
            return {k: {'name': r['name'], 'url': 'https://nether.wowhead.com/tooltip/' + k.replace(':', '/'), 'sha256': 'f' * 64}
                    for k, r in candidate['catalog.json']['records'].items() if r != old_docs['catalog.json']['records'].get(k)}
        return old, observation, parts, proofs

    def test_conflicting_new_catalogue_records_reject_only_the_later_part(self):
        old, observation, parts, proofs = self.shared_record_parts('Other Flask')
        with patch('apply_wow_season_refresh.identity_checks', side_effect=proofs):
            output, saved = self.run_pipeline(old, observation, parts)
        self.assertEqual(output['ai_updated'], [265])
        self.assertIn('Conflicting catalogue edits', output['rejected']['266'])
        self.assertEqual(saved['catalog.json']['records']['item:999']['name'], 'Shared Flask')

    def test_identical_new_catalogue_records_from_two_parts_are_fine(self):
        old, observation, parts, proofs = self.shared_record_parts('Shared Flask')
        with patch('apply_wow_season_refresh.identity_checks', side_effect=proofs):
            output, saved = self.run_pipeline(old, observation, parts)
        self.assertEqual((output['ai_updated'], output['rejected']), ([265, 266], {}))
        self.assertEqual(saved['index.json']['catalogue_record_count'], 6)

    def test_a_second_result_for_the_same_spec_is_ignored(self):
        old, observation = spec_fixture(flask='item:241324')
        first = copy.deepcopy(old['spec-265.json'])
        first['recommendations']['consumables']['flask'] = 'item:241326'
        second = copy.deepcopy(first)
        second['recommendations']['consumables']['flask'] = 'item:240967'
        output, saved = self.run_pipeline(old, observation, [(self.part('updated'), {'spec-265.json': first}),
                                                             (self.part('updated'), {'spec-265.json': second})])
        self.assertEqual(saved['spec-265.json']['recommendations']['consumables']['flask'], 'item:241326')
        self.assertTrue(any('Duplicate result for spec 265' in note for note in output['notes']))

    def test_unresolved_results_contribute_no_accepted_sources(self):
        old, _ = spec_fixture()
        part = self.part('unresolved', unresolved_sources=[LIVE_GUIDE], accepted_sources=['https://other.test/'])
        _, result, _ = merge_parts(old, [(part, copy.deepcopy(old))])
        self.assertEqual(result['accepted_sources'], [])

    def test_unavailable_guide_leaves_the_spec_untouched(self):
        old, observation = spec_fixture()
        down = {LIVE_GUIDE: {'kind': 'guide', 'spec_ids': [265], 'error': 'HTTP 403'}}
        output, _ = self.run_pipeline(old, observation, fetched=down)
        self.assertEqual(output['changed_files'], [])

    def test_cross_check_baselines_only_move_with_a_real_change(self):
        old, observation = spec_fixture()
        old['spec-265.json']['sources'].append({'url': 'https://murlok.io/a/b/m+', 'updated': None})
        observation['current']['sources']['https://murlok.io/a/b/m+'] = {'kind': 'crosscheck', 'spec_ids': [265], 'sha256': 'c' * 64}
        _, saved = self.run_pipeline(old, observation)
        self.assertEqual(saved['source-watch.json']['sources']['https://murlok.io/a/b/m+']['sha256'], 'c' * 64)
        settled = copy.deepcopy(saved)
        observation['current']['sources']['https://murlok.io/a/b/m+']['sha256'] = 'd' * 64
        output, _ = self.run_pipeline(settled, observation)
        self.assertEqual(output['changed_files'], [])


if __name__ == '__main__':
    unittest.main()
