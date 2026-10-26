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
        self.assertEqual(found['items_no_longer_listed'], {'consumables': [241324]})
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

    def test_an_item_dropped_from_its_own_section_is_a_change_even_if_listed_elsewhere(self):
        docs = documents()
        docs['spec-265.json']['recommendations']['gems']['unique'] = 'item:244007'
        found = diff_spec(docs['spec-265.json'], {GUIDE: live()})
        self.assertEqual(found['items_no_longer_listed'], {'gems': [244007]})

    def test_unparsed_top_picks_cannot_look_unchanged(self):
        page = PAGE.replace('<strong>Head:</strong>', '<strong>Helm</strong>')
        for slot in ('Shoulders', 'Chest', 'Boots', 'Legs', 'Rings', 'Weapon'):
            page = page.replace(f'<strong>{slot}:</strong>', f'<strong>{slot}</strong>')
        found = diff_spec(documents()['spec-265.json'], {GUIDE: live(page)})
        self.assertTrue(found['top_picks_unparsed'])
        self.assertEqual(classify(documents(), {GUIDE: live(page)})[265]['status'], 'changed')

    def test_layout_variants_that_lose_a_part_are_never_unchanged(self):
        variants = {
            'gems section renamed': PAGE.replace('id="gems"><h2>Gems</h2>', 'id="jewels"><h2>Jewels</h2>'),
            'stat ranking reworded': PAGE.replace('Crit &gt; Haste = Mastery &gt; Versatility', 'Crit, then Haste and Mastery'),
            'consumables renamed': PAGE.replace('id="consumables"><h2>Consumables</h2>', 'id="extras"><h2>Extras</h2>'),
            'item links stripped': PAGE.replace('/item=', '/thing='),
        }
        for label, page in variants.items():
            with self.subTest(label):
                digest, extra = analyze('guide', page)
                sources = {GUIDE: {'kind': 'guide', 'spec_ids': [265], 'sha256': digest, **extra}}
                verdict = classify(documents(), sources)[265]
                self.assertNotEqual(verdict['status'], 'unchanged')
                self.assertTrue(verdict['status'] == 'changed' or verdict['status'] == 'incomplete')

    def test_guide_without_a_stat_ranking_goes_to_claude_after_real_diffs_before_force_picks(self):
        feral = PAGE.replace('Crit &gt; Haste = Mastery &gt; Versatility', 'sim your gear').replace('Haste = Crit &gt; Mastery &gt; Versatility', 'sim it')
        digest, extra = analyze('guide', feral)
        sources = {GUIDE: {'kind': 'guide', 'spec_ids': [265], 'sha256': digest, **extra}}
        docs = documents()
        verdict = classify(docs, sources)[265]
        self.assertEqual(verdict['status'], 'incomplete')
        self.assertIn('stats', verdict['reason'])
        for sid in (266, 267, 268):
            docs['index.json']['specs'].append({'spec_id': sid, 'file': f'spec-{sid}.json'})
            docs[f'spec-{sid}.json'] = {**copy.deepcopy(docs['spec-265.json']), 'spec_id': sid, 'sources': [{'url': 'https://m.test/' + str(sid)}]}
        docs['spec-268.json']['recommendations']['consumables']['flask'] = 'item:241324'
        for sid in (266, 268):
            sources[f'https://m.test/{sid}'] = live()
        summary = summarize(docs, {'current': {'sources': sources}}, cap=3, force=True)
        self.assertEqual(summary['selected'], [268, 265, 266])
        self.assertEqual(summary['incomplete'], [265])
        self.assertNotIn(265, summary['unavailable'])
        task = spec_task(docs, {'current': {'sources': sources}}, 265, summary, 'bundle')
        self.assertIn(GUIDE, task['live_guides'])
        self.assertEqual(classify(docs, {GUIDE: {'kind': 'guide', 'error': 'HTTP 403'}})[265]['status'], 'unavailable')

    def test_force_sends_unchanged_specs_to_claude_after_the_differing_ones_within_the_cap(self):
        docs = documents()
        for sid in (266, 267):
            docs['index.json']['specs'].append({'spec_id': sid, 'file': f'spec-{sid}.json'})
            docs[f'spec-{sid}.json'] = copy.deepcopy(docs['spec-265.json'])
            docs[f'spec-{sid}.json']['spec_id'] = sid
        docs['spec-267.json']['recommendations']['consumables']['flask'] = 'item:241324'
        observation = {'current': {'sources': {GUIDE: live()}}}
        normal = summarize(docs, observation, cap=2)
        self.assertEqual((normal['selected'], normal['deferred']), ([267], []))
        forced = summarize(docs, observation, cap=2, force=True)
        self.assertEqual((forced['selected'], forced['deferred']), ([267, 265], [266]))
        self.assertNotIn(265, forced['advance'])
        self.assertEqual(forced['advance'], [266])

    def test_task_carries_only_this_specs_context(self):
        docs = documents()
        docs['spec-265.json']['recommendations']['consumables']['flask'] = 'item:241324'
        observation = {'current': {'sources': {GUIDE: live()}}}
        task = spec_task(docs, observation, 265, summarize(docs, observation), 'bundle/dir')
        self.assertEqual(task['spec_id'], 265)
        self.assertEqual(task['differences']['items_no_longer_listed'], {'consumables': [241324]})
        self.assertIn('item:244007', task['catalog_records'])
        self.assertIn('item:241326', task['catalog_records_missing'])
        self.assertIn(GUIDE, task['live_guides'])
        json.dumps(task)


if __name__ == '__main__':
    unittest.main()
