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


def spec_items(setup):
    recommended = {int(k.split(':')[1]) for k in refs(setup.get('recommendations', {})) if k.startswith('item:')}
    return recommended


def guide_urls(setup):
    return [s['url'] if isinstance(s, dict) else s for s in setup['sources']]


def live_guides(setup, sources):
    return {u: sources[u] for u in guide_urls(setup)
            if sources.get(u, {}).get('kind') == 'guide' and not sources[u].get('error')
            and {'enchants', 'gems', 'consumables'} & set(sources[u].get('extract', {}))}


def diff_spec(setup, guides):
    recommended = spec_items(setup)
    live = set()
    live_stats = []
    for guide in guides.values():
        extract = guide['extract']
        live |= item_ids(' '.join(extract.get(k, '') for k in ('enchants', 'gems', 'consumables', 'gear')))
        found = stat_lines(extract.get('stats'))
        if found:
            live_stats.append(found[0])
    spec_stats = set(stat_lines(json.dumps(setup.get('stat_guidance', {}))))
    differences = {}
    removed = sorted(recommended - live)
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
            result[entry['spec_id']] = {**info, 'status': 'unavailable', 'differences': {}}
            continue
        differences = diff_spec(setup, guides)
        result[entry['spec_id']] = {**info, 'status': 'changed' if differences else 'unchanged', 'differences': differences}
    return result


def needs_advance(setup, guides, baseline, force=False):
    if force:
        return True
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
    advance = sorted(e['spec_id'] for e in documents['index.json']['specs']
                     if specs[e['spec_id']]['status'] == 'unchanged'
                     and needs_advance(documents[e['file']], live_guides(documents[e['file']], sources), baseline, force))
    return {'schema_version': 1, 'cap': cap, 'specs': {str(k): v for k, v in specs.items()},
            'changed': changed, 'selected': changed[:cap], 'deferred': changed[cap:],
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
    guides = live_guides(setup, sources)
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
