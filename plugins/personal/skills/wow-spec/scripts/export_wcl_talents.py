#!/usr/bin/env python3
"""Read a CombatantInfo event from stdin; no vault access, secrets or file writes."""
import json
import argparse
import sys
import subprocess
import hashlib
from functools import lru_cache


def get(url):
    return subprocess.run(['curl', '-fsSL', '--max-time', '20', url], capture_output=True,
                          text=True, check=True).stdout


def lua(value):
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return '{' + ','.join(lua(v) for v in value) + '}'
    if isinstance(value, dict):
        return '{' + ','.join('[' + lua(k) + ']=' + lua(v) for k, v in value.items()) + '}'
    raise ValueError('Unsupported Lua input')


revision = '09b9db7948abc9b9648dedaab51eb0cf3ee67b31'
root = 'https://raw.githubusercontent.com/Gethe/wow-ui-source/' + revision + '/Interface/AddOns/'

setup = r'''
bit = {lshift=function(a,b) return a << b end, rshift=function(a,b) return a >> b end}
function tInvert(t) local r={} for k,v in pairs(t) do r[v]=k end return r end
function CreateAndInitFromMixin(m, ...) local r={} for k,v in pairs(m) do r[k]=v end r:Init(...) return r end
function getn(t) return #t end
table.getn=getn
StaticPopupDialogs={}
Enum={TraitNodeType={Selection=2,SubTreeSelection=3,Tiered=4}}
'''
adapter = r'''
local infos, entries = {}, {}
for _,id in ipairs(tree.fullNodeOrder) do
  local n=metadata[id]
  local info={ID=id,activeRank=0,ranksPurchased=0,maxRanks=n and (n.maxRanks or 1) or 1,type=0,entryIDs={}}
  if n then
    info.type=({choice=2,subtree=3,tiered=4})[n.type] or 0
    for _,e in ipairs(n.entries) do
      table.insert(info.entryIDs,e.id)
      entries[e.id]={maxRanks=e.maxRanks or 1}
      if expected[e.id] then
        info.activeRank=info.activeRank+expected[e.id]
        info.activeEntry={entryID=e.id}
      end
    end
    info.ranksPurchased=(n.freeNode and info.activeRank>0) and 0 or info.activeRank
  end
  infos[id]=info
end
C_Traits={
 GetTreeNodes=function() return tree.fullNodeOrder end,
 GetNodeInfo=function(_,id) return infos[id] end,
 GetEntryInfo=function(_,id) return entries[id] end
}
local codec=ClassTalentImportExportMixin
local knownStream=ExportUtil.MakeImportDataStream(known)
local valid,version,spec=codec:ReadLoadoutHeader(knownStream)
assert(valid and spec==tree.specId and version==2,'reference header mismatch')
local hash={} for i=1,16 do hash[i]=0 end
local out=ExportUtil.MakeExportDataStream()
codec:WriteLoadoutHeader(out,version,spec,hash)
codec:WriteLoadoutContent(out,1,tree.traitTreeId)
local encoded=out:GetExportString()
local back=ExportUtil.MakeImportDataStream(encoded)
local ok,v,s=codec:ReadLoadoutHeader(back)
assert(ok and v==version and s==spec)
local decoded=codec:ConvertToImportLoadoutEntryInfo(1,tree.traitTreeId,codec:ReadLoadoutContent(back,tree.traitTreeId))
local actual={}
for _,e in ipairs(decoded) do
  assert(actual[e.selectionEntryID]==nil,'duplicate decoded entry')
  actual[e.selectionEntryID]=e.ranksPurchased+e.ranksGranted
end
local count=0
for id,rank in pairs(expected) do assert(actual[id]==rank,'missing/wrong rank '..id) count=count+1 end
for id,rank in pairs(actual) do assert(expected[id]==rank,'extra entry '..id) end
print('ROUNDTRIP_ENTRIES '..count)
print('IMPORT '..encoded)
-- Independent source-provided string: decode and re-encode without changing it.
local ref=ExportUtil.MakeImportDataStream(known)
local rok,rv,rs,rh=codec:ReadLoadoutHeader(ref)
assert(rok and rv==version and rs==spec)
local refs=codec:ConvertToImportLoadoutEntryInfo(1,tree.traitTreeId,codec:ReadLoadoutContent(ref,tree.traitTreeId))
for _,info in pairs(infos) do info.activeRank=0 info.ranksPurchased=0 info.activeEntry=nil end
for _,e in ipairs(refs) do
 local info=infos[e.nodeID]
 info.activeRank=info.activeRank+e.ranksPurchased+e.ranksGranted
 info.ranksPurchased=info.ranksPurchased+e.ranksPurchased
 info.activeEntry={entryID=e.selectionEntryID}
end
local refout=ExportUtil.MakeExportDataStream()
codec:WriteLoadoutHeader(refout,rv,rs,rh)
codec:WriteLoadoutContent(refout,1,tree.traitTreeId)
if refout:GetExportString()~=known then print('REFERENCE_REENCODE '..refout:GetExportString()) end
assert(refout:GetExportString()==known,'source reference did not round-trip exactly')
print('SOURCE_STRING_ROUNDTRIP exact')
'''


@lru_cache(maxsize=1)
def tree_data():
    return json.loads(get('https://www.raidbots.com/static/data/live/talents.json'))


@lru_cache(maxsize=1)
def codec_sources():
    return (get(root + 'Blizzard_SharedXMLBase/ExportUtil.lua'),
            get(root + 'Blizzard_PlayerSpells/ClassTalents/Blizzard_ClassTalentImportExport.lua'))


def validate_event(event, snapshot):
    if event.get('specID') != snapshot['specId'] or not event.get('talentTree'):
        raise ValueError('Missing talents or wrong specialization')
    nodes = {n['id']: n for section in ['classNodes', 'specNodes', 'heroNodes', 'subTreeNodes']
             for n in snapshot[section]}
    expected, per_node = {}, {}
    for selection in event['talentTree']:
        entry_id, rank = selection['id'], selection['rank']
        node = nodes.get(selection['nodeID'])
        if not node or node['id'] not in snapshot['fullNodeOrder']:
            raise ValueError('Unknown node in current tree')
        entry = next((e for e in node['entries'] if e['id'] == entry_id), None)
        if (entry is None or type(rank) is not int or not 0 < rank <= entry.get('maxRanks', 1)
                or entry_id in expected):
            raise ValueError('Unknown/duplicate entry or invalid rank')
        expected[entry_id] = rank
        per_node.setdefault(node['id'], {})[entry_id] = rank
    for node_id, selections in per_node.items():
        node = nodes[node_id]
        if node['type'] != 'tiered' and len(selections) > 1:
            raise ValueError('Multiple selections in a non-tiered node')
        if node['type'] == 'tiered':
            remaining = sum(selections.values())
            for entry in node['entries']:
                wanted = min(remaining, entry['maxRanks'])
                if selections.get(entry['id'], 0) != wanted:
                    raise ValueError('Tiered ranks must fill entries in order')
                remaining -= wanted
    heroes = {nodes[i].get('subTreeId') for i in per_node if nodes[i].get('subTreeId')}
    if len(heroes) > 1:
        raise ValueError('Multiple hero trees')
    for node in snapshot['subTreeNodes']:
        for entry in node['entries']:
            if entry['id'] in expected and heroes and entry['traitSubTreeId'] not in heroes:
                raise ValueError('Hero selector does not match selected hero talents')
    return nodes, expected


def export_event(event, reference, lua_path='lua', snapshot=None):
    if snapshot is None:
        snapshot = next(t for t in tree_data() if t['specId'] == event['specID'])
    nodes, expected = validate_event(event, snapshot)
    export_util, class_export = codec_sources()
    program = (setup + export_util + class_export + '\ntree=' + lua(snapshot)
               + '\nmetadata=' + lua(nodes) + '\nexpected=' + lua(expected)
               + '\nknown=' + lua(reference) + '\n' + adapter)
    result = subprocess.run([lua_path, '-'], input=program, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('Codec validation failed (if the live talent data changed format, bump `revision` to a newer wow-ui-source commit): '
                         + result.stderr[:1200])
    lines = result.stdout.splitlines()
    exports = [line.removeprefix('IMPORT ') for line in lines if line.startswith('IMPORT ')]
    if len(exports) != 1 or 'SOURCE_STRING_ROUNDTRIP exact' not in lines:
        raise ValueError('Incomplete codec validation')
    return {'import_string': exports[0], 'roundtrip_entries': len(expected),
            'reference_roundtrip': 'exact', 'spec_id': event['specID'],
            'codec_revision': revision, 'tree_hash': 'zero',
            'metadata_sha256': hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(),
            'validation': 'codec fidelity; in-game import and full legality not tested'}


def main():
    parser = argparse.ArgumentParser(description='Export one CombatantInfo event with Blizzard\'s codec.')
    parser.add_argument('--reference', required=True, help='Known current import string for the same spec')
    parser.add_argument('--lua', default='lua', help='Installed Lua 5.3+ executable')
    args = parser.parse_args()
    try:
        result = export_event(json.load(sys.stdin), args.reference, args.lua)
    except (ValueError, KeyError, StopIteration, subprocess.SubprocessError, OSError) as error:
        print('Export failed: ' + str(error)[:1200], file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
