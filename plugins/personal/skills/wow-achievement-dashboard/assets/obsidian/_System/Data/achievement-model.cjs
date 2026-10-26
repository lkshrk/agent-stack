'use strict';
const labels = {'quick-wins':'Quick wins',buyable:'Buyable',grindable:'Grindable','long-term':'Long-term projects','needs-help':'Needs help',unclassified:'Unclassified / research'};
function taskId(task) {
  if(task.kind === 'action' || /^action-/.test(String(task.blockId || ''))) return null;
  const hit = String(task.blockId || '').match(/^ach-(\d+)$/) || String(task.text || '').match(/achievement=(\d+)/);
  return hit ? Number(hit[1]) : null;
}
function canonicalTasks(data, tasks) {
  const rows = new Map(data.tasks.map(row => [row.id, row])), found = new Map();
  const edition = data.game === 'classic-progression' ? 'mop-classic/' : '';
  for (const task of tasks) {
    const id = taskId(task), row = rows.get(id);
    if (!row) continue;
    const fail = message => {throw new Error('Canonical task ach-'+id+': '+message);};
    if (task.path !== row.source) fail('source path must match the dataset row.source');
    if (task.blockId !== 'ach-'+id) fail('missing or conflicting achievement block ID');
    const link = (String(task.text || '').match(/https?:\/\/[^\s<>()]+/g) || []).find(url => url.includes('achievement='));
    if (!wowheadLink(link, 'achievement', id, edition)) fail('achievement link must match the dataset game and block ID');
    if (found.has(id) && String(found.get(id).status || ' ') !== String(task.status || ' ')) fail('conflicting duplicate checkbox states');
    found.set(id, task);
  }
  return found;
}
function manualStates(data, tasks) {
  return new Map(Array.from(canonicalTasks(data, tasks), ([id, task]) => [id, String(task.status || ' ')]));
}
function status(row, states) {
  if (row.game_state === 'completed') return 'confirmed';
  if (row.game_state === 'unobtainable') return 'excluded';
  const value = states.get(row.id);
  if (/^[xX]$/.test(value || '')) return 'manual';
  if (value && value !== ' ') return 'excluded';
  return 'remaining';
}
function isActive(row, states) { return row.record_kind !== 'meta' && status(row, states) === 'remaining' && row.game_state === 'incomplete'; }
function select(rows, states, filters = {}) {
  return rows.filter(r => {
    if (!isActive(r, states)) return false;
    if (r.availability?.length && !filters.includeScheduled) return false;
    if (filters.pilotOnly && !r.pilot) return false;
    if (filters.categories?.length && !filters.categories.some(c => r.categories.includes(c))) return false;
    if (filters.character && !(r.characters || []).includes(filters.character)) return false;
    if (filters.players && r.players !== filters.players) return false;
    if (filters.readyOnly && r.readiness !== 'ready') return false;
    if (filters.budget != null && (!Number.isFinite(r.planning_max) || r.planning_max > filters.budget)) return false;
    return true;
  }).sort((a,b) => (a.planning_max ?? Infinity) - (b.planning_max ?? Infinity) || a.id - b.id);
}
function summarize(data, states) {
  const counts = {confirmed:0,manual:0,excluded:0,remaining:0,unknown:0};
  for (const row of data.tasks) {
    const st = status(row, states);
    counts[st]++;
    if (st === 'remaining' && row.game_state === 'unknown') counts.unknown++;
  }
  return {...counts,confirmedAP:data.account_ap,tracked:data.tasks.length};
}
function shopping(data, states) {
  const active = new Set(data.tasks.filter(r => isActive(r, states)).map(r => r.id));
  return data.shopping.map(item => {
    const required = item.allocations.filter(a => active.has(a.achievement_id)).reduce((sum,a)=>sum+a.quantity,0);
    const knownOwned = Number.isFinite(item.owned_usable);
    const buy = knownOwned ? Math.max(0,required-item.owned_usable) : null;
    return {...item,required,buy,max_cost:Number.isFinite(item.unit_cost)?(buy ?? required)*item.unit_cost:null};
  }).filter(r => r.required > 0);
}

function partition(data, tasks) {
  const states = manualStates(data, tasks), byId = canonicalTasks(data, tasks);
  const groups={execution:[],research:[],availability:[],manual:[],confirmed:[],unknown:[],excluded:[],missing:[]};
  for(const row of data.tasks) {
    const task=byId.get(row.id);
    if(!task){groups.missing.push(row);continue;}
    const item={row,task}, st=status(row,states);
    if(st==='confirmed'||st==='manual'||st==='excluded'){groups[st].push(item);continue;}
    if(row.game_state!=='incomplete'){groups.unknown.push(item);continue;}
    if(row.availability?.length){groups.availability.push(item);continue;}
    groups[row.record_kind==='meta' || row.queue==='research' || (!row.queue && row.source===data.research_source)?'research':'execution'].push(item);
  }
  return groups;
}


function wowheadLink(value, type, id, edition) {
  if(typeof value!=='string'||/[\u0000-\u0020\u007f\\]/.test(value))return false;
  const entity='/'+edition+'(?:[a-z]{2}/)?'+type+'='+id;
  if(!new RegExp('^https://www\\.wowhead\\.com'+entity+'(?:[/#?]|$)').test(value))return false;
  try{return new RegExp('^'+entity+'(?:/|$)').test(new URL(value).pathname);}catch{return false;}
}

function validate(data) {
  const fail=message=>{throw new Error('Achievement data: '+message);};
  const safePath=p=>typeof p==='string' && p.length>0 && !p.startsWith('/') && !p.includes('\\') && !p.split('/').includes('..') && !/^[a-z]+:/i.test(p);
  const nonnegative=n=>Number.isFinite(n)&&n>=0;
  if(data.schema_version!==1)fail('unsupported schema_version');
  const hasContext=['game','environment','expansion'].some(k=>Object.hasOwn(data,k));
  let edition='';
  if(hasContext){
    if(data.environment!=='live'||typeof data.expansion!=='string'||!data.expansion.trim())fail('unsupported game context; use plain Markdown goals');
    if(data.game==='classic-progression'&&data.expansion==='mists-of-pandaria')edition='mop-classic/';
    else if(data.game!=='retail')fail('unsupported game context; use plain Markdown goals');
  }
  if(data.account_ap!==null&&!nonnegative(data.account_ap))fail('account_ap must be a nonnegative number or null');
  if(!Array.isArray(data.tasks)||!Array.isArray(data.shopping)||!Array.isArray(data.characters)||!data.characters.every(x=>typeof x==='string'))fail('tasks, shopping and characters must be arrays');
  if(!Array.isArray(data.task_sources)||!data.task_sources.length||!data.task_sources.every(safePath)||new Set(data.task_sources).size!==data.task_sources.length)fail('task_sources must be unique vault-relative paths');
  if(!data.task_sources.includes(data.research_source))fail('research_source must be a canonical task source');
  if(data.validation?.reconciled && (!Number.isInteger(data.validation.completed_records)||data.validation.completed_records<0||data.account_ap===null||data.validation.calculated_completed_ap!==data.account_ap))fail('reconciled AP needs matching evidence');
  const ids=new Set();
  for(const row of data.tasks){
    if(!Number.isInteger(row.id)||row.id<=0||ids.has(row.id))fail('achievement IDs must be unique positive integers');
    ids.add(row.id);
    if(row.availability!==undefined && (!Array.isArray(row.availability) || !row.availability.every(a=>a&&typeof a==='object'&&Number.isInteger(a.expansion_order)&&a.expansion_order>=0&&['expansion','continent','area','what','check_note'].every(k=>typeof a[k]==='string')&&['tomtom','checks'].every(k=>Array.isArray(a[k])&&a[k].every(s=>typeof s==='string')))))fail('invalid availability rows');
    if(row.queue!==undefined&&!['execute','research'].includes(row.queue))fail('invalid queue');
    if(row.record_kind!==undefined&&!['achievement','meta'].includes(row.record_kind))fail('invalid record_kind');
    if(typeof row.name!=='string'||!row.name||!nonnegative(row.points))fail('invalid achievement name or points');
    if(!['completed','incomplete','unknown','unobtainable'].includes(row.game_state))fail('invalid game_state');
    if(!data.task_sources.includes(row.source)||!safePath(row.link))fail('task source/link must be within the vault');
    if(![row.source+'#^ach-'+row.id,row.source.replace(/\.md$/,'')+'#^ach-'+row.id].includes(row.link))fail('task link must match its canonical source and block ID');
    if(!wowheadLink(row.wowhead_url,'achievement',row.id,edition))fail('achievement link must match its game database and ID');
    if(!Array.isArray(row.categories)||!row.categories.length||!row.categories.every(c=>Object.hasOwn(labels,c)))fail('invalid categories');
    if(!Array.isArray(row.characters)||!row.characters.every(x=>typeof x==='string'))fail('characters must be an array');
    if(!['solo','group','unknown'].includes(row.players)||!['ready','conditional','verify','blocked'].includes(row.readiness))fail('invalid players/readiness');
    if(row.planning_max!==null&&(!nonnegative(row.planning_max)||row.planning_max===0))fail('planning_max must be positive or null');
    if(row.planning_min!==null&&(!nonnegative(row.planning_min)||row.planning_max===null||row.planning_min>row.planning_max))fail('invalid planning range');
  }
  const purchases=new Set();
  for(const item of data.shopping){
    if(typeof item.purchase_id!=='string'||!item.purchase_id||purchases.has(item.purchase_id))fail('purchase_id must be unique');
    purchases.add(item.purchase_id);
    if(!Number.isInteger(item.item_id)||item.item_id<=0||!wowheadLink(item.wowhead_url,'item',item.item_id,edition))fail('purchase item link must match its game database and item_id');
    if(typeof item.name!=='string'||typeof item.currency_label!=='string'||typeof item.character!=='string')fail('purchase labels must be strings');
    if(item.unit_cost!==null&&!nonnegative(item.unit_cost))fail('unit_cost must be nonnegative or null');
    if(item.owned_usable!==null&&!nonnegative(item.owned_usable))fail('owned_usable must be nonnegative or null');
    if(!Array.isArray(item.allocations)||!item.allocations.every(a=>ids.has(a.achievement_id)&&nonnegative(a.quantity)))fail('purchase allocations must reference known achievements');
  }
  return data;
}

module.exports = {labels,taskId,manualStates,status,isActive,select,summarize,shopping,partition,validate};
