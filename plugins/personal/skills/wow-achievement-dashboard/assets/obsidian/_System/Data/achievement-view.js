if(!input?.dataPath || !input?.modelPath) throw new Error('Configure dataPath and modelPath in this note.');
const raw = await dv.io.load(input.dataPath);
if(!raw) throw new Error('Achievement dataset missing: '+input.dataPath);
const code = await dv.io.load(input.modelPath);
if(!code) throw new Error('Achievement model missing: '+input.modelPath);
const loaded = {exports:{}};
new Function('module',code)(loaded);
const m=loaded.exports;
const data=m.validate(JSON.parse(raw));
const pages=data.task_sources.map(path=>dv.page(path));
if(pages.some(p=>!p)) throw new Error('Source note missing or not yet indexed. Reopen this view after Obsidian indexes the configured task sources.');
const tasks=pages.flatMap(p=>Array.from(p.file.tasks || []));
const states = m.manualStates(data,tasks);
const missing=m.partition(data,tasks).missing;
if(missing.length)throw new Error('Canonical source tasks missing or not indexed: '+missing.slice(0,10).map(r=>r.id).join(', '));
const root = dv.el('div', '', {cls:'wow-achievements'});
function el(parent, tag, text='', cls='') {const node=document.createElement(tag);node.textContent=text;if(cls)node.className=cls;parent.appendChild(node);return node;}
function link(parent,text,target,external=false) {const a=el(parent,'a',text);if(external&&!/^https:\/\//.test(target))throw new Error('Unsupported external link');a.href=target;if(external){a.target='_blank';a.rel='noopener noreferrer';}else{a.className='internal-link';a.dataset.href=target;}return a;}
function fmtTime(row){return row.planning_max == null ? 'Unpriced / verify' : (row.planning_min == null ? 'Up to '+row.planning_max+' min' : row.planning_min+'–'+row.planning_max+' min');}
const style=el(root,'style');
style.textContent='.wow-achievements .controls{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.wow-achievements label{display:flex;gap:6px;align-items:center}.wow-achievements .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px}.wow-achievements .metric{padding:12px;border:1px solid var(--background-modifier-border);border-radius:8px}.wow-achievements .value{font-size:1.65em;font-weight:700}.wow-achievements .muted{color:var(--text-muted)}.wow-achievements .scroll{overflow-x:auto}.wow-achievements table{width:100%}.wow-achievements td,.wow-achievements th{vertical-align:top}.wow-achievements progress{width:100%;accent-color:var(--interactive-accent)}';
if(data.is_demo) el(root,'p','DEMO DATA — synthetic examples, not your account. Replace the dataset before planning.','muted');
if(input?.mode === 'availability'){
 const rows=data.tasks.filter(r=>r.game_state==='incomplete'&&m.status(r,states)==='remaining').flatMap(row=>(row.availability||[]).map(a=>({...a,row}))).sort((a,b)=>a.expansion_order-b.expansion_order||a.continent.localeCompare(b.continent)||a.area.localeCompare(b.area)||a.row.name.localeCompare(b.row.name));
 el(root,'p',rows.length+' checks · sorted by expansion, continent and area. This note does not query the game. Run a check on the character you intend to use.');
 const wrap=el(root,'div','','scroll'),table=el(wrap,'table'),head=el(table,'tr');
 ['Achievement','Expansion · Continent / Area','TomTom','What / who','Check if up'].forEach(t=>el(head,'th',t));
 function commands(cell,values){
  for(const command of values){
   const pre=el(cell,'pre');pre.style.whiteSpace='pre';pre.style.maxWidth='23rem';pre.style.overflowX='auto';pre.style.margin='0.25rem 0';el(pre,'code',command);
   const button=el(cell,'button','Copy');button.type='button';button.setAttribute('aria-label','Copy command');
   button.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(command);button.textContent='Copied';}catch{button.textContent='Select the command to copy';}});
  }
 }
 for(const a of rows){
  const tr=el(table,'tr'),name=el(tr,'td');link(name,a.row.name,a.row.wowhead_url,true);el(name,'br');link(name,'Details / canonical task',a.row.link);
  el(tr,'td',a.expansion+' · '+a.continent+' / '+a.area);
  const way=el(tr,'td');commands(way,a.tomtom);if(!a.tomtom.length)el(way,'span','Waypoint not verified; use the indicated map/NPC check.');
  el(tr,'td',a.what);const check=el(tr,'td');commands(check,a.checks);const scope=el(check,'details');el(scope,'summary',a.checks.length?'Check scope':'Manual check');el(scope,'p',a.check_note);
 }
 if(!rows.length)el(root,'p','No remaining availability-gated work.');
}else if(['todo','backlog','completed'].includes(input?.mode)){
 const groups=m.partition(data,tasks);
 if(groups.availability.length)el(root,'p',groups.availability.length+' achievements are separated into the availability-check table.','muted');
 if(groups.missing.length)el(root,'p',groups.missing.length+' records have no indexed source task; check source notes before treating this as complete.','muted');
 const showTasks=(title,items)=>{
  dv.header(2,title+' · '+items.length);
  if(items.length)dv.taskList(items.map(item=>item.task),false);
  else dv.paragraph('None.');
 };
 if(input.mode==='completed'){
  const table=el(el(root,'div','','scroll'),'table'),head=el(table,'tr');
  ['Confirmed in game · '+groups.confirmed.length,'AP','Source / details'].forEach(x=>el(head,'th',x));
  for(const {row}of groups.confirmed){
   const tr=el(table,'tr');link(el(tr,'td'),row.name,row.wowhead_url,true);el(tr,'td',String(row.points));link(el(tr,'td'),'Open record',row.link);
  }
  el(root,'p','Game-confirmed completions follow the imported snapshot. Unchecking a source box cannot undo an achievement in the game.','muted');
  showTasks('Checked by you — awaiting game confirmation',groups.manual);
 }else{
  const items=input.mode==='todo'?groups.execution:groups.research;
  el(root,'p','Live task view: check a box to update its source record. It leaves this view and appears under manually reported completion. No file is moved or copied.','muted');
  if(input.mode==='todo'){
   showTasks('Curated tasks',items.filter(item=>item.row.pilot));
   showTasks('Remaining execution work — review old instructions',items.filter(item=>!item.row.pilot));
  }else showTasks('Research backlog — instructions need validation',items);
  if(groups.unknown.length)el(root,'p',groups.unknown.length+' source-state records are unknown; see source notes for verification.','muted');
  if(groups.excluded.length)el(root,'p',groups.excluded.length+' tasks have cancelled/deferred checkbox statuses and remain in their source notes.','muted');
 }
}else if(input?.mode === 'shopping'){
 const rows=m.shopping(data,states);
 el(root,'p','Quantities follow unchecked canonical tasks. Unknown owned stock stays unknown; the listed purchase amount is an upper bound.');
 if(!rows.length) el(root,'p','No remaining purchases in this plan.');
 else{
  const table=el(el(root,'div','','scroll'),'table');const head=el(table,'tr');
  ['Buy','Still required','Owned usable','Buy after stock check','Budget','Used by'].forEach(x=>el(head,'th',x));
  for(const r of rows){const tr=el(table,'tr');link(el(tr,'td'),r.name,r.wowhead_url,true);el(tr,'td',String(r.required));el(tr,'td',r.owned_usable == null?'Unknown':String(r.owned_usable));el(tr,'td',r.buy == null?'0–'+r.required:String(r.buy));el(tr,'td',r.max_cost == null?'Price unknown':'Up to '+r.max_cost+' '+r.currency_label);el(tr,'td',r.character);}
 }
 if(data.shopping_note)el(root,'p',data.shopping_note,'muted');
 el(root,'p','No automatic spending, inventory refresh or website connection occurs when this view renders.','muted');
}else{
 const summary=m.summarize(data,states);
 const metrics=el(root,'div','','metrics');
 for(const [title,value,detail] of [
  ['Confirmed account AP',summary.confirmedAP == null ? 'Unavailable' : summary.confirmedAP.toLocaleString(),'Imported game data only'],
  ['Tracked achievements',summary.tracked,'Configured source records'],
  ['Confirmed complete',summary.confirmed,'Within the tracked set'],
  ['Manually reported',summary.manual,'Checked since the snapshot'],
  ['Remaining tracked',summary.remaining,'Includes '+summary.unknown+' unknown-state records']
 ]){const card=el(metrics,'div','','metric');el(card,'div',title);el(card,'div',String(value),'value');el(card,'div',detail,'muted');}
 const progress=el(root,'progress');progress.max=Math.max(1,summary.tracked);progress.value=summary.confirmed+summary.manual;
 progress.setAttribute('aria-label','Confirmed or manually reported completion within the tracked set');
 el(root,'p','Snapshot '+(data.snapshot_date || 'unavailable')+' · '+(data.validation?.reconciled ? 'account AP reconciled against '+data.validation.completed_records+' completed records' : 'account AP reconciliation not confirmed')+'. Progress applies to this tracked set, not all possible achievements.','muted');
 el(root,'h2','What can I do next?');
 const controls=el(root,'div','','controls');
 function selectControl(title,options){const label=el(controls,'label',title);const s=el(label,'select');for(const [value,text] of options){const o=el(s,'option',text);o.value=value;}return s;}
 const budget=selectControl('Total session time',[['','All / include unknown'],['15','15 min'],['30','30 min'],['60','60 min'],['90','90 min']]);
 const character=selectControl('Character',[['','Any'],...data.characters.map(c=>[c,c])]);
 const players=selectControl('Players',[['','Any'],['solo','Solo'],['group','Needs helpers'],['unknown','Unverified']]);
 const readiness=selectControl('Readiness',[['all','Include preparation / verification'],['ready','Ready now only']]);
 const scope=selectControl('Scope',data.tasks.some(r=>r.pilot)?[['pilot','Curated tasks'],['all','All tracked work']]:[['all','All tracked work']]);
 const catbox=el(root,'div','','controls');const cats=[];
 for(const [key,label] of Object.entries(m.labels)){const l=el(catbox,'label');const box=el(l,'input');box.type='checkbox';box.value=key;box.checked=true;el(l,'span',label);cats.push(box);}
 const result=el(root,'div');
 function render(){
  result.replaceChildren();
  const selected=cats.filter(c=>c.checked).map(c=>c.value);
  const rows=selected.length?m.select(data.tasks,states,{categories:selected,budget:budget.value===''?null:Number(budget.value),character:character.value,players:players.value,readyOnly:readiness.value==='ready',pilotOnly:scope.value==='pilot'}):[];
  el(result,'p',rows.length+' matching achievements. Times are completion budgets, including travel/setup; recurring session targets and unknown completion times do not pass a timed filter.');
  if(!rows.length){el(result,'p','No verified match. Include preparation, increase the budget, or clear the character filter. Unknown timings are available with “All / include unknown”.');return;}
  const table=el(el(result,'div','','scroll'),'table');const head=el(table,'tr');
  ['Achievement','Categories','Plan','AP','Character','Readiness / next step'].forEach(x=>el(head,'th',x));
  for(const r of rows){const tr=el(table,'tr');const name=el(tr,'td');link(name,r.name,r.wowhead_url,true);el(name,'br');link(name,'Open task',r.link);
   el(tr,'td',r.categories.map(k=>m.labels[k]||k).join(', '));el(tr,'td',fmtTime(r));el(tr,'td',String(r.points));
   el(tr,'td',r.characters.join(', ')||'Not yet evaluated');el(tr,'td',r.next_step||'Research and reprice before planning.');
  }
 }
 for(const node of [budget,character,players,readiness,scope,...cats])node.addEventListener('change',render);
 render();
 el(root,'h2','Research coverage');
 const remaining=data.tasks.filter(r=>m.isActive(r,states));
 for(const [key,label] of Object.entries(m.labels)){const n=remaining.filter(r=>r.categories.includes(key)).length;const l=el(root,'label',label+' · '+n);const p=el(l,'progress');p.max=remaining.length||1;p.value=n;p.setAttribute('aria-label',label+': '+n+' remaining tracked achievements');}
 el(root,'p','Category memberships overlap; counts are not additive. Unclassified work remains in the research source. Individual AP values exclude unverified meta bonuses and must not be summed across dependent alternatives.','muted');
}
