#!/usr/bin/env python3
"""Bounded public WCL build aggregation, exact-fight checks and validated exports."""
import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import json
from itertools import zip_longest
import os
import subprocess
import sys
import urllib.error
import urllib.request

from export_wcl_talents import export_event, tree_data, validate_event


class Client:
    def __init__(self, client_id, secret):
        if not client_id or not secret:
            raise ValueError('Missing configured Warcraft Logs client credentials')
        basic = base64.b64encode((client_id + ':' + secret).encode()).decode()
        token = self.request('https://www.warcraftlogs.com/oauth/token',
                             b'grant_type=client_credentials',
                             {'Authorization': 'Basic ' + basic,
                              'Content-Type': 'application/x-www-form-urlencoded'})
        self.token = token['access_token']

    @staticmethod
    def request(url, data, headers):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise ValueError('WCL HTTP ' + str(error.code) + '; stopped without automatic retries') from None
        except urllib.error.URLError:
            raise ValueError('WCL connection failed') from None

    def query(self, query):
        result = self.request('https://www.warcraftlogs.com/api/v2/client',
                              json.dumps({'query': query}).encode(),
                              {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        if result.get('errors'):
            raise ValueError('WCL query failed: ' + '; '.join(e['message'] for e in result['errors']))
        return result['data']


def credentials(args):
    if args.rbw_entry:
        command = ['rbw', 'get', '--raw', args.rbw_entry]
        if args.rbw_folder:
            command += ['--folder', args.rbw_folder]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise ValueError('Cannot read the designated rbw entry; check vault access/sync')
        fields = {f['name']: f.get('value', '') for f in json.loads(result.stdout).get('fields', [])}
        return fields.get('client_id'), fields.get('secret_id')
    return os.environ.get('WCL_CLIENT_ID'), os.environ.get('WCL_CLIENT_SECRET')


def timestamp(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp() * 1000


def name_key(text):
    return ''.join(c.lower() for c in text if c.isalnum())


def pairs(talents, id_key='talentID', rank_key='points'):
    result = []
    for talent in talents:
        entry, rank = talent[id_key], talent[rank_key]
        if type(entry) is not int or type(rank) is not int or entry <= 0 or rank <= 0:
            raise ValueError('Malformed talent entry/rank')
        result.append((entry, rank))
    if not result or len({entry for entry, rank in result}) != len(result):
        raise ValueError('Missing or duplicate talent entries')
    return tuple(sorted(result))


def talent_entries(snapshot):
    return {entry['id']: (node, entry)
            for section in ['classNodes', 'specNodes', 'heroNodes', 'subTreeNodes']
            for node in snapshot[section] for entry in node['entries']}


def validate_ranked_talents(key, snapshot, entries):
    if any(entry not in entries for entry, rank in key):
        raise ValueError('Unknown talent entry in current metadata')
    event = {'specID': snapshot['specId'], 'talentTree': [
        {'id': entry, 'rank': rank, 'nodeID': entries[entry][0]['id']} for entry, rank in key]}
    validate_event(event, snapshot)


def select_rows(rows, args, selected, exclusions, snapshot):
    entries = talent_entries(snapshot)
    for row in rows:
        if not args.since_ms <= row.get('startTime', 0) <= args.until_ms:
            exclusions['outside_window'] += 1
            continue
        if name_key(row.get('class', '')) != name_key(args.class_name) or name_key(row.get('spec', '')) != name_key(args.spec):
            exclusions['wrong_spec'] += 1
            continue
        if args.min_key is not None or args.max_key is not None:
            key = row.get('hardModeLevel')
            if type(key) is not int or key < 1:
                exclusions['unknown_key'] += 1
                continue
            if (args.min_key is not None and key < args.min_key) or (args.max_key is not None and key > args.max_key):
                exclusions['outside_key_range'] += 1
                continue
        if not row.get('report', {}).get('code') or not row['report'].get('fightID'):
            exclusions['unlogged'] += 1
            continue
        server = row.get('server') or {}
        if not row.get('name') or not all(server.get(k) for k in ['region', 'id']):
            exclusions['missing_identity'] += 1
            continue
        try:
            key = pairs(row.get('talents', []))
        except (ValueError, KeyError, TypeError):
            exclusions['invalid_talents'] += 1
            continue
        try:
            validate_ranked_talents(key, snapshot, entries)
        except ValueError:
            exclusions['incompatible_talents'] += 1
            continue
        identity = (server['region'], server['id'], row['name'].casefold())
        if getattr(args, 'zone', None) is not None:
            identity += (row['_encounter_id'],)
        if identity in selected:
            exclusions['duplicate_character'] += 1
            continue
        selected[identity] = row
        if len(selected) == args.sample:
            return True
    return False


def event_for_row(client, row, args):
    code, fight_id = row['report']['code'], row['report']['fightID']
    data = client.query('''{ reportData { report(code:%s) { startTime
        fights(fightIDs:[%d]) { id encounterID difficulty startTime endTime keystoneLevel friendlyPlayers }
        masterData { actors { id name type subType } }
        events(fightIDs:[%d],dataType:CombatantInfo,limit:1000) { data nextPageTimestamp }
        } } }''' % (json.dumps(code), fight_id, fight_id))['reportData']['report']
    if not data or len(data['fights']) != 1:
        raise ValueError('Missing exact report fight')
    fight = data['fights'][0]
    expected_encounter = row.get('_encounter_id', args.encounter)
    if fight['encounterID'] != expected_encounter or fight['difficulty'] != args.difficulty:
        raise ValueError('Report fight does not match requested content/difficulty')
    if abs(data['startTime'] + fight['startTime'] - row['startTime']) > 1:
        raise ValueError('Ranking timestamp does not match selected fight')
    if args.min_key is not None or args.max_key is not None:
        if fight.get('keystoneLevel') != row.get('hardModeLevel'):
            raise ValueError('Ranking key level does not match report fight')
    if data['events'].get('nextPageTimestamp'):
        raise ValueError('Combatant events truncated; refusing partial verification')
    actors = [a['id'] for a in data['masterData']['actors']
              if a['type'] == 'Player' and name_key(a['subType']) == name_key(args.class_name) and a['name'] == row['name']
              and a['id'] in fight['friendlyPlayers']]
    matching = [e for e in data['events']['data']
                if e.get('sourceID') in actors and e.get('specID') == args.spec_id
                and e.get('fight', fight_id) == fight_id
                and fight['startTime'] <= e['timestamp'] <= fight['endTime']]
    if not matching or len({e['sourceID'] for e in matching}) != 1:
        raise ValueError('Cannot uniquely resolve the ranked player in this fight')
    expected = pairs(row['talents'])
    if any(pairs(e.get('talentTree', []), 'id', 'rank') != expected for e in matching):
        raise ValueError('Ranking talents disagree with exact-fight combatant information')
    return matching[0], f'https://www.warcraftlogs.com/reports/{code}#fight={fight_id}&source={matching[0]["sourceID"]}&type=summary'


def describe_groups(rows, snapshot):
    entries = talent_entries(snapshot)
    groups, counts = {}, Counter()
    for row in rows:
        key = pairs(row['talents'])
        validate_ranked_talents(key, snapshot, entries)
        group = groups.setdefault(key, {'count': 0, 'row': row, 'key': key, 'encounters': Counter()})
        group['count'] += 1
        if row.get('_encounter_id') is not None:
            group['encounters'][row['_encounter_id']] += 1
        counts.update(key)
    return sorted(groups.values(), key=lambda g: -g['count']), entries, counts


def hero_for_key(key, entries):
    heroes = [entries[e][1]['name'] for e, rank in key if entries[e][0]['type'] == 'subtree']
    if len(heroes) != 1:
        raise ValueError('Cannot uniquely identify hero tree')
    return heroes[0]


def metric_enum(name):
    return {'speed': 'playerspeed', 'score': 'playerscore'}.get(name, name)


def key_scope(args):
    target = getattr(args, 'key', None)
    exact = getattr(args, 'exact_key', None)
    spread = getattr(args, 'key_spread', 2)
    if spread < 0 or any(k is not None and k < 1 for k in [target, exact, args.min_key, args.max_key]):
        raise ValueError('Key levels must be positive and spread nonnegative')
    if target is not None and exact is not None:
        raise ValueError('Choose a target key or an exact key, not both')
    if (target is not None or exact is not None) and (args.min_key is not None or args.max_key is not None):
        raise ValueError('Do not combine a target/exact key with an explicit range')
    if target is not None:
        args.min_key, args.max_key = max(2, target - spread), target + spread
    elif exact is not None:
        args.min_key = args.max_key = exact
    if args.min_key is not None and args.max_key is not None and args.min_key > args.max_key:
        raise ValueError('Invalid key range')
    if args.bracket is not None and (target is not None or
            (args.min_key is not None and args.max_key is not None and args.min_key != args.max_key)):
        raise ValueError('A single API bracket must not narrow a multi-level key sample')


def default_difficulty(difficulties):
    # A transparent general raid default; never silently mix raid difficulties.
    for name in ['heroic', 'normal']:
        matches = [d for d in difficulties if d['name'].casefold() == name]
        if len(matches) == 1:
            return matches[0]['id']
    if len(difficulties) == 1:
        return difficulties[0]['id']
    raise ValueError('No unambiguous default difficulty in provider metadata')


def level_sources(args):
    if args.bracket is None and args.min_key is not None and args.max_key is not None:
        # Candidate mapping only: every returned row must match its expected level.
        levels = list(range(args.min_key, args.max_key + 1))
        target = getattr(args, 'key', None) or (args.min_key + args.max_key) / 2
        levels.sort(key=lambda level: (abs(level - target), level))
        return [(level - 1, level) for level in levels]
    expected = args.min_key if args.min_key == args.max_key else None
    return [(args.bracket, expected)]


def interleave_levels(batches):
    return [row for group in zip_longest(*batches) for row in group if row is not None]


def recurring_options(snapshot, hero_talents, hero_counts):
    choices, selections = [], []
    for hero, scope in hero_talents.items():
        denominator = hero_counts[hero]
        for section in ['classNodes', 'specNodes', 'heroNodes']:
            for node in snapshot[section]:
                options = [{'name': entry['name'], 'spell_id': entry.get('spellId'),
                            'count': sum(count for (e, rank), count in scope.items() if e == entry['id'])}
                           for entry in node['entries']]
                if node['type'] == 'choice':
                    if sum(option['count'] / denominator >= .2 for option in options) >= 2:
                        choices.append({'hero_tree': hero, 'sample': denominator, 'options': options})
                else:
                    for entry in node['entries']:
                        for (entry_id, rank), count in scope.items():
                            if entry_id == entry['id'] and .2 <= count / denominator <= .8:
                                selections.append({'hero_tree': hero, 'sample': denominator,
                                                   'entry_id': entry_id, 'talent': entry['name'],
                                                   'rank': rank, 'count': count})
    return choices, selections


def run(args, client):
    key_scope(args)
    snapshot = next(t for t in tree_data() if t['specId'] == args.spec_id)
    if name_key(snapshot['className']) != name_key(args.class_name) or name_key(snapshot['specName']) != name_key(args.spec):
        raise ValueError('Spec ID/name/class disagree with current metadata')
    defaulted = args.difficulty is None
    zone_id = getattr(args, 'zone', None)
    if zone_id is not None:
        zone = client.query('{ worldData { zone(id:' + str(zone_id)
                            + ') { name encounters { id name } difficulties { id name } } } }')['worldData']['zone']
        targets = {e['id']: e['name'] for e in zone['encounters']}
        if not targets:
            raise ValueError('Selected zone has no encounters')
        if defaulted:
            args.difficulty = default_difficulty(zone['difficulties'])
    else:
        targets = {args.encounter: None}
    if defaulted and zone_id is None:
        metadata = client.query('{ worldData { encounter(id:' + str(args.encounter)
                                + ') { zone { difficulties { id name } } } } }')['worldData']['encounter']
        args.difficulty = default_difficulty(metadata['zone']['difficulties'])
    selected, exclusions = {}, Counter()
    fetched, pages = 0, 0
    levels_to_fetch = level_sources(args)
    multi_level = len(levels_to_fetch) > 1
    sources = [(target, bracket, level) for bracket, level in levels_to_fetch for target in targets]
    page_budget = args.pages if args.pages is not None else max(5, len(sources))
    if len(sources) > page_budget or page_budget > 100:
        raise ValueError('Page budget must cover every requested content/level stream, at most 100 pages')
    for page in range(1, page_budget + 1):
        if pages + len(sources) > page_budget:
            break
        batches, following = [], []
        for target, bracket, expected_level in sources:
            filters = f'encounter(id:{target}) {{ name zone {{ name difficulties {{ id name }} }} characterRankings(difficulty:{args.difficulty},className:{json.dumps(args.class_name)},specName:{json.dumps(args.spec)},metric:{metric_enum(args.metric)},includeCombatantInfo:true,page:{page}'
            if bracket is not None:
                filters += f',bracket:{bracket}'
            if args.partition is not None:
                filters += f',partition:{args.partition}'
            encounter = client.query('{ worldData { ' + filters + ') } } }')['worldData']['encounter']
            rankings = encounter['characterRankings']
            rows = rankings['rankings']
            targets[target] = encounter['name']
            for row in rows:
                row['_encounter_id'] = target
            if expected_level is not None and any(row.get('hardModeLevel') != expected_level for row in rows):
                raise ValueError('API bracket mapping did not return the expected key level')
            fetched += len(rows)
            pages += 1
            batches.append(rows)
            if rankings.get('hasMorePages'):
                following.append((target, bracket, expected_level))
        if select_rows(interleave_levels(batches), args, selected, exclusions, snapshot) or not following:
            break
        sources = following
    if not selected:
        raise ValueError('No eligible identifiable logged characters; do not widen filters silently')
    groups, entries, counts = describe_groups(list(selected.values()), snapshot)
    n = len(selected)
    hero_counts, hero_talents = Counter(), {}
    for group in groups:
        group['hero'] = hero_for_key(group['key'], entries)
        hero_counts[group['hero']] += group['count']
        counter = hero_talents.setdefault(group['hero'], Counter())
        for pair in group['key']:
            counter[pair] += group['count']
    # Top two illustrate fragmentation; include other full builds reaching the 20% threshold.
    shown = [g for i, g in enumerate(groups) if i < 2 or g['count'] / n >= .2]
    for hero, count in hero_counts.items():
        if count / n >= .2 and not any(g['hero'] == hero for g in shown):
            shown.append(next(g for g in groups if g['hero'] == hero))
    builds = []
    for group in shown:
        event, url = event_for_row(client, group['row'], args)
        exported = export_event(event, args.reference, args.lua, snapshot)
        builds.append({'count': group['count'], 'sample': n, 'hero_tree': group['hero'],
                       'content_coverage': {targets[target]: count for target, count in group['encounters'].items()},
                       'source': url, **exported})
    first = dict(groups[0]['key'])
    differences = []
    for number, group in enumerate(shown[1:], 2):
        if group['hero'] != groups[0]['hero']:
            continue  # Different hero trees get separate complete builds, not swap tables.
        alternative = dict(group['key'])
        scope = hero_talents[group['hero']]
        denominator = hero_counts[group['hero']]
        for entry in sorted(set(first) | set(alternative)):
            a, b = first.get(entry, 0), alternative.get(entry, 0)
            if a != b:
                differences.append({'alternative': number, 'talent': entries[entry][1]['name'],
                                    'spell_id': entries[entry][1].get('spellId'),
                                    'entry_id': entry, 'sample': denominator, 'hero_tree': group['hero'],
                                    'main_rank': a, 'alternative_rank': b,
                                    'main_rank_count': scope[(entry, a)] if a else denominator - sum(c for (e, r), c in scope.items() if e == entry),
                                    'alternative_rank_count': scope[(entry, b)] if b else denominator - sum(c for (e, r), c in scope.items() if e == entry)})
    choices, selections = recurring_options(snapshot, hero_talents, hero_counts)
    difficulty = next((d['name'] for d in encounter['zone']['difficulties'] if d['id'] == args.difficulty), None)
    if not difficulty:
        raise ValueError('Requested difficulty not listed for this content')
    levels = Counter(row['hardModeLevel'] for row in selected.values() if type(row.get('hardModeLevel')) is int and row['hardModeLevel'] > 0)
    coverage = Counter(row['_encounter_id'] for row in selected.values())
    unique_characters = len({identity[:3] for identity in selected})
    units = 'character–encounter observations' if zone_id is not None else 'unique characters'
    sampling = 'interleaved encounter/level rankings; one observation per character per encounter' if zone_id is not None else ('interleaved per-level rankings, nearest target first, unique characters' if multi_level else 'single ranking stream, unique characters')
    return {'source': 'Warcraft Logs', 'encounter': zone['name'] if zone_id is not None else encounter['name'],
            'encounter_id': args.encounter, 'zone_id': zone_id, 'sample_units': units,
            'unique_characters': unique_characters,
            'content_coverage': [{'id': target, 'name': name, 'observations': coverage[target]} for target, name in targets.items()],
            'class': snapshot['className'], 'spec': snapshot['specName'], 'difficulty': args.difficulty, 'difficulty_name': difficulty,
            'difficulty_defaulted': defaulted, 'key_target': getattr(args, 'key', None), 'observed_key_counts': dict(sorted(levels.items())),
            'sampling': sampling,
            'metric': args.metric, 'since': args.since, 'until': args.until,
            'key_range': [args.min_key, args.max_key], 'bracket': args.bracket, 'partition': args.partition,
            'fetched_rows': fetched, 'considered_rows': n + sum(exclusions.values()),
            'pages': pages, 'sample': n, 'excluded': dict(exclusions),
            'distinct_builds': len(groups), 'builds': builds, 'differences': differences,
            'hero_counts': dict(hero_counts), 'contested_choices': choices, 'recurring_selections': selections,
            'limits': ['Bounded ranking sample; composite region/server/name identity cannot track transfers.',
                       'External buffs not filtered; popularity is not a causal performance comparison.',
                       'Shown builds checked against exact fights; other ranking rows not independently audited.',
                       'Current metadata compatibility and codec round trips passed; no in-game import/legality test.',
                       'Zero tree hash; no automatic client-patch equivalence guarantee.']}


def markdown(data):
    lines = [f'# {data["spec"]} {data["class"]} — {data["encounter"]}', '',
             f'Warcraft Logs · {data["difficulty_name"]} · ranked by {data["metric"]} · {data["since"]} to {data["until"]}', '',
             f'{data["sample"]} {data["sample_units"]}; {data["unique_characters"]} distinct characters; {data["distinct_builds"]} complete builds. Counts are popularity, not proof of superiority.', '']
    if data['zone_id'] is not None:
        lines += ['Coverage: ' + ', '.join(f'{c["name"]}: {c["observations"]}' for c in data['content_coverage']) + '.', '',
                  'Sampling: ' + data['sampling'] + '. Zero-count encounters are not represented in the final sample.', '',
                  'This is a broad baseline; encounter-specific utility or damage-profile changes may still be needed.', '']
    if any(k is not None for k in data['key_range']):
        lines += [f'Actual key filter: +{data["key_range"][0] or "any"} to +{data["key_range"][1] or "any"}.', '']
    if data['difficulty_defaulted']:
        lines += [f'Difficulty was not specified; using {data["difficulty_name"]}.', '']
    if data['observed_key_counts']:
        lines += ['Observed key levels: ' + ', '.join(f'+{key}: {count}' for key, count in data['observed_key_counts'].items()) + '.', '']
        if data['zone_id'] is None:
            lines += ['Sampling: ' + data['sampling'] + '. Duplicate players, missing data and date filters can make level counts uneven.', '']
        if not any(k is not None for k in data['key_range']):
            lines += ['No key level specified: no key filter applied. This ranked sample is not an even sample of all levels.', '']
    lines += ['Hero trees: ' + ', '.join(f'{hero} {count}/{data["sample"]}' for hero, count in data['hero_counts'].items()) + '.', '']
    if data['unique_characters'] < 20:
        lines += ['**Small sample: fewer than 20 usable characters. Treat these frequencies cautiously.**', '']
    if data['builds'][0]['count'] * 2 <= data['sample']:
        lines += ['**Mixed sample: no complete build has a majority.**', '']
    if len(data['builds']) > 1 and data['builds'][0]['count'] == data['builds'][1]['count']:
        lines += ['**The leading builds are tied; their display order is not a performance ranking.**', '']
    for i, build in enumerate(data['builds'], 1):
        label = 'Most common observed build' if i == 1 else 'Observed alternative'
        lines += [f'## {i}. {label} — {build["hero_tree"]}', '',
                  f'{build["count"]}/{build["sample"]} ({100*build["count"]/build["sample"]:.0f}%). [Exact logged build]({build["source"]}).', '',
                  '```text', build['import_string'], '```', '']
        if data['zone_id'] is not None:
            lines += ['This build appeared in: ' + ', '.join(f'{name} ({count})' for name, count in build['content_coverage'].items()) + '.', '']
    if data['differences']:
        lines += ['## Talent differences', '', 'Usage counts below are within the named hero tree, not the complete alternative build share.', '', '| Alternative | Talent | Main rank | Alternative rank | Alternative rank usage |', '| --- | --- | ---: | ---: | ---: |']
        names = Counter(diff['talent'] for diff in data['differences'])
        for diff in data['differences']:
            name = diff['talent']
            if names[name] > 1:
                name += f' (spell {diff["spell_id"]})'
            lines.append(f'| {diff["alternative"]} | {name} | {diff["main_rank"]} | {diff["alternative_rank"]} | {diff["alternative_rank_count"]}/{diff["sample"]} {diff["hero_tree"]} |')
        lines += ['', 'These are differences between complete builds, not independently validated one-for-one swaps.', '']
    if data['contested_choices']:
        lines += ['## Other mixed choices', '']
        for choice in data['contested_choices']:
            lines.append('- ' + choice['hero_tree'] + ': ' + ' / '.join(f'{o["name"]} {o["count"]}/{choice["sample"]}' for o in choice['options']))
        lines += ['', 'Observed choice-node frequencies; check full-build paths before changing talents.', '']
    other = [s for s in data['recurring_selections']
             if not any(d['entry_id'] == s['entry_id'] and d['hero_tree'] == s['hero_tree'] for d in data['differences'])]
    if other:
        lines += ['## Other recurring selections', '', 'These can coexist; they are not a suggested hybrid build.', '']
        lines += [f'- {s["hero_tree"]}: {s["talent"]}, rank {s["rank"]}: {s["count"]}/{s["sample"]}.' for s in other]
        lines.append('')
    lines += ['## Evidence and limits', '', f'Fetched {data["fetched_rows"]} rankings over {data["pages"]} pages; considered {data["considered_rows"]} to select the sample. Exclusions: ' + ', '.join(f'{n} {reason.replace("_", " ")}' for reason, n in data['excluded'].items()) + '.', '']
    lines += ['- ' + limit for limit in data['limits']]
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--encounter', type=int, help='One specific boss/dungeon')
    scope.add_argument('--zone', type=int, help='All encounters in a resolved current raid/M+ season')
    parser.add_argument('--difficulty', type=int, help='Optional; defaults to Heroic for raids or the sole dungeon difficulty')
    parser.add_argument('--class', dest='class_name', required=True)
    parser.add_argument('--spec', required=True)
    parser.add_argument('--spec-id', type=int, required=True)
    parser.add_argument('--since', required=True, help='Inclusive UTC date/time from the applicable patch')
    parser.add_argument('--until', default=datetime.now(timezone.utc).isoformat())
    parser.add_argument('--sample', type=int, default=50)
    parser.add_argument('--pages', type=int, help='Total page budget; defaults to at least one page per content/level stream, minimum 5')
    parser.add_argument('--metric', choices=['dps', 'hps', 'speed', 'score'], default='dps')
    parser.add_argument('--bracket', type=int)
    parser.add_argument('--partition', type=int)
    parser.add_argument('--min-key', type=int)
    parser.add_argument('--max-key', type=int)
    parser.add_argument('--key', type=int, help='Target key; includes nearby levels, default +/-2')
    parser.add_argument('--key-spread', type=int, default=2)
    parser.add_argument('--exact-key', type=int, help='Only when the user explicitly requests one exact level')
    parser.add_argument('--reference', required=True)
    parser.add_argument('--lua', default='lua')
    parser.add_argument('--rbw-entry')
    parser.add_argument('--rbw-folder')
    parser.add_argument('--format', choices=['json', 'markdown'], default='markdown')
    args = parser.parse_args()
    try:
        args.since_ms, args.until_ms = timestamp(args.since), timestamp(args.until)
        if not 1 <= args.sample <= 100 or (args.pages is not None and not 1 <= args.pages <= 100) or args.since_ms > args.until_ms:
            raise ValueError('Invalid sample/page/date bounds')
        if args.min_key is not None and args.max_key is not None and args.min_key > args.max_key:
            raise ValueError('Invalid key range')
        if any(k is not None and k < 1 for k in [args.min_key, args.max_key]):
            raise ValueError('Key levels must be positive')
        result = run(args, Client(*credentials(args)))
    except (ValueError, KeyError, TypeError, StopIteration, OSError, subprocess.SubprocessError) as error:
        print('Build analysis incomplete: ' + str(error)[:1200], file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.format == 'json' else markdown(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
