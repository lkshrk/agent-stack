#!/usr/bin/env python3
"""Fill catalogue effect, duration, cooldown and quality fields from public Wowhead tooltip JSON."""
import argparse
import html
import json
from pathlib import Path
import re
import subprocess
import sys

from check_wow_season_updates import active_bundle

FIELDS = {'effect': 'effect', 'duration_seconds': 'duration', 'cooldown_seconds': 'cooldown and sharing',
          'quality_rank': 'quality rank'}
UNITS = {'sec': 1, 'second': 1, 'min': 60, 'minute': 60, 'hour': 3600, 'hr': 3600}
TOOLTIP = 'https://nether.wowhead.com/tooltip/{kind}/{id}'


def seconds(amount, unit):
    return int(float(amount) * UNITS[re.sub(r's$', '', unit.casefold())])


def text_of(fragment):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', fragment)).split())


def parse_tooltip(data):
    tooltip = data.get('tooltip') or ''
    found = {}
    use = re.search(r'<span id="useText\d*"[^>]*>(.*?)</span>\s*<!--useText', tooltip, re.S)
    if use:
        effect = re.sub(r'^Use:\s*', '', text_of(use.group(1)))
        found['effect'] = effect
        duration = re.search(r'\bfor (\d+(?:\.\d+)?) (sec|seconds?|min|minutes?|hours?|hr)\b', effect, re.I)
        if duration:
            found['duration_seconds'] = seconds(*duration.groups())
    cooldown = re.search(r'\((\d+(?:\.\d+)?) (Sec|Min|Hr)s?\.? Cooldown\)', text_of(tooltip), re.I)
    if cooldown:
        found['cooldown_seconds'] = seconds(*cooldown.groups())
    rank = re.search(r'professions-icon-quality-tier(\d)|crafting-quality[^>]*tier-?(\d)', tooltip)
    if rank:
        found['quality_rank'] = int(rank.group(1) or rank.group(2))
    return found


def fetch(kind, identity):
    url = TOOLTIP.format(kind=kind, id=identity)
    result = subprocess.run(['curl', '-fsSL', '--max-time', '20', url], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Tooltip unavailable: ' + url)
    return json.loads(result.stdout)


def lacking(record):
    return [field for field in FIELDS if record.get(field) is None]


def enrich(catalog, today, fetcher=fetch, only=None):
    """Return {key: filled fields}; mutates catalog records in place."""
    changes = {}
    for key, record in catalog['records'].items():
        if only and key not in only:
            continue
        if not key.startswith('item:') or not lacking(record):
            continue
        try:
            data = fetcher('item', key.split(':')[1])
        except (ValueError, json.JSONDecodeError):
            continue
        if data.get('name') != record.get('name') and re.sub(r'\W', '', str(data.get('name'))).casefold() \
                != re.sub(r'\W', '', record.get('name', '')).casefold():
            continue
        parsed = parse_tooltip(data)
        if record.get('category') != 'consumable':
            parsed = {k: v for k, v in parsed.items() if k in ('effect', 'quality_rank')}
        gained = {k: v for k, v in parsed.items() if record.get(k) is None}
        if not gained:
            continue
        record.update(gained)
        remaining = [u for u in record.get('unknown_fields', []) if u not in {FIELDS[k] for k in gained}]
        record['unknown_fields'] = remaining
        record['tooltip_enriched_at'] = today
        changes[key] = gained
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--item', action='append', help='Limit to item ids, e.g. 271887')
    parser.add_argument('--write', action='store_true', help='Persist changes (default is a dry run)')
    args = parser.parse_args()
    bundle = args.bundle or active_bundle()
    path = bundle / 'catalog.json'
    catalog = json.loads(path.read_text())
    from datetime import date
    changes = enrich(catalog, date.today().isoformat(), only={'item:' + i for i in args.item or []})
    print(json.dumps(changes, indent=2), file=sys.stdout)
    if args.write and changes:
        path.write_text(json.dumps(catalog, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
