import {parseMonitoringAlerts} from "./monitoring-alerts";

/** One same-origin private read; credentials remain HTTP-only cookies. */
export async function readLiveMonitoring(signal:AbortSignal) {
  const response=await fetch("/api/incidents/monitoring",{cache:"no-store",redirect:"error",credentials:"same-origin",signal});
  if(!response.ok){await response.body?.cancel();return {ok:false as const,reason:response.status===401?"SIGN_IN":"UNAVAILABLE"};}
  const reader=response.body?.getReader();if(!reader)throw new Error("Monitoring read unavailable");
  const chunks:Uint8Array[]=[];let length=0;
  try{for(;;){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>8192)throw new Error("Monitoring read exceeded capacity");chunks.push(value);}}
  finally{await reader.cancel();}
  const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  const snapshot=parseMonitoringAlerts(JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes)));
  return snapshot?{ok:true as const,value:snapshot}:{ok:false as const,reason:"UNAVAILABLE"};
}
