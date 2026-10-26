import copy
import json
from pathlib import Path
import unittest

from check_wow_season_updates import analyze
from prediff_wow_specs import classify, diff_spec, needs_advance, spec_task, stat_lines, summarize, top_picks

GUIDE = 'https://www.method.gg/guides/affliction-warlock/stats-races-and-consumables'
PAGE = (Path(__file__).parent / 'fixtures/method-affliction-warlock.html').read_text()


def live(page=PAGE):
    digest, extra = analyze('guide', page)
    return {'kind': 'guide', 'spec_ids': [265], 'sha256': digest, **extra}


def documents(**overrides):
    setup = {'spec_id': 265, 'verified_patch': '12.1', 'verified_at': '2026-10-04',
             'sources': [{'url': GUIDE, 'updated': '2026-08-12'}],
             'stat_guidance': {'single_target': 'Method: Crit > Haste = Mastery > Versatility.'},
             'recommendations': {'enchants': {'head': 'item:244007'}, 'gems': {'unique': 'item:240967'},
                                 'consumables': {'flask': 'item:241326', 'potion': 'item:241308'}},
             **overrides}
    return {'index.json': {'specs': [{'spec_id': 265, 'file': 'spec-265.json'}]}, 'spec-265.json': setup,
            'catalog.json': {'records': {'item:244007': {'name': 'Enchant'}}},
            'source-watch.json': {'sources': {GUIDE: {'sha256': 'old', 'kind': 'guide'}}}}


class StatLines(unittest.TestCase):
    def test_formats_normalise_to_the_same_line(self):
        expected = ['crit>haste=mastery>vers']
        self.assertEqual(stat_lines('Crit > Haste = Mastery > Versatility'), expected)
        self.assertEqual(stat_lines('Critical Strike Mastery = Haste Versa')[0], 'crit>haste=mastery>vers')
        self.assertEqual(stat_lines('Intellect > Mastery > Haste = Crit > Versa'), ['mastery>crit=haste>vers'])
        self.assertEqual(stat_lines('Haste approximately Critical Strike; prefer Mastery'), [])


class PreDiff(unittest.TestCase):
    def test_matching_spec_is_unchanged_and_needs_no_claude(self):
        docs = documents()
        docs['spec-265.json']['recommendations']['consumables']['flask'] = 'item:241326'
        sources = {GUIDE: live()}
        self.assertEqual(diff_spec(docs['spec-265.json'], {GUIDE: sources[GUIDE]}), {})
        self.assertEqual(classify(docs, sources)[265]['status'], 'unchanged')

    def test_removed_item_and_stat_change_are_reported(self):
        docs = documents()
        docs['spec-265.json']['recommendations']['consumables']['flask'] = 'item:241324'
        docs['spec-265.json']['stat_guidance']['single_target'] = 'Haste > Crit > Mastery > Versatility'
        found = diff_spec(docs['spec-265.json'], {GUIDE: live()})
        self.assertEqual(found['items_no_longer_listed'], [241324])
        self.assertEqual(found['stat_priority']['live'], ['crit>haste=mastery>vers'])

    def test_top_listed_pick_per_slot_is_extracted_from_the_fixture(self):
        picks = top_picks(analyze('guide', PAGE)[1]['extract'])
        self.assertEqual(picks[('enchants', 'head')], 244007)
        self.assertEqual(picks[('enchants', 'shoulders')], 243991)
        self.assertEqual(picks[('enchants', 'feet')], 243953)
        self.assertEqual(picks[('consumables', 'food')], 242275)
        self.assertEqual(picks[('consumables', 'flask')], 241326)
        self.assertEqual(picks[('consumables', 'combat_potion')], 241308)
        self.assertEqual(picks[('gems', 'unique')], 240967)
        self.assertNotIn(('consumables', 'healing_potion'), picks)

    def test_a_different_top_pick_sends_the_spec_to_claude_even_if_the_old_item_is_still_listed(self):
        docs = documents()
        docs['spec-265.json']['recommendations']['consumables']['food'] = 'item:255846'
        found = diff_spec(docs['spec-265.json'], {GUIDE: live()})
        self.assertEqual(found['top_pick_differs'], {'consumables.food': {'live': 242275, 'spec': 255846}})
        self.assertNotIn('items_no_longer_listed', found)

    def test_slots_the_spec_does_not_track_are_not_compared(self):
        docs = documents()
        self.assertEqual(diff_spec(docs['spec-265.json'], {GUIDE: live()}), {})

    def test_prose_only_stat_guidance_is_not_compared(self):
        docs = documents(stat_guidance={'mplus': 'Haste approximately Critical Strike'})
        self.assertEqual(diff_spec(docs['spec-265.json'], {GUIDE: live()}), {})

    def test_guide_without_gear_sections_is_not_evidence(self):
        docs = documents()
        talents = {GUIDE: {'kind': 'guide', 'sha256': 'x', 'extract': {'body': 'talent text'}}}
        self.assertEqual(classify(docs, talents)[265]['status'], 'unavailable')
        self.assertEqual(classify(docs, {GUIDE: {'kind': 'guide', 'error': 'HTTP 403'}})[265]['status'], 'unavailable')

    def test_summary_caps_claude_runs_and_defers_the_rest(self):
        docs = documents()
        docs['spec-265.json']['recommendations']['consumables']['flask'] = 'item:241324'
        for sid in (266, 267, 268):
            docs['index.json']['specs'].append({'spec_id': sid, 'file': f'spec-{sid}.json'})
            docs[f'spec-{sid}.json'] = copy.deepcopy(docs['spec-265.json'])
            docs[f'spec-{sid}.json']['spec_id'] = sid
        summary = summarize(docs, {'current': {'sources': {GUIDE: live()}}}, cap=2)
        self.assertEqual(summary['changed'], [265, 266, 267, 268])
        self.assertEqual((summary['selected'], summary['deferred']), ([265, 266], [267, 268]))

    def test_unchanged_spec_advances_only_when_patch_date_or_fingerprint_moved(self):
        docs = documents()
        guides = {GUIDE: live()}
        self.assertTrue(needs_advance(docs['spec-265.json'], guides, docs['source-watch.json']['sources']))
        settled = copy.deepcopy(docs)
        settled['spec-265.json'].update(verified_patch='12.1.5')
        settled['spec-265.json']['sources'][0]['updated'] = '2026-10-13'
        baseline = {GUIDE: {'sha256': guides[GUIDE]['sha256']}}
        self.assertFalse(needs_advance(settled['spec-265.json'], guides, baseline))
        self.assertTrue(needs_advance(settled['spec-265.json'], guides, baseline, force=True))

    def test_task_carries_only_this_specs_context(self):
        docs = documents()
        docs['spec-265.json']['recommendations']['consumables']['flask'] = 'item:241324'
        observation = {'current': {'sources': {GUIDE: live()}}}
        task = spec_task(docs, observation, 265, summarize(docs, observation), 'bundle/dir')
        self.assertEqual(task['spec_id'], 265)
        self.assertEqual(task['differences']['items_no_longer_listed'], [241324])
        self.assertIn('item:244007', task['catalog_records'])
        self.assertIn('item:241326', task['catalog_records_missing'])
        self.assertIn(GUIDE, task['live_guides'])
        json.dumps(task)


if __name__ == '__main__':
    unittest.main()
