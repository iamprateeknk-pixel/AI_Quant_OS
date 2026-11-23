type Read={ok:true;value:unknown}|{ok:false;reason:string};
export type CostsSnapshot={observed_at:string;callBudget:Read;reviewSpending:Read;sharedSpending:Read};
const MAX_COSTS_BYTES=530000;
function isRead(value:unknown):value is Read {
 if(!value||typeof value!=="object"||Array.isArray(value))return false;
 const row=value as Record<string,unknown>;
 return row.ok===true&&Object.keys(row).length===2&&"value"in row || row.ok===false&&Object.keys(row).length===2&&typeof row.reason==="string"&&["SIGN_IN","CHANGED","UNAVAILABLE"].includes(row.reason);
}
export function parseCostsSnapshot(value:unknown):CostsSnapshot|null {
 if(!value||typeof value!=="object"||Array.isArray(value))return null;
 const row=value as Record<string,unknown>;
 if(Object.keys(row).length!==4||typeof row.observed_at!=="string"||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(row.observed_at)||!Number.isFinite(Date.parse(row.observed_at))||!isRead(row.callBudget)||!isRead(row.reviewSpending)||!isRead(row.sharedSpending))return null;
 return{observed_at:row.observed_at,callBudget:row.callBudget,reviewSpending:row.reviewSpending,sharedSpending:row.sharedSpending};
}
/** Same-origin protected read with a strict response bound; cookies never enter JavaScript. */
export async function readLiveCosts(signal:AbortSignal):Promise<CostsSnapshot|null>{
 const response=await fetch("/api/costs/observations",{cache:"no-store",redirect:"error",credentials:"same-origin",signal});
 if(!response.ok){await response.body?.cancel();return null;}
 const reader=response.body?.getReader();if(!reader)return null;
 const chunks:Uint8Array[]=[];let length=0;
 try{for(;;){const{done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>MAX_COSTS_BYTES)throw new Error("Costs read exceeded capacity");chunks.push(value);}}
 finally{await reader.cancel();}
 const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
 return parseCostsSnapshot(JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes)));
}
