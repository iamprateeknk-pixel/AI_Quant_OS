import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import ts from "typescript";

const source=()=>readFileSync(new URL("../src/features/incidents/monitoring-journal.ts",import.meta.url),"utf8");
const compileRaw=text=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(text,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString("base64")}`;
const alerts=()=>compileRaw(readFileSync(new URL("../src/features/incidents/monitoring-alerts.ts",import.meta.url),"utf8"));
const compile=text=>compileRaw(text.replace('from "./monitoring-alerts"',`from ${JSON.stringify(alerts())}`));
const head="a".repeat(64);
const episode={episode_id:"4f2e03a6-7dda-560a-b4fe-64e82af8b0b1",rule_name:"PaperHttpScrapeFailed",first_observed_at:"2026-10-12T00:00:00+00:00",active_since:"2026-10-11T23:57:00+00:00",state:"FIRING",last_observed_at:"2026-10-12T00:00:00+00:00",runbook_path:"docs/runbooks/paper-http-monitoring.md#scrape-failed",acknowledgement:null};
const page={schema_version:"1.0",mode:"PAPER",source_kind:"LOCAL_PROMETHEUS_RULE_EVALUATION",durable:true,notification_verified:false,independently_attested:false,head_hash:head,total:1,offset:0,limit:50,next_offset:null,episodes:[episode]};

test("monitoring history decoder selects the fixed original page contract",async()=>{
 const {parseMonitoringJournal}=await import(compile(source()));
 assert.deepEqual(parseMonitoringJournal({...page,private_error:"hidden"}),null);
 assert.deepEqual(parseMonitoringJournal(page),page);
 assert.deepEqual(parseMonitoringJournal({...page,notification_verified:true}),null);
 assert.deepEqual(parseMonitoringJournal({...page,episodes:[{...episode,runbook_path:"https://invalid.test"}]}),null);
 assert.deepEqual(parseMonitoringJournal({...page,episodes:[{...episode,extra:"private"}]}),null);
});

test("monitoring history pagination and receipt authority are internally coherent",async()=>{
 const {parseMonitoringJournal}=await import(compile(source()));
 const later={...page,total:101,offset:50,next_offset:100,episodes:Array.from({length:50},(_,index)=>({...episode,episode_id:`00000000-0000-5000-8000-${String(index+1).padStart(12,"0")}`}))};
 assert.deepEqual(parseMonitoringJournal(later),later);
 const validPage={...page,total:51,offset:50,next_offset:null,episodes:[episode]};
 assert.deepEqual(parseMonitoringJournal(validPage),validPage);
 const receipt={schema_version:"1.0",mode:"PAPER",committed:true,control_authorized:false,action_id:"10bb3203-550b-4cf1-bcca-83f764e1d009",episode_id:episode.episode_id,actor_session_id:"20bb3203-550b-4cf1-bcca-83f764e1d009",acknowledged_at:"2026-10-12T00:00:01+00:00",source_head:head};
 const acknowledged={...page,episodes:[{...episode,acknowledgement:receipt}]};
 assert.deepEqual(parseMonitoringJournal(acknowledged),acknowledged);
 assert.equal(parseMonitoringJournal({...acknowledged,episodes:[{...episode,acknowledgement:{...receipt,control_authorized:true}}]}),null);
 assert.equal(parseMonitoringJournal({...page,next_offset:50}),null);
 assert.equal(parseMonitoringJournal({...page,episodes:[{...episode,first_observed_at:"2026-02-30T00:00:00Z"}]}),null);
});

test("rule evaluation source outage is selected without implying a stopped engine",async()=>{
 const {parseMonitoringJournal}=await import(compile(source()));
 const sourceEpisode={...episode,rule_name:"MonitoringRuleEvaluationUnavailable",first_observed_at:"2026-10-12T00:00:00+00:00",active_since:"2026-10-12T00:00:00+00:00",state:"PENDING",last_observed_at:"2026-10-12T00:00:00+00:00",runbook_path:"docs/runbooks/paper-http-monitoring.md#source-unavailable"};
 assert.deepEqual(parseMonitoringJournal({...page,episodes:[sourceEpisode]}),{...page,episodes:[sourceEpisode]});
 assert.equal(parseMonitoringJournal({...page,episodes:[{...sourceEpisode,runbook_path:"docs/runbooks/paper-http-monitoring.md#scrape-missing"}]}),null);
});
