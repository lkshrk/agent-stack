"""Offline regression checks: python3 -m unittest discover -s skills/wow-spec/scripts."""
from collections import Counter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from export_wcl_talents import validate_event
from wcl_builds import default_difficulty, describe_groups, event_for_row, interleave_levels, key_scope, level_sources, metric_enum, name_key, pairs, recurring_options, run, select_rows, timestamp


def tree():
    return {'specId': 71, 'fullNodeOrder': [1, 2, 3, 4, 5], 'classNodes': [
        {'id': 1, 'type': 'choice', 'entries': [{'id': 11, 'name': 'A', 'maxRanks': 1}, {'id': 12, 'name': 'B', 'maxRanks': 1}]}],
        'specNodes': [{'id': 2, 'type': 'tiered', 'entries': [{'id': 21, 'name': 'Tier A', 'maxRanks': 1}, {'id': 22, 'name': 'Tier B', 'maxRanks': 2}]}],
        'heroNodes': [{'id': 3, 'type': 'single', 'subTreeId': 60, 'entries': [{'id': 31, 'name': 'Hero A', 'maxRanks': 1}]},
                      {'id': 4, 'type': 'single', 'subTreeId': 62, 'entries': [{'id': 41, 'name': 'Hero B', 'maxRanks': 1}]}],
        'subTreeNodes': [{'id': 5, 'type': 'subtree', 'entries': [{'id': 51, 'name': 'Tree A', 'traitSubTreeId': 60}, {'id': 52, 'name': 'Tree B', 'traitSubTreeId': 62}]}]}


def event(*entries):
    return {'specID': 71, 'talentTree': [{'nodeID': n, 'id': e, 'rank': r} for n, e, r in entries]}


def row(name='Player', server=1, key=11):
    return {'name': name, 'server': {'region': 'EU', 'id': server}, 'class': 'Warrior', 'spec': 'Arms',
            'startTime': 1000, 'hardModeLevel': key, 'report': {'code': 'example', 'fightID': 2},
            'talents': [{'talentID': 11, 'points': 1}]}


class ValidationTests(unittest.TestCase):
    def test_incompatible_row_is_excluded_and_sample_refilled(self):
        snapshot = {**tree(), 'className': 'Warrior', 'specName': 'Arms'}
        args = SimpleNamespace(encounter=9, zone=None, difficulty=4, class_name='Warrior', spec='Arms', spec_id=71,
                               since_ms=0, until_ms=2000, since='start', until='end', sample=50, pages=2,
                               min_key=None, max_key=None, bracket=None, partition=None, metric='dps',
                               reference='fixture', lua='unused')
        for bad_talents, budget in [([{'talentID': 999999, 'points': 1}], 2),
                                    ([{'talentID': 11, 'points': 2}], 2),
                                    ([{'talentID': 999999, 'points': 1}], 1)]:
            with self.subTest(talents=bad_talents, budget=budget):
                args.pages = budget
                valid = [row(f'Player{i}') for i in range(1, 50)]
                replacement = row('Player0')
                for item in valid + [replacement]:
                    item['talents'].append({'talentID': 51, 'points': 1})
                invalid = row('Player0')
                invalid['talents'] = bad_talents
                class FakeClient:
                    calls = 0
                    def query(self, query):
                        self.calls += 1
                        rows = valid + [invalid] if self.calls == 1 else [replacement]
                        return {'worldData': {'encounter': {'name': 'Fixture boss',
                                'zone': {'difficulties': [{'id': 4, 'name': 'Heroic'}]},
                                'characterRankings': {'rankings': rows, 'hasMorePages': self.calls == 1}}}}
                client = FakeClient()
                with patch('wcl_builds.tree_data', return_value=[snapshot]), \
                     patch('wcl_builds.event_for_row', return_value=({}, 'https://example.test/log')), \
                     patch('wcl_builds.export_event', return_value={'import_string': 'validated-fixture'}):
                    result = run(args, client)
                expected_sample = 50 if budget == 2 else 49
                self.assertEqual(client.calls, budget)
                self.assertEqual(result['sample'], expected_sample)
                self.assertEqual(result['excluded'], {'incompatible_talents': 1})
                self.assertEqual(result['considered_rows'], expected_sample + 1)
                self.assertEqual(result['builds'][0]['count'], expected_sample)

    def test_whole_zone_keeps_different_encounter_appearances(self):
        args = SimpleNamespace(zone=53, since_ms=0, until_ms=2000, class_name='Warrior', spec='Arms',
                               min_key=None, max_key=None, sample=50)
        a, b = row(), row()
        a['_encounter_id'], b['_encounter_id'] = 9, 10
        selected, excluded = {}, Counter()
        select_rows([a, b, a], args, selected, excluded, tree())
        self.assertEqual(len(selected), 2)
        self.assertEqual(len({identity[:3] for identity in selected}), 1)
        self.assertEqual(excluded['duplicate_character'], 1)

    def test_optional_difficulty_defaults(self):
        self.assertEqual(default_difficulty([{'id': 5, 'name': 'Mythic'}, {'id': 4, 'name': 'Heroic'}]), 4)
        self.assertEqual(default_difficulty([{'id': 10, 'name': 'Dungeon'}]), 10)
        self.assertEqual(default_difficulty([{'id': 3, 'name': 'Normal'}, {'id': 1, 'name': 'LFR'}]), 3)

    def test_key_target_uses_nearby_levels(self):
        for target, bounds in [(10, (8, 12)), (2, (2, 4)), (None, (None, None))]:
            args = SimpleNamespace(key=target, exact_key=None, key_spread=2, min_key=None, max_key=None, bracket=None)
            key_scope(args)
            self.assertEqual((args.min_key, args.max_key), bounds)

    def test_explicit_exact_or_range_preserved(self):
        args = SimpleNamespace(key=None, exact_key=10, key_spread=2, min_key=None, max_key=None, bracket=None)
        key_scope(args)
        self.assertEqual((args.min_key, args.max_key), (10, 10))
        args = SimpleNamespace(key=None, exact_key=None, key_spread=2, min_key=7, max_key=12, bracket=None)
        key_scope(args)
        self.assertEqual((args.min_key, args.max_key), (7, 12))

    def test_bracket_cannot_collapse_target_range(self):
        args = SimpleNamespace(key=10, exact_key=None, key_spread=2, min_key=None, max_key=None, bracket=10)
        with self.assertRaisesRegex(ValueError, 'single API bracket'):
            key_scope(args)

    def test_range_uses_all_levels_not_only_global_leaders(self):
        args = SimpleNamespace(key=10, exact_key=None, key_spread=2, min_key=None, max_key=None, bracket=None)
        key_scope(args)
        self.assertEqual(level_sources(args), [(9, 10), (8, 9), (10, 11), (7, 8), (11, 12)])
        self.assertEqual(interleave_levels([['10a', '10b'], ['9a'], ['11a', '11b']]), ['10a', '9a', '11a', '10b', '11b'])

    def test_provider_name_spacing(self):
        self.assertEqual(name_key('Beast Mastery'), name_key('BeastMastery'))
        self.assertNotEqual(name_key('Arms'), name_key('Fury'))

    def test_metric_names_are_api_enums(self):
        self.assertEqual(metric_enum('score'), 'playerscore')
        self.assertEqual(metric_enum('speed'), 'playerspeed')
        self.assertEqual(metric_enum('dps'), 'dps')

    def test_mixed_choices_independent_of_top_complete_builds(self):
        choices, options = recurring_options(tree(), {'Tree A': Counter({(11, 1): 6, (12, 1): 4, (21, 1): 4})}, {'Tree A': 10})
        self.assertEqual([o['count'] for o in choices[0]['options']], [6, 4])
        self.assertEqual(choices[0]['sample'], 10)
        self.assertEqual([(o['entry_id'], o['count']) for o in options], [(21, 4)])

    def test_partial_tiered_ranks(self):
        _, selected = validate_event(event((2, 21, 1), (2, 22, 1)), tree())
        self.assertEqual(selected, {21: 1, 22: 1})

    def test_invalid_tiered_gap(self):
        with self.assertRaisesRegex(ValueError, 'in order'):
            validate_event(event((2, 22, 1)), tree())

    def test_choices_duplicates_unknown_and_overrank(self):
        for value in [event((1, 11, 1), (1, 12, 1)), event((1, 11, 1), (1, 11, 1)),
                      event((99, 11, 1)), event((1, 11, 2))]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_event(value, tree())

    def test_hero_tree_conflicts(self):
        for value in [event((3, 31, 1), (4, 41, 1)), event((3, 31, 1), (5, 52, 1))]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_event(value, tree())

    def test_canonical_order_and_duplicate_rejection(self):
        a = [{'talentID': 11, 'points': 1}, {'talentID': 21, 'points': 1}]
        self.assertEqual(pairs(a), pairs(list(reversed(a))))
        with self.assertRaises(ValueError):
            pairs(a + a)

    def test_complete_builds_not_node_majorities(self):
        a, b = row('A'), row('B')
        b['talents'] = [{'talentID': 12, 'points': 1}]
        groups, _, counts = describe_groups([a, b, row('C')], tree())
        self.assertEqual([g['count'] for g in groups], [2, 1])
        self.assertEqual(groups[0]['key'], ((11, 1),))
        self.assertEqual(counts[(12, 1)], 1)

    def test_selection_identity_actual_key_and_missing_logs(self):
        args = SimpleNamespace(since_ms=0, until_ms=2000, class_name='Warrior', spec='Arms',
                               min_key=11, max_key=11, sample=50)
        selected, excluded = {}, Counter()
        anonymous = row(); anonymous.pop('server')
        unlogged = row(); unlogged['report'] = {}
        select_rows([row(), row(), row(server=2), row(key=10), anonymous, unlogged], args, selected, excluded, tree())
        self.assertEqual(len(selected), 2)
        self.assertEqual(excluded, {'duplicate_character': 1, 'outside_key_range': 1, 'missing_identity': 1, 'unlogged': 1})

    def test_timezone_offsets(self):
        self.assertEqual(timestamp('2026-10-04T10:00:00+02:00'), timestamp('2026-10-04T08:00:00Z'))

    def test_exact_fight_and_talent_mismatch(self):
        args = SimpleNamespace(encounter=9, difficulty=4, class_name='Warrior', spec_id=71,
                               min_key=None, max_key=None)
        report = {'startTime': 0, 'fights': [{'id': 2, 'encounterID': 9, 'difficulty': 4, 'startTime': 1000, 'endTime': 2000, 'friendlyPlayers': [3]}],
                  'masterData': {'actors': [{'id': 3, 'name': 'Player', 'type': 'Player', 'subType': 'Warrior'}]},
                  'events': {'data': [{'sourceID': 3, 'specID': 71, 'timestamp': 1001, 'fight': 2,
                                      'talentTree': [{'id': 11, 'rank': 1}]}], 'nextPageTimestamp': None}}
        class FakeClient:
            def query(self, _):
                return {'reportData': {'report': report}}
        self.assertEqual(event_for_row(FakeClient(), row(), args)[0]['sourceID'], 3)
        report['events']['data'][0]['talentTree'][0]['id'] = 12
        with self.assertRaisesRegex(ValueError, 'disagree'):
            event_for_row(FakeClient(), row(), args)
        report['fights'][0]['encounterID'] = 10
        with self.assertRaisesRegex(ValueError, 'content'):
            event_for_row(FakeClient(), row(), args)


if __name__ == '__main__':
    unittest.main()
