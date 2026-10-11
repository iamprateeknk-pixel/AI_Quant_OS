const tokenFields=["daily_tokens","monthly_tokens","unsettled_tokens","daily_token_limit","monthly_token_limit","daily_token_remaining","monthly_token_remaining","daily_token_reserve","monthly_token_reserve"] as const;
const moneyFields=["daily_nano_usd","monthly_nano_usd","unsettled_nano_usd","daily_money_limit","monthly_money_limit","daily_money_remaining","monthly_money_remaining","daily_money_reserve","monthly_money_reserve"] as const;
export type SpendCounters={agent_id:string;pending_count:number}&Record<typeof tokenFields[number],number>&Record<typeof moneyFields[number],string>;
export type ReviewSpending={schema_version:"1.0";mode:"PAPER";provider_id:"OPENAI_RESPONSES";state:"ENFORCED"|"BREACHED"|"NOT_CONFIGURED"|"UNAVAILABLE";accounting:"PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING";billable_cost_accounting:false;recovery_spending_enabled:false;observed_at:string;day:string|null;month:string|null;model:string|null;policy_sha256:string|null;head_hash:string|null;agents:SpendCounters[]};
const record=(v:unknown):v is Record<string,unknown>=>v!==null&&typeof v==="object"&&!Array.isArray(v);
const integer=(v:unknown,max:number):v is number=>typeof v==="number"&&Number.isSafeInteger(v)&&v>=0&&v<=max;
const money=(v:unknown):v is string=>typeof v==="string"&&/^(0|[1-9][0-9]{0,23})$/.test(v);
function timestamp(v:unknown):v is string{
 return typeof v==="string"&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v)&&Number(v.slice(0,4))>=1&&Number.isFinite(Date.parse(v))&&new Date(v).toISOString().slice(0,19)===v.slice(0,19);
}
export function formatNanoUSD(value:string):string{
 if(!money(value))throw new Error("Invalid exact amount");
 const amount=BigInt(value),scale=BigInt(1000000000);return `$${amount/scale}.${(amount%scale).toString().padStart(9,"0")}`;
}
export function parseSpendCounters(v:unknown,enforced:boolean):SpendCounters|null{
 if(!record(v)||typeof v.agent_id!=="string"||!/^A(0[1-9]|1[0-8])$/.test(v.agent_id)||!integer(v.pending_count,20000))return null;
 if(tokenFields.some(key=>!integer(v[key],key.endsWith("tokens")?40000000000000:1000000000))||moneyFields.some(key=>!money(v[key])))return null;
 const row={agent_id:v.agent_id,pending_count:v.pending_count,...Object.fromEntries([...tokenFields,...moneyFields].map(key=>[key,v[key]]))} as SpendCounters;
 if(!(row.unsettled_tokens<=row.daily_tokens&&row.daily_tokens<=row.monthly_tokens)||!(BigInt(row.unsettled_nano_usd)<=BigInt(row.daily_nano_usd)&&BigInt(row.daily_nano_usd)<=BigInt(row.monthly_nano_usd))||(row.pending_count===0)!==(row.unsettled_tokens===0)||(row.pending_count===0)!==(BigInt(row.unsettled_nano_usd)===BigInt(0))||row.unsettled_tokens<128*row.pending_count)return null;
 for(const [daily,monthly,dr,mr,du,mu,dleft,mleft,bound] of [
  [row.daily_token_limit,row.monthly_token_limit,row.daily_token_reserve,row.monthly_token_reserve,row.daily_tokens,row.monthly_tokens,row.daily_token_remaining,row.monthly_token_remaining,1000000000].map(BigInt),
  [row.daily_money_limit,row.monthly_money_limit,row.daily_money_reserve,row.monthly_money_reserve,row.daily_nano_usd,row.monthly_nano_usd,row.daily_money_remaining,row.monthly_money_remaining,"1000000000000000"].map(BigInt),
 ]){
  const dAvailable=daily-dr-du,mAvailable=monthly-mr-mu;
  const zero=BigInt(0);
  if(daily>monthly||monthly>bound||dr>daily||mr>monthly||dr>mr||dleft!==(dAvailable>zero?dAvailable:zero)||mleft!==(mAvailable>zero?mAvailable:zero)||(enforced&&(dAvailable<zero||mAvailable<zero)))return null;
 }
 return row;
}
export function parseReviewSpending(v:unknown):ReviewSpending|null{
 if(!record(v)||v.schema_version!=="1.0"||v.mode!=="PAPER"||v.provider_id!=="OPENAI_RESPONSES"||v.accounting!=="PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING"||v.billable_cost_accounting!==false||v.recovery_spending_enabled!==false||!timestamp(v.observed_at)||!Array.isArray(v.agents)||!["ENFORCED","BREACHED","NOT_CONFIGURED","UNAVAILABLE"].includes(v.state as string))return null;
 const base={schema_version:"1.0",mode:"PAPER",provider_id:"OPENAI_RESPONSES",accounting:"PROVIDER_REPORTED_USAGE_AT_DECLARED_RATE_CEILING",billable_cost_accounting:false,recovery_spending_enabled:false,observed_at:v.observed_at,state:v.state as ReviewSpending["state"]} as const;
 if(v.state==="NOT_CONFIGURED"||v.state==="UNAVAILABLE"){
  if(v.agents.length||[v.day,v.month,v.model,v.policy_sha256,v.head_hash].some(item=>item!==null))return null;
  return {...base,day:null,month:null,model:null,policy_sha256:null,head_hash:null,agents:[]};
 }
 if(typeof v.day!=="string"||v.day!==v.observed_at.slice(0,10)||v.month!==v.day.slice(0,7)||typeof v.model!=="string"||v.model.length>128||!/^gpt-[a-z0-9.-]+-\d{4}-\d{2}-\d{2}$/.test(v.model)||typeof v.policy_sha256!=="string"||!/^[a-f0-9]{64}$/.test(v.policy_sha256)||typeof v.head_hash!=="string"||!/^[a-f0-9]{64}$/.test(v.head_hash)||v.agents.length!==18)return null;
 const agents:SpendCounters[]=[];
 for(const input of v.agents){const row=parseSpendCounters(input,v.state==="ENFORCED");if(!row||agents.some(other=>other.agent_id===row.agent_id))return null;agents.push(row);}
 return {...base,day:v.day,month:v.month as string,model:v.model,policy_sha256:v.policy_sha256,head_hash:v.head_hash,agents:agents.sort((a,b)=>a.agent_id.localeCompare(b.agent_id))};
}
