#!/usr/bin/env python3
"""Fill catalogue effect, duration, cooldown and quality fields from public Wowhead tooltip JSON."""
import argparse
import ast
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


FORMULA = re.compile(r'\([\d.\s+\-*/()]*\)')
UNEVALUATED = re.compile(r'\(\s*[\d.]*\s*[*/+]|[*/]\s*[\d.]+\s*\)|\(\s+[\d.]+\s+\)|\$\w+|\{\$')
OPERATORS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
             ast.Div: lambda a, b: a / b}


def evaluate(expression):
    def walk(node, depth=0):
        if depth > 8:
            raise ValueError('too deep')
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand, depth + 1)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            return OPERATORS[type(node.op)](walk(node.left, depth + 1), walk(node.right, depth + 1))
        raise ValueError('unsupported expression')
    if len(expression) > 80:
        raise ValueError('expression too long')
    return walk(ast.parse(expression.strip(), mode='eval').body)


def resolve_formulas(text):
    def substitute(match):
        if not re.search(r'[*/+]|^\(\s', match.group(0)):
            return match.group(0)
        try:
            return str(int(evaluate(match.group(0)) + 0.5))
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, RecursionError):
            raise ValueError('Unevaluable expression: ' + match.group(0).strip())
    return FORMULA.sub(substitute, text)


def clean_effect(effect, item_id):
    """Return (effect or None, note or None)."""
    try:
        effect = resolve_formulas(effect)
    except ValueError as error:
        return None, str(error)
    if UNEVALUATED.search(effect):
        return None, 'Tooltip effect keeps an unevaluated expression'
    if item_id is not None and re.search(rf'(?<![\d.]){item_id}(?![\d.])', effect):
        return None, f'Tooltip effect amount equals the item id {item_id}; real amount not derivable'
    return effect, None


def parse_tooltip(data, item_id=None):
    tooltip = data.get('tooltip') or ''
    found = {}
    use = re.search(r'<span id="useText\d*"[^>]*>(.*?)</span>\s*<!--useText', tooltip, re.S)
    if use:
        effect = re.sub(r'^Use:\s*', '', text_of(use.group(1)))
        raw, (effect, note) = effect, clean_effect(effect, item_id)
        if note:
            found['unresolved_note'] = note
        else:
            found['effect'] = effect
        duration = re.search(r'\bfor (\d+(?:\.\d+)?) (sec|seconds?|min|minutes?|hours?|hr)\b', raw, re.I)
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
        parsed = parse_tooltip(data, int(key.split(':')[1]))
        note = parsed.pop('unresolved_note', None)
        if note and note not in record.get('unresolved', []):
            record['unresolved'] = [*record.get('unresolved', []), note]
            changes.setdefault(key, {})['unresolved'] = record['unresolved']
        if record.get('category') != 'consumable':
            parsed = {k: v for k, v in parsed.items() if k in ('effect', 'quality_rank')}
        gained = {k: v for k, v in parsed.items() if record.get(k) is None}
        if not gained:
            continue
        changes.setdefault(key, {}).update(gained)
        record.update(gained)
        remaining = [u for u in record.get('unknown_fields', []) if u not in {FIELDS[k] for k in gained}]
        record['unknown_fields'] = remaining
        record['tooltip_enriched_at'] = today
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
