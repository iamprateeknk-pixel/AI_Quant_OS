import {monitoringMicros} from "./monitoring-alerts";

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const uuid4=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const ruleNames=["PaperHttpScrapeFailed","PaperHttpScrapeMissing"] as const;
const runbooks=["docs/runbooks/paper-http-monitoring.md#scrape-failed","docs/runbooks/paper-http-monitoring.md#scrape-missing"] as const;
const states=["PENDING","FIRING","NOT_CURRENTLY_OBSERVED","OBSERVED_INACTIVE","OBSERVED_REPLACED"] as const;
type Receipt={schema_version:"1.0";mode:"PAPER";committed:true;control_authorized:false;action_id:string;episode_id:string;actor_session_id:string;acknowledged_at:string;source_head:string};
export type MonitoringEpisode={episode_id:string;rule_name:typeof ruleNames[number];first_observed_at:string;active_since:string;state:typeof states[number];last_observed_at:string;runbook_path:typeof runbooks[number];acknowledgement:Receipt|null};
export type MonitoringJournalPage={schema_version:"1.0";mode:"PAPER";source_kind:"LOCAL_PROMETHEUS_RULE_EVALUATION";durable:true;notification_verified:false;independently_attested:false;head_hash:string;total:number;offset:number;limit:50;next_offset:number|null;episodes:MonitoringEpisode[]};
export type MonitoringAcknowledgementInput={episode_id:string;action_id:string;expected_head:string};
type ObjectValue=Record<string,unknown>;
const object=(value:unknown):ObjectValue|null=>value&&typeof value==="object"&&!Array.isArray(value)?value as ObjectValue:null;
const keys=(value:ObjectValue,expected:string[])=>Object.keys(value).sort().join(",")===expected.slice().sort().join(",");
const stamp=(value:unknown):value is string=>monitoringMicros(value)!==null;
const head=(value:unknown):value is string=>typeof value==="string"&&/^[a-f0-9]{64}$/.test(value);

export function parseMonitoringAcknowledgementInput(value:unknown):MonitoringAcknowledgementInput|null {
 const v=object(value);
 if(!v||!keys(v,["episode_id","action_id","expected_head"])||typeof v.episode_id!=="string"||!uuid.test(v.episode_id)||typeof v.action_id!=="string"||!uuid4.test(v.action_id)||!head(v.expected_head))return null;
 return {episode_id:v.episode_id,action_id:v.action_id,expected_head:v.expected_head};
}

function receipt(value:unknown,episodeId:string):Receipt|null {
 const v=object(value);
 if(!v||!keys(v,["schema_version","mode","committed","control_authorized","action_id","episode_id","actor_session_id","acknowledged_at","source_head"])||v.schema_version!=="1.0"||v.mode!=="PAPER"||v.committed!==true||v.control_authorized!==false||typeof v.action_id!=="string"||!uuid4.test(v.action_id)||v.episode_id!==episodeId||typeof v.actor_session_id!=="string"||!uuid4.test(v.actor_session_id)||!stamp(v.acknowledged_at)||!head(v.source_head))return null;
 return {schema_version:"1.0",mode:"PAPER",committed:true,control_authorized:false,action_id:v.action_id,episode_id:episodeId,actor_session_id:v.actor_session_id,acknowledged_at:v.acknowledged_at,source_head:v.source_head};
}

export function parseMonitoringAcknowledgement(value:unknown):Receipt|null {
 const v=object(value);
 if(!v||typeof v.episode_id!=="string"||!uuid.test(v.episode_id))return null;
 return receipt(value,v.episode_id);
}

export function parseMonitoringJournal(value:unknown):MonitoringJournalPage|null {
 const v=object(value);
 if(!v||!keys(v,["schema_version","mode","source_kind","durable","notification_verified","independently_attested","head_hash","total","offset","limit","next_offset","episodes"])||v.schema_version!=="1.0"||v.mode!=="PAPER"||v.source_kind!=="LOCAL_PROMETHEUS_RULE_EVALUATION"||v.durable!==true||v.notification_verified!==false||v.independently_attested!==false||!head(v.head_hash)||!Number.isSafeInteger(v.total)||Number(v.total)<0||Number(v.total)>8192||!Number.isSafeInteger(v.offset)||Number(v.offset)<0||Number(v.offset)>8192||v.limit!==50||!Array.isArray(v.episodes))return null;
 const expected=Math.min(50,Math.max(0,Number(v.total)-Number(v.offset)));
 const next=Number(v.offset)+50<Number(v.total)?Number(v.offset)+50:null;
 if(v.next_offset!==next||v.episodes.length!==expected)return null;
 const episodes:MonitoringEpisode[]=[],seen=new Set<string>();
 for(const item of v.episodes){
  const r=object(item);
  if(!r||!keys(r,["episode_id","rule_name","first_observed_at","active_since","state","last_observed_at","runbook_path","acknowledgement"])||typeof r.episode_id!=="string"||!uuid.test(r.episode_id)||seen.has(r.episode_id)||!ruleNames.includes(r.rule_name as typeof ruleNames[number])||!states.includes(r.state as typeof states[number])||!stamp(r.first_observed_at)||!stamp(r.active_since)||!stamp(r.last_observed_at)||monitoringMicros(r.active_since)!>monitoringMicros(r.first_observed_at)!||monitoringMicros(r.first_observed_at)!>monitoringMicros(r.last_observed_at)!||monitoringMicros(r.active_since)!>monitoringMicros(r.last_observed_at)!||(r.state==="FIRING"&&monitoringMicros(r.last_observed_at)!-monitoringMicros(r.active_since)!<BigInt(120000000))){return null;}
  const index=ruleNames.indexOf(r.rule_name as typeof ruleNames[number]);
  if(r.runbook_path!==runbooks[index])return null;
  const acknowledgement=r.acknowledgement===null?null:receipt(r.acknowledgement,r.episode_id);
  if(r.acknowledgement!==null&&!acknowledgement)return null;
  seen.add(r.episode_id);
  episodes.push({episode_id:r.episode_id,rule_name:r.rule_name as MonitoringEpisode["rule_name"],first_observed_at:r.first_observed_at,active_since:r.active_since,state:r.state as MonitoringEpisode["state"],last_observed_at:r.last_observed_at,runbook_path:r.runbook_path as MonitoringEpisode["runbook_path"],acknowledgement});
 }
 return {schema_version:"1.0",mode:"PAPER",source_kind:"LOCAL_PROMETHEUS_RULE_EVALUATION",durable:true,notification_verified:false,independently_attested:false,head_hash:v.head_hash,total:Number(v.total),offset:Number(v.offset),limit:50,next_offset:next,episodes};
}
