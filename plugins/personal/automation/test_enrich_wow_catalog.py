import copy
import json
from pathlib import Path
import unittest

from enrich_wow_catalog import enrich, parse_tooltip

FIXTURES = Path(__file__).parent / 'fixtures'


def tooltip(item):
    return json.loads((FIXTURES / f'tooltip-item-{item}.json').read_text())


def record(**changes):
    return {'item_id': 271887, 'name': 'Liquid Luster', 'category': 'consumable', 'effect': None, 'duration_seconds': None,
            'cooldown_seconds': None, 'quality_rank': None,
            'unknown_fields': ['effect', 'duration', 'quality rank', 'cooldown and sharing'], **changes}


class Enrichment(unittest.TestCase):
    def test_liquid_luster_effect_and_duration_are_parsed(self):
        found = parse_tooltip(tooltip(271887))
        self.assertIn('Lustrous Gleam', found['effect'])
        self.assertIn('Versatility by 420', found['effect'])
        self.assertEqual(found['duration_seconds'], 30)
        self.assertNotIn('cooldown_seconds', found)

    def test_hour_long_flask_with_item_cooldown(self):
        found = parse_tooltip(tooltip(241324))
        self.assertEqual((found['duration_seconds'], found['cooldown_seconds']), (3600, 3))

    def test_only_missing_fields_are_filled_and_unknowns_pruned(self):
        catalog = {'records': {'item:271887': record(effect='kept')}}
        changes = enrich(catalog, '2026-10-26', fetcher=lambda kind, ident: tooltip(271887))
        self.assertEqual(changes, {'item:271887': {'duration_seconds': 30}})
        filled = catalog['records']['item:271887']
        self.assertEqual(filled['effect'], 'kept')
        self.assertEqual(filled['unknown_fields'], ['effect', 'quality rank', 'cooldown and sharing'])

    def test_complete_records_are_not_fetched(self):
        done = record(effect='e', duration_seconds=1, cooldown_seconds=None, quality_rank=1)
        done['cooldown_seconds'] = 0
        calls = []
        enrich({'records': {'item:271887': done}}, 'd', fetcher=lambda *a: calls.append(a))
        self.assertEqual(calls, [])

    def test_wrong_identity_and_unavailable_tooltips_change_nothing(self):
        catalog = {'records': {'item:271887': record(name='Other Potion'), 'item:2': record(item_id=2)}}
        before = copy.deepcopy(catalog)

        def fetcher(kind, ident):
            if ident == '2':
                raise ValueError('down')
            return tooltip(271887)
        self.assertEqual(enrich(catalog, 'd', fetcher=fetcher), {})
        self.assertEqual(catalog, before)

    def test_enchants_take_the_effect_text_but_not_proc_durations(self):
        data = {'name': 'Enchant X', 'tooltip': '<span id="useText1">Use: Permanently enchants for 15 sec bursts.</span><!--useText:1-->'}
        catalog = {'records': {'item:5': record(item_id=5, name='Enchant X', category='enchant')}}
        self.assertEqual(list(enrich(catalog, 'd', fetcher=lambda *a: data)['item:5']), ['effect'])


if __name__ == '__main__':
    unittest.main()
