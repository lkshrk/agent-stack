#!/usr/bin/env python3
"""Read-only public-source drift check; never updates WoW recommendations or credentials."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / 'skills/wow-spec/assets/season-data'
SOURCE_FIELDS = ('kind', 'spec_ids', 'sha256', 'meta', 'error')


def active_bundle(game=None, season=None, root=DATA_ROOT):
    if not (game and season):
        pointer = json.loads((root / 'current.json').read_text())
        game, season = game or pointer['game'], season or pointer['season']
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', game) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', season):
        raise ValueError('Invalid game/season name')
    return root / game / season


def watch_record(value):
    return {k: v for k, v in value.items() if k in SOURCE_FIELDS}


def refs(value):
    if isinstance(value, str):
        return {value} if re.fullmatch(r'(item|spell):\d+', value) else set()
    if isinstance(value, dict):
        return set().union(*(refs(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(refs(v) for v in value))
    return set()


def classify_url(url):
    host = urlparse(url).netloc
    if host == 'nether.wowhead.com' and '/tooltip/' in url:
        return 'tooltip'
    if host.endswith('murlok.io'):
        return 'crosscheck'
    return 'guide'


def targets(bundle=None, documents=None):
    def read(name):
        return documents[name] if documents is not None else json.loads((bundle / name).read_text())
    catalog = read('catalog.json')
    index = read('index.json')
    out = {}

    def add(url, kind, spec=None):
        row = out.setdefault(url, {'kind': kind, 'spec_ids': []})
        if spec is not None and spec not in row['spec_ids']:
            row['spec_ids'].append(spec)

    for entry in index['specs']:
        setup = read(entry['file'])
        for source in setup['sources']:
            url = source['url'] if isinstance(source, dict) else source
            entity = re.search(r'(item|spell)=(\d+)', url)
            if entity and 'wowhead.com' in urlparse(url).netloc:
                add('https://nether.wowhead.com/tooltip/' + '/'.join(entity.groups()), 'tooltip', entry['spec_id'])
            else:
                add(url, classify_url(url), entry['spec_id'])
        for key in refs(setup):
            if key not in catalog['records']:
                raise ValueError('Unresolved catalogue reference: ' + key)
            add('https://nether.wowhead.com/tooltip/' + key.replace(':', '/'), 'tooltip', entry['spec_id'])
    add(index['roster_source'], 'roster')
    if index.get('season_source'):
        add(index['season_source'], 'seasons')
    for url in index.get('trigger_sources', []):
        add(url, 'news')
    for row in out.values():
        row['spec_ids'].sort()
    return dict(sorted(out.items()))


SECTION_KINDS = [('stats', r'stat'), ('enchants', r'enchant'), ('gems', r'gem'),
                 ('consumables', r'consum|flask|potion|food'), ('gear', r'gear|\bbis\b')]
SECTION_TITLE = re.compile(r'<div[^>]*class="[^"]*guide-section-title[^"]*"[^>]*>(.*?)</div>', re.S)
AGO = re.compile(r'\(?\b\d+\s*(?:d|w|mo|days?|weeks?|months?|years?)\s+ago\)?', re.I)


class SectionText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ['script', 'style', 'noscript', 'svg']:
            self.skip += 1
        if self.skip:
            return
        if tag in ['h1', 'h2', 'h3', 'h4', 'p', 'li', 'tr', 'br', 'div']:
            self.parts.append('\n')
        entity = re.search(r'(item|spell)=(\d+)', dict(attrs).get('href') or '')
        if tag == 'a' and entity:
            self.parts.append(' [' + ':'.join(entity.groups()) + '] ')

    def handle_endtag(self, tag):
        if tag in ['script', 'style', 'noscript', 'svg']:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(text)


def plain(fragment):
    parser = SectionText()
    parser.feed(fragment)
    return ' '.join(AGO.sub('', ''.join(parser.parts)).split())


def ordinal_date(text):
    match = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*,?\s+(\d{4})', text or '')
    if not match:
        return None
    try:
        return datetime.strptime(' '.join(match.groups()), '%d %b %Y').date().isoformat()
    except ValueError:
        return None


def guide_meta(raw):
    patch = re.search(r'Patch\s+(\d+(?:\.\d+)+)', raw)
    updated = re.search(r'Last Updated:?\s*(?:</[^>]+>)?\s*([^<]{4,40})', raw)
    return {'patch': patch.group(1) if patch else None, 'updated': ordinal_date(updated.group(1)) if updated else None}


def guide_sections(raw):
    body = raw[:raw.index('</article>')] if '</article>' in raw else raw
    marks = list(SECTION_TITLE.finditer(body))
    sections = {}
    for number, mark in enumerate(marks):
        end = marks[number + 1].start() if number + 1 < len(marks) else len(body)
        title = plain(mark.group(1)).casefold()
        for key, pattern in SECTION_KINDS:
            if re.search(pattern, title):
                sections[key] = (sections.get(key, '') + ' ' + plain(body[mark.end():end])).strip()
                break
    return sections


def murlok_sections(raw):
    raw = re.sub(r'<(script|style|svg)\b.*?</\1>', '', raw, flags=re.S)
    parts = re.split(r'(?=<h2[ >])', raw)

    def block(title):
        return next((p for p in parts if re.match(r'<h2[^>]*>\s*' + title, p)), '')
    stat = plain(block('Stat Priority'))
    order = re.search(r'Stat priority ((?:[A-Z][a-z]+(?: Strike)? ){2,5}?)(?:Additional|Talent)', stat + ' Talent')
    names = lambda title: sorted({plain(n) for n in re.findall(r'<h4[^>]*>(.*?)</h4>', block(title), re.S)})
    return {'stats': order.group(1).strip() if order else '', 'enchants': ' | '.join(names('Enchantments')),
            'gems': ' | '.join(names('Gems'))}


def news_entries(raw):
    found = re.findall(r'"title":"([^"]+)","id":(\d+),"slug":"([^"]+)"', raw)
    return sorted({(int(i), t) for t, i, _ in found if re.search(r'hotfix|patch\s+\d|patch notes', t, re.I)})


def analyze(kind, raw):
    extra = {}
    if kind == 'guide':
        sections = guide_sections(raw)
        if not sections:
            start = max(raw.find('<article'), 0) if '<article' in raw else max(raw.find('<h1'), 0)
            ends = [i for i in (raw.find('</article>', start), raw.find('<footer', start)) if i >= 0]
            sections = {'body': plain(raw[start:min(ends) if ends else len(raw)])}
        if sum(map(len, sections.values())) < 150:
            raise ValueError('No usable guide body; access challenge or layout change')
        content = json.dumps(sections, sort_keys=True)
        extra = {'meta': guide_meta(raw), 'extract': sections}
    elif kind == 'crosscheck':
        sections = murlok_sections(raw)
        if not sections['stats'] or not sections['enchants']:
            raise ValueError('Cross-check page layout not recognised')
        content = json.dumps(sections, sort_keys=True)
        extra = {'extract': sections}
    elif kind == 'news':
        entries = news_entries(raw)
        if not entries:
            raise ValueError('No hotfix or patch announcements found')
        content = json.dumps(entries)
        extra = {'extract': {'announcements': [title for _, title in entries]}}
    else:
        data = json.loads(raw)
        if kind == 'tooltip':
            if not data.get('name') or not data.get('tooltip'):
                raise ValueError('Missing tooltip identity/body')
            body = re.sub(r'<[^>]+>', ' ', data['tooltip'])
            content = json.dumps({'name': data['name'], 'text': ' '.join(body.split())}, sort_keys=True)
        elif kind == 'roster':
            content = json.dumps(sorted((r['specId'], r['className'], r['specName']) for r in data))
        elif kind == 'seasons':
            seasons = [{'slug': s['slug'], 'starts': s.get('starts'), 'ends': s.get('ends'),
                        'dungeons': sorted(d['slug'] for d in s.get('dungeons', []))}
                       for s in data['seasons'] if s.get('is_main_season')]
            content = json.dumps(sorted(seasons, key=lambda s: s['slug']), sort_keys=True)
        else:
            raise ValueError('Unsupported source kind')
    return hashlib.sha256(content.encode()).hexdigest(), extra


def fingerprint(kind, raw):
    return analyze(kind, raw)[0]


def observe(pair):
    url, info = pair
    if urlparse(url).scheme != 'https':
        return url, {**info, 'error': 'Only HTTPS public sources supported'}
    result = subprocess.run(['curl', '-fsSL', '--max-time', '20', url], capture_output=True, text=True)
    if result.returncode:
        status = re.search(r'(?:error:|returned error:)\s*(\d{3})', result.stderr)
        return url, {**info, 'error': 'HTTP ' + status.group(1) if status else 'Retrieval failed/timeout'}
    try:
        digest, extra = analyze(info['kind'], result.stdout)
        return url, {**info, 'sha256': digest, **extra}
    except (ValueError, KeyError, TypeError) as error:
        return url, {**info, 'error': str(error)[:150]}


def capture(bundle):
    wanted = targets(bundle)
    started = time.monotonic()
    total = len(wanted)
    print(f'Source detection started: fetching {total} public sources with 4 workers; '
          '20s timeout per source.', file=sys.stderr, flush=True)
    sources = {}
    unavailable = 0
    interval = max(1, (total + 9) // 10)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(observe, pair) for pair in wanted.items()]):
            url, value = future.result()
            sources[url] = value
            unavailable += bool(value.get('error'))
            done = len(sources)
            if done % interval == 0 or done == total:
                print(f'Source collection: {done}/{total} completed; {done - unavailable} fingerprinted; '
                      f'{unavailable} unavailable; {time.monotonic() - started:.1f}s elapsed.',
                      file=sys.stderr, flush=True)
    index = json.loads((bundle / 'index.json').read_text())
    return {'schema_version': 2, 'captured_at': datetime.now(timezone.utc).isoformat(),
            'game': index['game'], 'season': index['season'],
            'sources': {url: sources[url] for url in wanted}}


INFORMATIONAL = ('crosscheck',)


def compare(baseline, current):
    rows = []
    for url, value in current['sources'].items():
        old = baseline.get('sources', {}).get(url)
        if value.get('error'):
            status = 'unavailable'
        elif old is None or not old.get('sha256'):
            status = 'baseline-needed'
        elif value['sha256'] != old['sha256']:
            status = 'changed'
        else:
            status = 'unchanged'
        if value.get('kind') in INFORMATIONAL and status != 'unchanged':
            status = 'informational'
        rows.append({'url': url, 'status': status,
                     'known_unavailable': status == 'unavailable' and bool(old and old.get('error') and not old.get('sha256')),
                     **{k: v for k, v in value.items() if k != 'extract'}})
    for url, old in baseline.get('sources', {}).items():
        if url not in current['sources']:
            rows.append({'url': url, 'status': 'no-longer-referenced', 'spec_ids': old.get('spec_ids', [])})
    return rows


def report(rows, current, force_refresh=False):
    changed = [r for r in rows if r['status'] != 'unchanged']
    counts = Counter(r['status'] for r in rows)
    known = sum(bool(r.get('known_unavailable')) for r in rows)
    required = exit_status(rows) != 0
    reasons = []
    for status, label in [('changed', 'changed fingerprints'), ('baseline-needed', 'sources needing a baseline'),
                          ('no-longer-referenced', 'removed source references')]:
        if counts[status]:
            reasons.append(f'{counts[status]} {label}')
    if counts['unavailable'] > known:
        reasons.append(f'{counts["unavailable"] - known} newly unavailable sources')
    if rows and counts['unavailable'] == len(rows):
        reasons.append('all sources unavailable; no successful source checks')
    if force_refresh:
        reasons.append('force_refresh=true requests a recommendation recheck')
    if not reasons:
        reasons.append('no actionable source drift detected; known monitoring gaps alone do not trigger refresh')
    lines = ['# WoW seasonal source check', '',
             f'Season: {current["season"]} · Checked: {current["captured_at"]}', '',
             '**Source detection ran:** public-source collection and fingerprint comparison completed.', '',
             f'{counts["unchanged"]} unchanged; {counts["changed"]} changed; '
             f'{counts["baseline-needed"]} baseline needed; {counts["no-longer-referenced"]} removed; '
             f'{counts["unavailable"] - known} newly unavailable; {known} known monitoring gaps.', '',
             f'**Recommendation refresh gate: {"RUN" if required or force_refresh else "SKIP"}** — '
             + '; '.join(reasons) + '.',
             f'`requires_refresh={str(required).lower()}` · `force_refresh={str(force_refresh).lower()}`', '',
             'RUN authorizes the downstream refresh; it does not mean Codex or publication has completed.',
             'Manual dispatch alone does not force a refresh. Enable **Recheck recommendations even when source '
             'fingerprints are unchanged** (`force_refresh`) to request that recheck.', '',
             'This detects source drift, not whether a recommendation is now wrong. Verified setup files were not modified.', '']
    if changed:
        lines += ['| Status | Source | Affected specs | Detail |', '| --- | --- | --- | --- |']
        for row in changed:
            affected = ', '.join(str(i) for i in row['spec_ids']) or 'All / season metadata'
            detail = row.get('error', 'Review before refreshing the bundled baseline').replace('|', '/')
            if row.get('known_unavailable'):
                detail += ' (known monitoring gap; retrieval attempted, content could not be checked)'
            lines.append(f'| {row["status"]} | {row["url"]} | {affected} | {detail} |')
    else:
        lines += ['No source changes detected. This does not replace checking unmonitored hotfix announcements.']
    return '\n'.join(lines) + '\n'


def exit_status(rows):
    if rows and all(r['status'] == 'unavailable' for r in rows):
        return 2
    if any(r['status'] == 'unavailable' and not r.get('known_unavailable') for r in rows):
        return 2
    return int(any(r['status'] not in ['unchanged', 'unavailable', 'informational'] for r in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--game', help='Defaults to the game in season-data/current.json')
    parser.add_argument('--season', help='Defaults to the season in season-data/current.json')
    parser.add_argument('--print-bundle', action='store_true', help='Print the active bundle directory and exit')
    parser.add_argument('--snapshot', action='store_true', help='Print candidate fingerprints only; not verification or automatic acceptance')
    parser.add_argument('--json', action='store_true', help='Emit observations and change rows for the refresh workflow')
    args = parser.parse_args()
    args.bundle = args.bundle or active_bundle(args.game, args.season)
    if args.print_bundle:
        print(args.bundle)
        return 0
    path = args.bundle / 'source-watch.json'
    baseline = json.loads(path.read_text()) if path.exists() else {'sources': {}}
    print(f'Baseline: {path}; {len(baseline.get("sources", {}))} saved sources; '
          f'captured at {baseline.get("captured_at", "unknown / no baseline")}.', file=sys.stderr, flush=True)
    current = capture(args.bundle)
    if args.snapshot:
        current['sources'] = {url: watch_record(value) for url, value in current['sources'].items()}
        for url, value in current['sources'].items():
            previous = baseline.get('sources', {}).get(url, {})
            if value.get('error') and previous.get('sha256'):
                value['sha256'] = previous['sha256']
                value['hash_captured_at'] = previous.get('hash_captured_at', baseline.get('captured_at'))
        print(json.dumps(current, indent=2))
        print('Candidate snapshot emitted; no baseline accepted and no recommendations updated.', file=sys.stderr)
        return 0  # Failures retain any previous digest; no new digest is invented.
    rows = compare(baseline, current)
    print(f'Source detection complete: compared {len(rows)} source records; '
          f'requires_refresh={str(exit_status(rows) != 0).lower()}; source_check_exit={exit_status(rows)}.',
          file=sys.stderr, flush=True)
    if args.json:
        print(json.dumps({'schema_version': 1, 'current': current, 'rows': rows,
                          'requires_refresh': exit_status(rows) != 0}, indent=2))
        return 0
    print(report(rows, current), end='')
    known = sum(bool(r.get('known_unavailable')) for r in rows)
    if known:
        print(f'::warning::{known} known source-access gaps remain unchecked; see the report.', file=sys.stderr)
    return exit_status(rows)


if __name__ == '__main__':
    raise SystemExit(main())
