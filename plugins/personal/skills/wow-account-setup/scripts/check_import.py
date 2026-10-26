#!/usr/bin/env python3
"""Standard-library checks; all mutations are confined to a temporary directory."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from import_saved_variables import parse_lua, read_lua, import_data as import_context_data, report


def import_data(*args, **kwargs):
    """All existing fixtures are explicitly verified Retail live exports."""
    return import_context_data(*args, game='retail', environment='live', **kwargs)

SKILL = Path(__file__).resolve().parent.parent
FIXTURES = SKILL / "tests" / "fixtures"
SCRIPT = SKILL / "scripts" / "import_saved_variables.py"
G1, G2 = "Player-1111-00000001", "Player-2222-00000002"


def rejects(call):
    try:
        call()
    except (ValueError, UnicodeError):
        return
    raise AssertionError("Invalid input was accepted")


def check_localized():
    collector_text = (FIXTURES / 'collector.lua').read_text(encoding='utf-8')
    att_text = (FIXTURES / 'att.lua').read_text(encoding='utf-8')
    for name, realm, location, region in [
        ('Mära', 'Die Aldor', 'Sturmwind', 'eu'),
        ('Мара', 'Гордунни', 'Штормград', 'eu'),
        ('마라', '아즈샤라', '스톰윈드', 'kr'),
        ('瑪拉', '銀翼要塞', '暴風城', 'tw'),
    ]:
        # Raw UTF-8 and Lua decimal-byte escapes must identify the same player.
        escaped = ''.join(f'\\{byte:03d}' for byte in name.encode('utf-8'))
        localized_c = collector_text.replace('"Ada"', f'"{escaped}"').replace(
            'level = 80,', f'level = 80, currentLocation = "{location}", bindLocation = "{location}",')
        localized_a = att_text.replace('"Ada"', f'"{name}"').replace('Test Realm One', realm).replace('Test Realm Two', realm + ' II')
        collector = parse_lua(localized_c)['WWTCSaved']
        att = parse_lua(localized_a)['ATTCharacterData']
        with tempfile.TemporaryDirectory(prefix='wow-localized-check-') as directory:
            root = Path(directory)
            cp, ap, output, markdown = [root / filename for filename in ('collector.lua', 'att.lua', 'account.json', 'coverage.md')]
            cp.write_text(localized_c, encoding='utf-8')
            ap.write_text(localized_a, encoding='utf-8')
            cmd = [sys.executable, str(SCRIPT), '--game', 'retail', '--environment', 'live', '--collector', str(cp), '--att', str(ap), '--account', name, '--region', region, '--output', str(output), '--report', str(markdown)]
            subprocess.run(cmd, capture_output=True, check=True)
            saved = json.loads(output.read_text(encoding='utf-8'))
            assert saved['account'] == name and saved['region'] == region
            assert set(saved['characters']) == {G1, G2}
            assert saved['coverage']['identities']['matched'] == 2
            for guid, expected_realm in [(G1, realm), (G2, realm + ' II')]:
                assert saved['characters'][guid]['identity']['name'] == name
                assert saved['characters'][guid]['identity']['realm'] == expected_realm
            fields = saved['characters'][G1]['observations']
            baseline = import_data(parse_lua(collector_text)['WWTCSaved'], att, name, region)
            for field in ('currencies', 'reputations', 'professions', 'achievements', 'inventory'):
                assert fields[field]['values'] == baseline['characters'][G1]['observations'][field]['values']
            assert fields['currentLocation']['values'] == fields['bindLocation']['values'] == location
            saved['user'] = {'notes': f'{name}: {location}', 'manual_completed_ids': [8]}
            saved['characters'][G1]['user'] = {'notes': location}
            repeated = import_data(collector, att, name, region, saved, saved['sources'])
            assert repeated['characters'] == saved['characters']
            assert repeated['coverage']['field_conflicts'] == 0
            output.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
            subprocess.run(cmd, capture_output=True, check=True)
            reloaded = json.loads(output.read_text(encoding='utf-8'))
            assert reloaded['characters'] == saved['characters'] and reloaded['user'] == saved['user']
            assert reloaded['account_observations'] == saved['account_observations']
            assert name in output.read_text(encoding='utf-8')
            assert f'{name}-{realm}' in markdown.read_text(encoding='utf-8')
            assert cp.read_text(encoding='utf-8') == localized_c and ap.read_text(encoding='utf-8') == localized_a


def check_context_and_invalid_snapshots(collector, att, previous):
    invalid = []
    for schema, game, environment in [(1, 'retail', None), (1, None, 'live'), (1, 'retail', 'live'),
                                       (2, 'retail', None), (2, None, 'live'), (2, None, None),
                                       (3, 'retail', 'live'), (2, 'retail', 'invalid')]:
        snapshot = copy.deepcopy(previous)
        snapshot['schema_version'] = schema
        for key, value in [('game', game), ('environment', environment)]:
            if value is None:
                snapshot.pop(key)
            else:
                snapshot[key] = value
        invalid.append(snapshot)
    for path in [('characters',), ('characters', G1), ('characters', G1, 'identity'),
                 ('characters', G1, 'observations'), ('characters', G1, 'observations', 'currencies'),
                 ('characters', G1, 'observations', 'currencies', 'scan_times'),
                 ('account_observations',), ('account_observations', 'reputations')]:
        for bad in ([], None, 'invalid'):
            snapshot = copy.deepcopy(previous)
            target = snapshot
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = bad
            invalid.append(snapshot)
    for snapshot in invalid:
        untouched = copy.deepcopy(snapshot)
        rejects(lambda: import_data(collector, att, 'demo', 'eu', snapshot, adopt_legacy_retail=True))
        assert snapshot == untouched
    rejects(lambda: import_context_data(collector, att, 'demo', 'eu', game='retail', environment='invalid'))

    with tempfile.TemporaryDirectory(prefix='wow-context-check-') as directory:
        root = Path(directory)
        cp, ap = root/'collector.lua', root/'att.lua'
        ap.write_bytes((FIXTURES/'att.lua').read_bytes())
        snapshots = {}
        for index, environment in enumerate(('live', 'ptr', 'beta')):
            # Synthetic compatible schemas exercise isolation, not real-client compatibility.
            cp.write_text((FIXTURES/'collector.lua').read_text().replace('level = 80,', f'level = {80 + index},'))
            output, markdown = root/f'{environment}.json', root/f'{environment}.md'
            cmd = [sys.executable, str(SCRIPT), '--game', 'retail', '--environment', environment,
                   '--collector', str(cp), '--att', str(ap), '--account', 'demo', '--region', 'eu',
                   '--output', str(output), '--report', str(markdown)]
            for _ in range(2):
                subprocess.run(cmd, capture_output=True, check=True)
                saved = json.loads(output.read_text())
                assert saved['characters'][G1]['observations']['level']['values'] == 80 + index
                assert saved['environment'] == environment and saved['coverage']['field_conflicts'] == 0
            snapshots[environment] = output.read_bytes()
            legacy = copy.deepcopy(saved)
            legacy['schema_version'] = 1
            del legacy['game'], legacy['environment']
            output.write_text(json.dumps(legacy))
            protected = {p: p.read_bytes() for p in (output, markdown, cp, ap)}
            failed = subprocess.run(cmd, capture_output=True, text=True)
            assert failed.returncode != 0 and '--adopt-legacy-retail' in failed.stderr
            assert all(p.read_bytes() == data for p, data in protected.items())
            subprocess.run(cmd + ['--adopt-legacy-retail'], capture_output=True, check=True)
            adopted = json.loads(output.read_text())
            assert adopted['environment'] == environment and adopted['schema_version'] == 2
            assert adopted['characters'] == saved['characters']
            snapshots[environment] = output.read_bytes()
        assert len({json.loads(data)['characters'][G1]['observations']['level']['values'] for data in snapshots.values()}) == 3
        for environment, data in snapshots.items():
            assert (root/f'{environment}.json').read_bytes() == data
        cmd[cmd.index('--environment') + 1] = 'live'
        rejected_cmd = cmd.copy()
        rejected_cmd[rejected_cmd.index('--environment') + 1] = 'invalid'
        protected = {p: p.read_bytes() for p in (output, markdown, cp, ap)}
        assert subprocess.run(rejected_cmd, capture_output=True).returncode != 0
        assert all(p.read_bytes() == data for p, data in protected.items())
        for snapshot in invalid:
            output.write_text(json.dumps(snapshot))
            protected = {p: p.read_bytes() for p in (output, markdown, cp, ap)}
            failed = subprocess.run(cmd + ['--adopt-legacy-retail'], capture_output=True, text=True)
            assert failed.returncode != 0 and 'Import failed:' in failed.stderr and 'Traceback' not in failed.stderr
            assert all(p.read_bytes() == data for p, data in protected.items())


def check():
    assert parse_lua('DB = {name="M\\195\\164ra", x=false, n=-4, a={1,2}, z=nil}')['DB']['name'] == 'Mära'
    for bad in ['DB=os.execute("bad")', 'DB={a=function() end}', 'DB={a=1,a=2}', 'DB={', 'DB={x=1e999}', 'DB={x="\\999"}']:
        rejects(lambda bad=bad: parse_lua(bad))
    c, sc = read_lua(FIXTURES / 'collector.lua')
    a, sa = read_lua(FIXTURES / 'att.lua')
    collector, att = c['WWTCSaved'], a['ATTCharacterData']
    first = import_data(collector, att, 'demo', 'eu', sources=[sc, sa])
    assert first['coverage']['current_characters'] == 2
    assert first['coverage']['identities']['matched'] == 2
    assert first['characters'][G1]['identity']['realm'] != first['characters'][G2]['identity']['realm']
    assert len(first['characters']) == 2  # ATT-only historic character is not imported.
    observed = first['characters'][G1]['observations']
    assert observed['currencies']['values']['3055']['quantity'] == 0
    assert '0' not in observed['currencies']['values']
    assert observed['reputations']['values']['942']['value'] == 12345
    assert observed['reputations']['scope'] == 'character'
    assert first['account_observations']['reputations']['scope'] == 'account'
    assert observed['professions']['status'] == 'partial'
    assert observed['professions']['observed_at'] is None
    assert observed['professions']['values']['2751']['known_skill_line_ability_ids'] == [111, 222]
    assert observed['achievements']['values']['7']['state'] == 'completed'
    assert observed['achievements']['scope'] == 'character' and first['account_ap'] is None
    assert observed['inventory']['values']['b0']['s1']['quantity'] == 2
    assert observed['inventory']['observed_at'] is None  # Bag and bank clocks differ.
    assert first['account_observations']['warbank']['values']['b13']['s1']['item_id'] == 1710
    assert 'Shared account observations' in report(first)

    # JSON round trips must not create conflicts or drop user-owned progress.
    first['user'] = {'notes': 'keep me', 'manual_completed_ids': [8]}
    first['characters'][G1]['user'] = {'deferred': True, 'actual_minutes': 65}
    previous = json.loads(json.dumps(first))
    again = import_data(collector, att, 'demo', 'eu', previous, [sc, sa])
    assert (again['schema_version'], again['game'], again['environment']) == (2, 'retail', 'live')
    assert 'retail / live' in report(again)
    # The same account, region and GUID must never join different client contexts.
    untouched = copy.deepcopy(previous)
    for game, environment in [('classic-era', 'live'), ('forever', 'live'), ('retail', 'ptr'), ('retail', 'beta')]:
        rejects(lambda: import_context_data(collector, att, 'demo', 'eu', previous, game=game, environment=environment))
    assert previous == untouched
    rejects(lambda: import_data(collector, att, 'demo', 'eu', {}))
    rejects(lambda: import_data(collector, att, 'demo', 'eu', [], adopt_legacy_retail=True))
    legacy = copy.deepcopy(previous)
    legacy['schema_version'] = 1
    del legacy['game'], legacy['environment']
    rejects(lambda: import_data(collector, att, 'demo', 'eu', legacy))
    adopted = import_data(collector, att, 'demo', 'eu', legacy, [sc, sa], adopt_legacy_retail=True)
    assert adopted['characters'] == previous['characters'] and adopted['user'] == previous['user']
    assert legacy['schema_version'] == 1 and 'game' not in legacy
    mismatched = copy.deepcopy(previous); mismatched['environment'] = 'ptr'
    rejects(lambda: import_data(collector, att, 'demo', 'eu', mismatched, adopt_legacy_retail=True))
    mismatched['environment'] = 'live'; mismatched['game'] = 'forever'
    rejects(lambda: import_data(collector, att, 'demo', 'eu', mismatched, adopt_legacy_retail=True))
    assert again['characters'] == previous['characters']
    assert again['account_observations'] == previous['account_observations']
    assert again['user'] == first['user']
    assert again['coverage']['field_conflicts'] == 0
    check_context_and_invalid_snapshots(collector, att, previous)

    partial = copy.deepcopy(collector)
    del partial['chars'][G2]
    del partial['chars'][G1]['currencies']
    del partial['chars'][G1]['copper']
    partial['chars'][G1]['level'] = 81
    partial['chars'][G1]['lastSeen'] += 100
    merged = import_data(partial, att, 'demo', 'eu', previous)
    assert not merged['characters'][G2]['present_in_latest_import']
    fields = merged['characters'][G1]['observations']
    assert fields['currencies']['values']['3055']['quantity'] == 0
    assert not fields['currencies']['present_in_latest_import']
    assert fields['copper']['values'] == 12000 and not fields['copper']['present_in_latest_import']
    assert fields['level']['values'] == 81
    assert merged['characters'][G1]['user']['deferred'] is True
    identity_retained = import_data(collector, {}, 'demo', 'eu', previous)
    assert identity_retained['characters'][G1]['identity']['name'] == 'Ada'
    assert not identity_retained['characters'][G1]['identity']['present_in_latest_import']

    changed = copy.deepcopy(collector)
    changed['chars'][G1]['currencies'][3055] = '50:100:0:0:0:0:0'
    conflict = import_data(changed, att, 'demo', 'eu', previous)
    assert conflict['characters'][G1]['observations']['currencies']['status'] == 'conflicting'
    assert conflict['characters'][G1]['observations']['currencies']['values']['3055']['quantity'] == 0
    changed['chars'][G1]['scanTimes']['currencies'] += 200
    updated = import_data(changed, att, 'demo', 'eu', conflict)
    assert updated['characters'][G1]['observations']['currencies']['values']['3055']['quantity'] == 50
    assert 'incoming' not in updated['characters'][G1]['observations']['currencies']
    stale = import_data(collector, att, 'demo', 'eu', updated)
    assert stale['characters'][G1]['observations']['currencies']['values']['3055']['quantity'] == 50
    assert stale['characters'][G1]['observations']['currencies']['status'] == 'conflicting'
    changed['chars'][G1]['currencies'][3055] = 'new:format'
    unsupported = import_data(changed, att, 'demo', 'eu')
    assert unsupported['characters'][G1]['observations']['currencies']['status'] == 'unsupported'
    changed['chars'][G1]['name'] = 'Different name'
    assert import_data(changed, att, 'demo', 'eu')['characters'][G1]['identity']['status'] == 'conflicting'
    assert import_data(collector, {}, 'demo', 'eu')['coverage']['identities']['unmatched'] == 2
    bad_att = copy.deepcopy(att); bad_att[G1]['guid'] = G2
    assert import_data(collector, bad_att, 'demo', 'eu')['characters'][G1]['identity']['status'] == 'conflicting'
    rejects(lambda: import_data(collector, att, 'other', 'eu', previous))
    rejects(lambda: import_data(collector, att, 'demo', 'us', previous))

    with tempfile.TemporaryDirectory(prefix='wow-import-check-') as directory:
        root = Path(directory)
        collector_path, att_path = root/'collector.lua', root/'att.lua'
        collector_path.write_bytes((FIXTURES/'collector.lua').read_bytes())
        att_path.write_bytes((FIXTURES/'att.lua').read_bytes())
        original_c, original_a = collector_path.read_bytes(), att_path.read_bytes()
        output, markdown = root/'account.json', root/'coverage.md'
        cmd = [sys.executable, str(SCRIPT), '--game', 'retail', '--environment', 'live', '--collector', str(collector_path), '--att', str(att_path), '--account', 'demo', '--region', 'eu', '--output', str(output), '--report', str(markdown)]
        unsupported_cmd = cmd.copy(); unsupported_cmd[unsupported_cmd.index('--game') + 1] = 'forever'
        failure = subprocess.run(unsupported_cmd, capture_output=True, text=True)
        assert failure.returncode != 0 and 'Unsupported game' in failure.stderr
        assert not output.exists() and not markdown.exists()
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        saved = json.loads(output.read_text()); saved['user'] = {'keep': 'manual progress'}
        output.write_text(json.dumps(saved))
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert json.loads(output.read_text())['user'] == saved['user']
        assert collector_path.read_bytes() == original_c and att_path.read_bytes() == original_a
        stable = output.read_bytes()
        stable_report = markdown.read_bytes()
        for flag, value in [('--game', 'classic-era'), ('--game', 'forever'), ('--environment', 'ptr'), ('--environment', 'beta')]:
            rejected_cmd = cmd.copy(); rejected_cmd[rejected_cmd.index(flag) + 1] = value
            assert subprocess.run(rejected_cmd, capture_output=True).returncode != 0
            assert output.read_bytes() == stable and markdown.read_bytes() == stable_report
            assert collector_path.read_bytes() == original_c and att_path.read_bytes() == original_a
        missing_context = cmd.copy()
        del missing_context[missing_context.index('--game'):missing_context.index('--game') + 2]
        assert subprocess.run(missing_context, capture_output=True).returncode != 0
        output.write_text(json.dumps(legacy))
        legacy_bytes = output.read_bytes()
        assert subprocess.run(cmd, capture_output=True).returncode != 0
        assert output.read_bytes() == legacy_bytes and markdown.read_bytes() == stable_report
        subprocess.run(cmd + ['--adopt-legacy-retail'], capture_output=True, check=True)
        migrated = json.loads(output.read_text())
        assert migrated['user'] == legacy['user'] and migrated['characters'] == legacy['characters']
        assert (migrated['game'], migrated['environment'], migrated['schema_version']) == ('retail', 'live', 2)
        stable = output.read_bytes()
        collector_path.write_text('WWTCSaved = os.execute("must not execute")')
        assert subprocess.run(cmd, capture_output=True).returncode != 0
        assert output.read_bytes() == stable
        collector_path.write_bytes(original_c)
        bad_cmd = cmd.copy(); bad_cmd[bad_cmd.index('--output') + 1] = str(collector_path)
        assert subprocess.run(bad_cmd, capture_output=True).returncode != 0
        assert collector_path.read_bytes() == original_c
        markdown.write_text('User-authored notes')
        assert subprocess.run(cmd, capture_output=True).returncode != 0
        assert output.read_bytes() == stable and markdown.read_text() == 'User-authored notes'
    check_localized()
    print('PASS: safe parsing, exact identities, partial coverage, timestamps, repeated/partial imports, conflicts, manual-progress preservation, localized UTF-8 state, game/environment isolation, explicit legacy adoption and CLI write safety.')


if __name__ == '__main__':
    check()
