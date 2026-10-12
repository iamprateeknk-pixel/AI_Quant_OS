import {readMonitoringAlerts} from "@/features/operator-session/server";
import {parseMonitoringAlerts} from "@/features/incidents/monitoring-alerts";

export async function GET(request:Request) {
  const failure=(status:number)=>Response.json({detail:status===401?"Sign in to read monitoring observations.":"Monitoring observations unavailable."},{status,headers:{"Cache-Control":"no-store"}});
  if(new URL(request.url).search)return failure(422);
  try{
    const result=await readMonitoringAlerts();
    if(!result.ok)return failure(result.reason==="SIGN_IN"?401:503);
    const value=parseMonitoringAlerts(result.value);
    return value?Response.json(value,{headers:{"Cache-Control":"no-store"}}):failure(503);
  }catch{return failure(503);}
}
