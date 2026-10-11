import {parseSpendCounters,type SpendCounters} from "./review-spending";

const accounting="SHARED_MODEL_COUNTED_TOKENS_AT_DECLARED_RATE_CEILING" as const;
type Provider="JEV"|"OPENAI_RESPONSES";
type ModelAccounting="JEV_INPUT_ONLY_AT_DECLARED_RATE_CEILING"|"PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING";
export type ModelSpending={provider_id:Provider;model:string;accounting:ModelAccounting;policy_sha256:string;agents:SpendCounters[]};
export type SpendingRoute={provider_id:Provider;model:string;purpose:"CLASSIFICATION"|"REVIEW";agent_id:string;agent_version:string};
export type SharedSpending={schema_version:"1.0";mode:"PAPER";accounting:typeof accounting;invoice_verified:false;recovery_spending_enabled:false;state:"ENFORCED"|"BREACHED"|"NOT_CONFIGURED"|"NOT_SHARED"|"SEPARATE_LEDGERS"|"INCOMPLETE_CONFIGURATION"|"UNAVAILABLE";observed_at:string;book_id:string|null;day:string|null;month:string|null;policy_sha256:string|null;head_hash:string|null;agents:SpendCounters[];models:ModelSpending[];routes:SpendingRoute[]};
const record=(v:unknown):v is Record<string,unknown>=>v!==null&&typeof v==="object"&&!Array.isArray(v);
const hash=(v:unknown):v is string=>typeof v==="string"&&/^[a-f0-9]{64}$/.test(v);
const timestamp=(v:unknown):v is string=>typeof v==="string"&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v)&&Number(v.slice(0,4))>=1&&Number.isFinite(Date.parse(v))&&new Date(v).toISOString().slice(0,19)===v.slice(0,19);
function rows(v:unknown,enforced:boolean):SpendCounters[]|null{
 if(!Array.isArray(v)||v.length!==18)return null;
 const result:SpendCounters[]=[];
 for(const value of v){const row=parseSpendCounters(value,enforced);if(!row||result.some(other=>other.agent_id===row.agent_id))return null;result.push(row);}
 return result.sort((a,b)=>a.agent_id.localeCompare(b.agent_id));
}
function model(v:unknown,enforced:boolean):ModelSpending|null{
 if(!record(v)||typeof v.model!=="string"||v.model.length>128||!hash(v.policy_sha256))return null;
 const jev=v.provider_id==="JEV",openai=v.provider_id==="OPENAI_RESPONSES";
 if(jev?(v.model!=="jev-1.13.0"||v.accounting!=="JEV_INPUT_ONLY_AT_DECLARED_RATE_CEILING"):(!openai||!/^gpt-[a-z0-9.-]+-\d{4}-\d{2}-\d{2}$/.test(v.model)||v.accounting!=="PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING"))return null;
 const agents=rows(v.agents,enforced);return agents?{provider_id:v.provider_id as Provider,model:v.model,accounting:v.accounting as ModelAccounting,policy_sha256:v.policy_sha256,agents}:null;
}
function route(v:unknown,models:ModelSpending[]):SpendingRoute|null{
 if(!record(v)||typeof v.model!=="string"||typeof v.agent_id!=="string"||!/^A(0[1-9]|1[0-8])$/.test(v.agent_id)||typeof v.agent_version!=="string"||v.agent_version.length>32||!/^(0|[1-9]\d{0,5})(\.(0|[1-9]\d{0,5})){2}$/.test(v.agent_version))return null;
 if(!models.some(model=>model.model===v.model&&model.provider_id===v.provider_id)||!(v.provider_id==="JEV"&&v.purpose==="CLASSIFICATION"||v.provider_id==="OPENAI_RESPONSES"&&v.purpose==="REVIEW"))return null;
 return {provider_id:v.provider_id as Provider,model:v.model,purpose:v.purpose as SpendingRoute["purpose"],agent_id:v.agent_id,agent_version:v.agent_version};
}
export function parseSharedSpending(v:unknown):SharedSpending|null{
 const states=["ENFORCED","BREACHED","NOT_CONFIGURED","NOT_SHARED","SEPARATE_LEDGERS","INCOMPLETE_CONFIGURATION","UNAVAILABLE"];
 if(!record(v)||v.schema_version!=="1.0"||v.mode!=="PAPER"||v.accounting!==accounting||v.invoice_verified!==false||v.recovery_spending_enabled!==false||!timestamp(v.observed_at)||!states.includes(v.state as string)||!Array.isArray(v.agents)||!Array.isArray(v.models)||!Array.isArray(v.routes))return null;
 const base={schema_version:"1.0",mode:"PAPER",accounting,invoice_verified:false,recovery_spending_enabled:false,state:v.state as SharedSpending["state"],observed_at:v.observed_at} as const;
 if(v.state!=="ENFORCED"&&v.state!=="BREACHED"){
  if(v.agents.length||v.models.length||v.routes.length||[v.book_id,v.day,v.month,v.policy_sha256,v.head_hash].some(value=>value!==null))return null;
  return {...base,book_id:null,day:null,month:null,policy_sha256:null,head_hash:null,agents:[],models:[],routes:[]};
 }
 if(typeof v.book_id!=="string"||!/^[a-f0-9]{32}$/.test(v.book_id)||v.day!==v.observed_at.slice(0,10)||v.month!==v.observed_at.slice(0,7)||!hash(v.policy_sha256)||!hash(v.head_hash)||v.models.length<1||v.models.length>8||v.routes.length<1||v.routes.length>2)return null;
 const agents=rows(v.agents,v.state==="ENFORCED"),models:ModelSpending[]=[],routes:SpendingRoute[]=[];
 if(!agents)return null;
 for(const value of v.models){const item=model(value,v.state==="ENFORCED");if(!item||models.some(other=>other.model===item.model))return null;models.push(item);}
 for(const value of v.routes){const item=route(value,models);if(!item||routes.some(other=>other.purpose===item.purpose))return null;routes.push(item);}
 const sums=["daily_tokens","monthly_tokens","unsettled_tokens","pending_count","daily_nano_usd","monthly_nano_usd","unsettled_nano_usd"] as const;
 for(const row of agents){const children=models.map(model=>model.agents.find(child=>child.agent_id===row.agent_id)!);for(const key of sums)if(BigInt(row[key])!==children.reduce((sum,child)=>sum+BigInt(child[key]),BigInt(0)))return null;}
 return {...base,book_id:v.book_id,day:v.day as string,month:v.month as string,policy_sha256:v.policy_sha256,head_hash:v.head_hash,agents,models:models.sort((a,b)=>a.model.localeCompare(b.model)),routes:routes.sort((a,b)=>a.purpose.localeCompare(b.purpose))};
}
