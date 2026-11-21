// Native CLT pipeline + runtime admission, isolated forward handoff. No alerts/orders.
const fs=require('fs'),path=require('path'),crypto=require('crypto'),Module=require('module');
const home=path.resolve(process.argv[2]);
const dependencyRoot='C:/Users/piard/.gemini/antigravity/scratch/swing-bos-core';
const isolatedRoot=path.join(home,'../native/ChecklistTrigger');
const root=fs.existsSync(isolatedRoot)?isolatedRoot:dependencyRoot;
process.env.ENABLE_TELEMETRY='false';process.env.ENABLE_RESEARCH_LEDGER='false';
process.env.IS_REPLAY='false';
process.env.NODE_PATH=path.join(dependencyRoot,'node_modules');Module._initPaths();
process.env.MEGALORD_ENTRY_INTENTS_PATH=path.join(home,'native_bridge.jsonl');
const hash=x=>crypto.createHash('sha256').update(x).digest('hex');
const flags=['ENABLE_KILLZONE','DISABLE_KILLZONE_7_24','ENABLE_PVP_SIGNAL_ACCELERATION','ENABLE_SIGNAL_QUALITY_ENGINE','ENABLE_DISTANCE_FILTER','ENABLE_PVP_KILLZONE_BYPASS','REQUIRE_ENTRY_REACTION'];
const flagPath=path.join(home,'selected_flags.json');
if(!fs.existsSync(flagPath)){
 const selected={};
 for(const line of fs.readFileSync(path.join(dependencyRoot,'.env'),'utf8').split(/\r?\n/)){
  const m=line.match(/^\s*([A-Z_0-9]+)\s*=\s*["']?(true|false)["']?\s*(?:#.*)?$/);
  if(m&&flags.includes(m[1]))selected[m[1]]=m[2];
  const number=line.match(/^\s*MAX_SIGNAL_DATA_AGE_MINUTES\s*=\s*["']?(\d+(?:\.\d+)?)["']?\s*(?:#.*)?$/);
  if(number)selected.MAX_SIGNAL_DATA_AGE_MINUTES=number[1];
 }
 fs.writeFileSync(flagPath,JSON.stringify(selected,null,2));
}
const selected=JSON.parse(fs.readFileSync(flagPath,'utf8'));
for(const [k,v] of Object.entries(selected))process.env[k]=v;
const compiled=path.join(home,'compiled');
if(!fs.existsSync(path.join(home,'compiled_manifest.json'))){
 const ts=require(path.join(dependencyRoot,'node_modules/typescript')),manifest={};
 function compile(dir){for(const item of fs.readdirSync(path.join(root,dir),{withFileTypes:true})){
  const rel=path.join(dir,item.name);if(item.isDirectory()){compile(rel);continue;}
  if(!rel.endsWith('.ts')||rel.endsWith('.d.ts'))continue;
  const data=fs.readFileSync(path.join(root,rel));manifest[rel]=hash(data);
  const target=path.join(compiled,rel.replace(/\.ts$/,'.js'));
  fs.mkdirSync(path.dirname(target),{recursive:true});
  fs.writeFileSync(target,ts.transpileModule(data.toString('utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText);
 }}compile('src');compile('server');
 fs.writeFileSync(path.join(compiled,'package.json'),'{"type":"commonjs"}');
 fs.writeFileSync(path.join(home,'compiled_manifest.json'),JSON.stringify(manifest,null,2));
}
for(const [rel,digest] of Object.entries(JSON.parse(fs.readFileSync(path.join(home,'compiled_manifest.json'),'utf8')))){
 if(hash(fs.readFileSync(path.join(root,rel)))!==digest)throw Error('Native CLT source changed: '+rel);
}
global.fetch=()=>{throw Error('Network prohibited in isolated CLT worker');};
const {runPipeline}=require(path.join(compiled,'server/pipeline.js'));
const {defaultMarketDataOutcomeTracker}=require(path.join(compiled,'src/signalOutcomeTracker.js'));
// The worker publishes admitted intents; native outcome tracking is deliberately
// omitted here. Megalord quote/portfolio tracking is a separate responsibility.
defaultMarketDataOutcomeTracker.register=()=>undefined;
const {runRuntimeExecutionPipeline}=require(path.join(compiled,'server/runtimeExecutionPipeline.js'));
const input=JSON.parse(fs.readFileSync(path.join(home,'input.json'),'utf8'));
const stateFile=path.join(home,'dedup.json');
const state=fs.existsSync(stateFile)?JSON.parse(fs.readFileSync(stateFile,'utf8')):{seen:[],impulses:{}};
const seen=new Set(state.seen),impulses=state.impulses;
const notifications={hasBeenNotified:k=>seen.has(k),hasImpulseOrNewerBeenNotified:(s,t)=>(impulses[s]??-Infinity)>=t};
const results=[];
for(const symbol of ['EURUSD','GBPUSD','AUDUSD']){
 const frames=input.frames[symbol],clock=Date.parse(input.observed_at);
 const store={getCandles:(s,tf)=>frames[tf].filter(c=>c.timestamp+({'15m':900000,'1h':3600000,'4h':14400000}[tf])<=clock).slice(-500)};
 const candidates=runPipeline(symbol,store,notifications,clock);
 let admitted=0;
 for(const candidate of candidates){
  const result=runRuntimeExecutionPipeline(candidate);
  if(result.riskResult.items[0]?.evaluation.executionAllowed)admitted++;
  seen.add(candidate.uniqueKey);if(candidate.dedupeKey)seen.add(candidate.dedupeKey);
  impulses[symbol]=candidate.poi.relatedEvent.breakTimestamp;
 }
 results.push({symbol,candidates:candidates.length,admitted});
}
fs.writeFileSync(stateFile,JSON.stringify({seen:[...seen],impulses}));
// Bridge output is copied without outcomes into the common forward contract.
const nativeFile=path.join(home,'native_bridge.jsonl');
const commonFile=path.join(home,'intents.jsonl');
const existing=new Set(fs.existsSync(commonFile)?fs.readFileSync(commonFile,'utf8').trim().split('\n').filter(Boolean).map(l=>JSON.parse(l).signal_id):[]);
if(fs.existsSync(nativeFile))for(const line of fs.readFileSync(nativeFile,'utf8').split('\n').filter(Boolean)){
 const row=JSON.parse(line);if(existing.has(row.signal_id))continue;
 fs.appendFileSync(commonFile,JSON.stringify({...row,source_kind:'NATIVE_FORWARD_INTENT',engine:'ChecklistTrigger',
  origin_at:row.entry_time,sub_strategy:row.poi_type,metadata:{status:'PENDING_LIMIT',grade:row.grade},broker_fill_verified:false})+'\n');
 existing.add(row.signal_id);
}
fs.writeFileSync(path.join(home,'native_result.json'),JSON.stringify({symbols:results,bridge_compiled:true,orders_enabled:false,notifications_enabled:false},null,2));
