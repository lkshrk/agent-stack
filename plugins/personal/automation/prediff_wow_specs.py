#!/usr/bin/env python3
"""Deterministic comparison of each spec file with the live guide it cites; decides what needs Claude."""
import argparse
import json
from pathlib import Path
import re
import sys

from check_wow_season_updates import active_bundle, refs

DEFAULT_CAP = 12
STAT_WORDS = {'critical strike': 'crit', 'crit': 'crit', 'haste': 'haste', 'mastery': 'mastery',
              'versatility': 'vers', 'versa': 'vers', 'vers': 'vers'}
PRIMARY = ('intellect', 'agility', 'strength', 'stamina')
STATW = '|'.join(sorted([*STAT_WORDS, *PRIMARY], key=len, reverse=True))
STAT_RUN = re.compile(rf'\b(?:{STATW})(?:[\s>=,/]+(?:{STATW}))+\b', re.I)
ITEM_ID = re.compile(r'\[item:(\d+)\]')


def stat_lines(text):
    lines = []
    for run in STAT_RUN.finditer(text or ''):
        tiers, joined = [], False
        for word, equals in re.findall(rf'({STATW})|(=)', run.group(0), re.I):
            if equals:
                joined = True
                continue
            stat = STAT_WORDS.get(word.casefold())
            if stat and any(stat in tier for tier in tiers):
                continue
            if stat and joined and tiers:
                tiers[-1].add(stat)
            elif stat:
                tiers.append({stat})
            joined = False
        if len(tiers) >= 2 and sum(map(len, tiers)) >= 3:
            lines.append('>'.join('='.join(sorted(t)) for t in tiers))
    return lines


def item_ids(text):
    return {int(i) for i in ITEM_ID.findall(text or '')}


def section_items(setup, section):
    return {int(k.split(':')[1]) for k in refs(setup.get('recommendations', {}).get(section, {})) if k.startswith('item:')}


SLOT_LABELS = [('health potions?', None), ('weapon oils?', None), ('heads?', ('enchants', 'head')),
               ('shoulders?', ('enchants', 'shoulders')), ('chest', ('enchants', 'chest')),
               ('boots|feet', ('enchants', 'feet')), ('legs?', ('enchants', 'legs')), ('rings?', ('enchants', 'rings')),
               ('weapons?', ('enchants', 'weapon')), ('food', ('consumables', 'food')),
               ('flasks?|phials?', ('consumables', 'flask')), ('potions?', ('consumables', 'combat_potion')),
               ('augment runes?|runes?', ('consumables', 'optional_augment_rune'))]
LABEL = re.compile(r'(?<![\w-])(?:' + '|'.join(f'(?P<l{i}>{p})' for i, (p, _) in enumerate(SLOT_LABELS)) + r'):', re.I)


def top_picks(extract):
    picks = {}
    for section in ('enchants', 'consumables'):
        text = extract.get(section, '')
        marks = list(LABEL.finditer(text))
        for number, mark in enumerate(marks):
            slot = SLOT_LABELS[int(mark.lastgroup[1:])][1]
            end = marks[number + 1].start() if number + 1 < len(marks) else len(text)
            first = ITEM_ID.search(text[mark.end():end])
            if slot and first and slot[0] == section:
                picks.setdefault(slot, int(first.group(1)))
    gem = ITEM_ID.search(extract.get('gems', ''))
    if gem:
        picks[('gems', 'unique')] = int(gem.group(1))
    return picks


def guide_urls(setup):
    return [s['url'] if isinstance(s, dict) else s for s in setup['sources']]


ITEM_SECTIONS = ('enchants', 'gems', 'consumables')


def missing_parts(extract):
    missing = [s for s in ITEM_SECTIONS if not item_ids(extract.get(s))]
    return (['stats'] if not stat_lines(extract.get('stats')) else []) + missing


def live_guides(setup, sources, complete=True):
    return {u: sources[u] for u in guide_urls(setup)
            if sources.get(u, {}).get('kind') == 'guide' and not sources[u].get('error')
            and set(ITEM_SECTIONS) & set(sources[u].get('extract', {}))
            and (not complete or not missing_parts(sources[u]['extract']))}


def diff_spec(setup, guides):
    live = {s: set() for s in ITEM_SECTIONS}
    live_stats = []
    for guide in guides.values():
        extract = guide['extract']
        for section in ITEM_SECTIONS:
            live[section] |= item_ids(extract.get(section))
        found = stat_lines(extract.get('stats'))
        if found:
            live_stats.append(found[0])
    spec_stats = set(stat_lines(json.dumps(setup.get('stat_guidance', {}))))
    differences = {}
    picks = {}
    for guide in guides.values():
        picks.update(top_picks(guide['extract']))
    for (section, key), live_id in picks.items():
        ours = setup.get('recommendations', {}).get(section, {}).get(key)
        if isinstance(ours, str) and ours.startswith('item:') and int(ours[5:]) != live_id:
            differences.setdefault('top_pick_differs', {})[f'{section}.{key}'] = {'live': live_id, 'spec': int(ours[5:])}
    if section_items(setup, 'enchants') and not any(section == 'enchants' for section, _ in picks):
        differences['top_picks_unparsed'] = True
    removed = {s: sorted(section_items(setup, s) - live[s]) for s in ITEM_SECTIONS}
    removed = {s: ids for s, ids in removed.items() if ids}
    if removed:
        differences['items_no_longer_listed'] = removed
    if spec_stats:
        missing = [line for line in live_stats if not any(s == line or s.startswith(line + '>') for s in spec_stats)]
        if missing:
            differences['stat_priority'] = {'live': missing, 'spec': sorted(spec_stats)}
    return differences


def classify(documents, sources):
    result = {}
    for entry in documents['index.json']['specs']:
        setup = documents[entry['file']]
        guides = live_guides(setup, sources)
        info = {'guides': [{'url': u, 'patch': g.get('meta', {}).get('patch'), 'updated': g.get('meta', {}).get('updated')}
                           for u, g in guides.items()]}
        if not guides:
            partial = live_guides(setup, sources, complete=False)
            reason = {u: missing_parts(g['extract']) for u, g in partial.items()}
            result[entry['spec_id']] = ({**info, 'status': 'incomplete', 'differences': {},
                                         'reason': 'Guide layout incomplete: ' + json.dumps(reason)} if reason else
                                        {**info, 'status': 'unavailable', 'differences': {}, 'reason': 'No readable guide'})
            continue
        differences = diff_spec(setup, guides)
        result[entry['spec_id']] = {**info, 'status': 'changed' if differences else 'unchanged', 'differences': differences}
    return result


def needs_advance(setup, guides, baseline):
    for url, guide in guides.items():
        meta = guide.get('meta', {})
        stored = next((s for s in setup['sources'] if (s['url'] if isinstance(s, dict) else s) == url), None)
        if guide.get('sha256') != baseline.get(url, {}).get('sha256'):
            return True
        if meta.get('patch') and meta['patch'] != setup.get('verified_patch'):
            return True
        if meta.get('updated') and isinstance(stored, dict) and stored.get('updated') != meta['updated']:
            return True
    return False


def summarize(documents, observation, cap=DEFAULT_CAP, force=False):
    sources = observation['current']['sources']
    specs = classify(documents, sources)
    baseline = documents['source-watch.json'].get('sources', {})
    changed = sorted(i for i, v in specs.items() if v['status'] == 'changed')
    incomplete = sorted(i for i, v in specs.items() if v['status'] == 'incomplete')
    ordered = changed + incomplete + (sorted(i for i, v in specs.items() if v['status'] == 'unchanged') if force else [])
    selected = ordered[:cap]
    advance = sorted(e['spec_id'] for e in documents['index.json']['specs']
                     if specs[e['spec_id']]['status'] == 'unchanged' and e['spec_id'] not in selected
                     and needs_advance(documents[e['file']], live_guides(documents[e['file']], sources), baseline))
    return {'schema_version': 1, 'cap': cap, 'specs': {str(k): v for k, v in specs.items()},
            'changed': changed, 'incomplete': incomplete, 'selected': selected, 'deferred': ordered[cap:],
            'unchanged': sorted(i for i, v in specs.items() if v['status'] == 'unchanged'),
            'advance': advance,
            'unavailable': sorted(i for i, v in specs.items() if v['status'] == 'unavailable')}


def load_documents(bundle):
    docs = {}
    for name in ['index.json', 'catalog.json', 'source-watch.json']:
        docs[name] = json.loads((bundle / name).read_text())
    for entry in docs['index.json']['specs']:
        docs[entry['file']] = json.loads((bundle / entry['file']).read_text())
    return docs


def spec_task(documents, observation, spec_id, prediff, bundle_path):
    entry = next(e for e in documents['index.json']['specs'] if e['spec_id'] == spec_id)
    setup = documents[entry['file']]
    sources = observation['current']['sources']
    guides = live_guides(setup, sources, complete=False)
    records = documents['catalog.json']['records']
    live = set()
    for guide in guides.values():
        live |= item_ids(' '.join(guide['extract'].values()))
    wanted = sorted(refs(setup) | {f'item:{i}' for i in live})
    return {'bundle_dir': bundle_path, 'spec_id': spec_id, 'spec_file': entry['file'], 'spec': setup,
            'differences': prediff['specs'][str(spec_id)]['differences'],
            'catalog_records': {k: records[k] for k in wanted if k in records},
            'catalog_records_missing': [k for k in wanted if k not in records],
            'live_guides': {u: {'meta': g.get('meta'), 'sections': g['extract']} for u, g in guides.items()},
            'cross_check': {u: v['extract'] for u, v in sources.items()
                            if v.get('kind') == 'crosscheck' and spec_id in v.get('spec_ids', []) and v.get('extract')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', type=Path, required=True)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--cap', type=int, default=DEFAULT_CAP)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--out', type=Path, help='Write the classification JSON here')
    parser.add_argument('--task', type=int, help='Write the narrow task for one spec to --task-out')
    parser.add_argument('--task-out', type=Path)
    args = parser.parse_args()
    bundle = args.bundle or active_bundle()
    documents = load_documents(bundle)
    observation = json.loads(args.observation.read_text())
    prediff = summarize(documents, observation, args.cap, args.force)
    if args.task is not None:
        task = spec_task(documents, observation, args.task, prediff, str(bundle))
        args.task_out.write_text(json.dumps(task, indent=2))
        return 0
    text = json.dumps(prediff, indent=2)
    if args.out:
        args.out.write_text(text)
    print(json.dumps({k: v for k, v in prediff.items() if k != 'specs'}), file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
