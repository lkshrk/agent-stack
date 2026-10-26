#!/usr/bin/env python3
"""Validate an AI-produced data-only candidate before the trusted publishing job commits it."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse

from check_wow_season_updates import active_bundle, observe, refs, targets, watch_record
from enrich_wow_catalog import enrich
from prediff_wow_specs import classify, live_guides, needs_advance

ALLOWED = re.compile(r'(catalog|index|source-watch|spec-\d+)\.json')
# Provenance and verification bookkeeping; a change confined to these is not a player-visible update.
BOOKKEEPING = {'verified_at', 'identity_verified_at', 'verified_patch', 'captured_at', 'updated', 'sources',
               'unresolved', 'unknown_fields', 'quality_note', 'identity_verification', 'verification_basis',
               'validation_scope', 'coverage', 'tooltip_enriched_at'}


def load_bundle(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Candidate must be a real directory')
    docs = {}
    for file in path.iterdir():
        if file.is_symlink() or not file.is_file() or not ALLOWED.fullmatch(file.name):
            raise ValueError('Unexpected candidate path: ' + file.name)
        if file.stat().st_size > 5_000_000:
            raise ValueError('Candidate JSON unexpectedly large')
        docs[file.name] = json.loads(file.read_text())
    return docs


def sources_valid(sources):
    if not sources:
        raise ValueError('Missing source provenance')
    for source in sources:
        url = source['url'] if isinstance(source, dict) else source
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Expected public HTTPS source')


def valid_date(value):
    if date.fromisoformat(value) > date.today():
        raise ValueError('Verification date is in the future')


def identity_checks(old, candidate):
    checked = {}
    for key, record in candidate['catalog.json']['records'].items():
        if record == old['catalog.json']['records'].get(key):
            continue
        if not re.fullmatch(r'(item|spell):\d+', key):
            raise ValueError('Invalid changed catalogue key')
        url = 'https://nether.wowhead.com/tooltip/' + key.replace(':', '/')
        response = subprocess.run(['curl', '-fsSL', '--max-time', '20', url], capture_output=True, text=True, check=True)
        data = json.loads(response.stdout)
        from check_wow_season_updates import fingerprint
        checked[key] = {'name': data['name'], 'url': url, 'sha256': fingerprint('tooltip', response.stdout)}
    return checked


def validate(old, candidate, result, observation, checked=None):
    checked = checked or {}
    if result.get('status') not in ['complete', 'partial']:
        raise ValueError('Refresh blocked; no candidate may be published')
    if not isinstance(result.get('summary'), str) or not result['summary'].strip():
        raise ValueError('Missing refresh summary')
    if not set(old).issubset(candidate):
        raise ValueError('Candidate deleted existing files')
    if candidate['source-watch.json'] != old['source-watch.json']:
        raise ValueError('Agent must not approve its own source fingerprints')
    index, catalog = candidate['index.json'], candidate['catalog.json']
    if index.get('schema_version') != 1 or catalog.get('schema_version') != 1:
        raise ValueError('Unsupported bundle schema')
    scope = (old['index.json']['game'], old['index.json']['season'])
    if (index['game'], index['season']) != scope or (catalog['game'], catalog['season']) != scope:
        raise ValueError('Game/season migration requires a separate change')
    if observation['current']['season'] != scope[1]:
        raise ValueError('Observation is from a different season')
    for field in ['roster_source', 'season_source', 'trigger_sources']:
        if index.get(field) != old['index.json'].get(field):
            raise ValueError('Metadata source migration requires a separate change')
    valid_date(index['verified_at'])
    valid_date(catalog['verified_at'])
    if not set(old['catalog.json']['records']).issubset(catalog['records']):
        raise ValueError('Existing catalogue records must be preserved')
    ids = [s['spec_id'] for s in index['specs']]
    if len(ids) != len(set(ids)) or len(ids) != index['spec_count']:
        raise ValueError('Duplicate/inconsistent spec coverage')
    expected = {f'spec-{sid}.json' for sid in ids}
    if expected != {name for name in candidate if name.startswith('spec-')}:
        raise ValueError('Index and spec files disagree')
    if index['catalogue_record_count'] != len(catalog['records']):
        raise ValueError('Catalogue count mismatch')
    for key, record in catalog['records'].items():
        kind, identity = key.split(':')
        if kind not in ['item', 'spell'] or record.get(kind + '_id') != int(identity):
            raise ValueError('Invalid item/spell identity')
        if record.get('status') != 'verified' or record['name'].startswith(('Recipe:', 'Formula:')):
            raise ValueError('Unverified identity or recipe used as an item')
        sources_valid(record['sources'])
        valid_date(record['verified_at'])
        valid_date(record['identity_verified_at'])
    for entry in index['specs']:
        name = f'spec-{entry["spec_id"]}.json'
        setup = candidate[name]
        if setup.get('schema_version') != 1:
            raise ValueError('Unsupported spec schema')
        if entry['file'] != name or setup['spec_id'] != entry['spec_id']:
            raise ValueError('Spec identity mismatch')
        if (setup['game'], setup['season']) != scope or setup.get('status') != 'verified':
            raise ValueError('Invalid spec scope/status')
        sources_valid(setup['sources'])
        valid_date(setup['verified_at'])
        if not setup.get('stat_guidance') or any(not setup.get('recommendations', {}).get(s) for s in ['gems', 'enchants', 'consumables']):
            raise ValueError('Required setup section removed')
        if any(key not in catalog['records'] for key in refs(setup)):
            raise ValueError('Broken catalogue reference')
    accepted = result.get('accepted_sources')
    unresolved = result.get('unresolved_sources')
    if not isinstance(accepted, list) or not isinstance(unresolved, list) or set(accepted) & set(unresolved):
        raise ValueError('Invalid accepted/unresolved source lists')
    if result['status'] == 'complete' and unresolved:
        raise ValueError('A complete result cannot contain unresolved sources')
    substantive_changes = []
    for name, value in candidate.items():
        if name == 'source-watch.json':
            continue
        previous = old.get(name, {})
        if name.startswith('spec-'):
            value = {k: v for k, v in value.items() if k != 'unresolved'}
            previous = {k: v for k, v in previous.items() if k != 'unresolved'}
        substantive_changes.append(value != previous)
    if any(substantive_changes) and not accepted:
        raise ValueError('Data changes require verified source acknowledgement')
    wanted = targets(documents=candidate)
    previous_targets = targets(documents=old)
    if any(url not in wanted for url in accepted):
        raise ValueError('Cannot accept an unrelated source')
    current_sources = copy.deepcopy(observation['current']['sources'])
    # Replacement guides were not present during the pre-agent capture. Observe
    # only accepted new citations, with spec ownership derived from the candidate.
    for url in dict.fromkeys(accepted):
        added_specs = set(wanted[url]['spec_ids']) - set(previous_targets.get(url, {}).get('spec_ids', []))
        if wanted[url]['kind'] == 'guide' and (url not in previous_targets or added_specs):
            sources_valid([url])
            _, current_sources[url] = observe((url, wanted[url]))
    # A successful unrelated lookup cannot authorise rewriting an unavailable guide's advice.
    for name, setup in candidate.items():
        if not name.startswith('spec-') or setup == old.get(name):
            continue
        previous = old.get(name, {})
        before = {k: v for k, v in previous.items() if k != 'unresolved'}
        after = {k: v for k, v in setup.items() if k != 'unresolved'}
        if before == after and set(previous.get('unresolved', [])).issubset(set(setup.get('unresolved', []))):
            continue  # May add a concrete limitation without changing advice or dates.
        guide_urls = {s['url'] if isinstance(s, dict) else s for s in setup['sources']}
        evidence = [current_sources[u] for u in accepted if u in guide_urls and u in current_sources
                    and current_sources[u].get('kind') == 'guide' and not current_sources[u].get('error')
                    and setup['spec_id'] in current_sources[u].get('spec_ids', [])]
        if not evidence:
            raise ValueError('Changed spec advice requires its own successfully checked guide: ' + name)
    for key, record in catalog['records'].items():
        if record != old['catalog.json']['records'].get(key):
            proof = checked.get(key, {})
            normalize = lambda value: re.sub(r'[^a-z0-9]', '', value.casefold())
            if not proof.get('name') or normalize(proof['name']) != normalize(record['name']):
                raise ValueError('Changed catalogue identity not verified by publisher: ' + key)
    refreshed = copy.deepcopy(candidate)
    watch = refreshed['source-watch.json']
    fingerprints_changed = False
    for url in list(watch['sources']):
        if url not in wanted:
            del watch['sources'][url]
            fingerprints_changed = True
    for url in accepted:
        current = current_sources.get(url)
        if not current or current.get('error') or not re.fullmatch('[0-9a-f]{64}', current.get('sha256', '')):
            raise ValueError('Cannot advance an unavailable/unobserved source')
        if watch['sources'].get(url) != watch_record(current):
            watch['sources'][url] = watch_record(current)
            fingerprints_changed = True
    if fingerprints_changed:
        watch['captured_at'] = observation['current']['captured_at']
    return refreshed


def substance(value):
    if isinstance(value, dict):
        return {k: substance(v) for k, v in value.items() if k not in BOOKKEEPING}
    if isinstance(value, list):
        return [substance(v) for v in value]
    if isinstance(value, str):
        return ' '.join(value.split()).rstrip('.').casefold()
    return value


def drop_negligible(old, docs):
    """Revert data whose only differences are bookkeeping or cosmetic wording; fingerprints still advance."""
    kept = copy.deepcopy(docs)
    records, previous = kept['catalog.json']['records'], old['catalog.json']['records']
    for key in records:
        if key in previous and substance(records[key]) == substance(previous[key]):
            records[key] = previous[key]
    for name in kept:
        if name != 'source-watch.json' and name in old and substance(kept[name]) == substance(old[name]):
            kept[name] = old[name]
    return kept


def merge_parts(old, parts):
    """Combine independent per-spec Claude runs into one candidate bundle and one legacy-shaped result."""
    merged = copy.deepcopy(old)
    records = merged['catalog.json']['records']
    accepted, unresolved, summaries, unchanged, updated = [], [], [], [], []
    for result, candidate in parts:
        spec_id, name = result['spec_id'], f'spec-{result["spec_id"]}.json'
        if name not in old:
            raise ValueError('Result for an unknown spec: ' + name)
        if result['outcome'] not in ['updated', 'unchanged', 'unresolved']:
            raise ValueError('Unsupported per-spec outcome')
        for other, value in candidate.items():
            if other in [name, 'catalog.json']:
                continue
            before, after = old.get(other), value
            if other == 'index.json':
                before, after = ({k: v for k, v in d.items() if k != 'catalogue_record_count'} for d in (before, after))
            if before != after:
                raise ValueError('Per-spec run changed files outside its scope: ' + other)
        accepted += result['accepted_sources']
        unresolved += result['unresolved_sources']
        summaries.append(f'Spec {spec_id} ({result["outcome"]}): {result["summary"].strip()}')
        if result['outcome'] == 'unresolved':
            continue
        for key, record in candidate['catalog.json']['records'].items():
            if record == old['catalog.json']['records'].get(key):
                continue
            if key in records and records[key] != old['catalog.json']['records'].get(key) and records[key] != record:
                raise ValueError('Conflicting catalogue edits for ' + key)
            records[key] = record
        if result['outcome'] == 'updated':
            merged[name] = candidate[name]
            updated.append(spec_id)
        else:
            before = {k: v for k, v in old[name].items() if k != 'unresolved'}
            if {k: v for k, v in candidate[name].items() if k != 'unresolved'} != before:
                raise ValueError('An unchanged outcome must not edit the spec: ' + name)
            unchanged.append(spec_id)
    merged['index.json']['catalogue_record_count'] = len(records)
    blocked = set(unresolved)
    accepted = [url for url in dict.fromkeys(accepted) if url not in blocked]
    result = {'status': 'partial' if unresolved else 'complete',
              'summary': '\n'.join(summaries) or 'No Claude runs were needed.',
              'accepted_sources': accepted, 'unresolved_sources': list(dict.fromkeys(unresolved))}
    return merged, result, unchanged, updated


def load_parts(directory):
    parts = []
    for folder in sorted(directory.iterdir()) if directory and directory.is_dir() else []:
        result_file = folder / 'result.json'
        if result_file.is_file() and (folder / 'candidate').is_dir():
            parts.append((json.loads(result_file.read_text()), load_bundle(folder / 'candidate')))
    return parts


def fetch_guides(docs, spec_ids):
    wanted = {}
    for entry in docs['index.json']['specs']:
        if entry['spec_id'] in spec_ids:
            for source in docs[entry['file']]['sources']:
                url = source['url'] if isinstance(source, dict) else source
                wanted[url] = {'kind': 'guide', 'spec_ids': [entry['spec_id']]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(observe, wanted.items()))


def version_key(patch):
    return [int(part) for part in patch.split('.')]


def advance_verified(docs, spec_ids, sources, today, require_unchanged, force=False):
    """Record that a spec was rechecked against its live guide and its advice still stands."""
    live = {s: v for s, v in sources.items() if v.get('kind') == 'guide'}
    done = []
    verdicts = classify(docs, live) if require_unchanged else {}
    watch = docs['source-watch.json']
    for sid in spec_ids:
        name = f'spec-{sid}.json'
        setup = copy.deepcopy(docs[name])
        guides = live_guides(setup, live)
        if not guides or (require_unchanged and (verdicts[sid]['status'] != 'unchanged'
                                                 or not needs_advance(setup, guides, watch['sources'], force))):
            continue
        patches = [g['meta']['patch'] for g in guides.values() if g.get('meta', {}).get('patch')]
        if patches:
            setup['verified_patch'] = max(patches, key=version_key)
        setup['verified_at'] = today
        for source in setup['sources']:
            if isinstance(source, dict) and guides.get(source['url'], {}).get('meta', {}).get('updated'):
                source['updated'] = guides[source['url']]['meta']['updated']
        if setup != docs[name]:
            docs[name] = setup
            done.append(sid)
        for url, guide in guides.items():
            if watch['sources'].get(url) != watch_record(guide):
                watch['sources'][url] = watch_record(guide)
    if done:
        index = docs['index.json']
        index['verified_at'] = docs['catalog.json']['verified_at'] = today
        patches = {docs[f'spec-{e["spec_id"]}.json'].get('verified_patch') for e in index['specs']}
        if len(patches) == 1:
            index['verified_patch'] = patches.pop()
    return done


def record_informational(docs, observation):
    watch = docs['source-watch.json']
    wanted = targets(documents=docs)
    for url, value in observation['current']['sources'].items():
        if value.get('kind') in ['crosscheck', 'news'] and not value.get('error') and url in wanted:
            watch['sources'][url] = watch_record(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parts', type=Path, help='Directory of per-spec runs: <dir>/<name>/{candidate/,result.json}; may be empty or absent')
    parser.add_argument('--observation', type=Path, required=True)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--game')
    parser.add_argument('--season')
    parser.add_argument('--no-enrich', action='store_true')
    parser.add_argument('--force', action='store_true', help='Advance verification of unchanged specs even without drift')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    bundle = args.bundle or active_bundle(args.game, args.season)
    old = load_bundle(bundle)
    observation = json.loads(args.observation.read_text())
    today = date.today().isoformat()
    candidate, result, ai_unchanged, ai_updated = merge_parts(old, load_parts(args.parts))
    checked = identity_checks(old, candidate)
    for key, proof in checked.items():
        spec_ids = [d['spec_id'] for name, d in candidate.items() if name.startswith('spec-') and key in refs(d)]
        observation['current']['sources'][proof['url']] = {'kind': 'tooltip', 'spec_ids': sorted(spec_ids), 'sha256': proof['sha256']}
    docs = copy.deepcopy(drop_negligible(old, validate(old, candidate, result, observation, checked)))
    all_ids = [e['spec_id'] for e in docs['index.json']['specs']]
    rest = [i for i in all_ids if i not in ai_updated]
    fresh = fetch_guides(docs, set(rest))
    advanced = advance_verified(docs, [i for i in rest if i not in ai_unchanged], fresh, today, True, args.force)
    advanced += advance_verified(docs, [i for i in ai_unchanged if i in rest], {**fresh, **observation['current']['sources']}, today, False)
    if any(docs[n] != old.get(n) for n in docs):
        record_informational(docs, observation)
    enriched = {} if args.no_enrich else enrich(docs['catalog.json'], today)
    changed = [name for name, value in docs.items() if value != old.get(name)]
    release = any(substance(docs[n]) != substance(old.get(n, {})) for n in changed if n != 'source-watch.json')
    if not args.dry_run:
        for name in changed:
            (bundle / name).write_text(json.dumps(docs[name], indent=2) + '\n')
    lines = [result['summary']]
    if advanced:
        lines += ['', f'Advice unchanged, verification advanced for {len(advanced)} specs: ' + ', '.join(map(str, sorted(advanced)))]
    if enriched:
        lines += ['', f'Tooltip data filled for {len(enriched)} catalogue records: ' + ', '.join(sorted(enriched))]
    deferred = observation.get('prediff', {}).get('deferred', [])
    if deferred:
        lines += ['', 'Specs with differences not processed this run (retried next run): ' + ', '.join(map(str, deferred))]
    output = ({'changed_files': changed, 'release': release, 'status': result['status'], 'summary': '\n'.join(lines),
                      'advanced': sorted(advanced), 'ai_updated': ai_updated, 'enriched': sorted(enriched)})
    print(json.dumps(output))
    return output


if __name__ == '__main__':
    main()
