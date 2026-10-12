import {readMonitoringIncidents} from "@/features/operator-session/server";

const failure=(status:number)=>Response.json(
 {detail:status===401?"Sign in to read incident history.":status===409?"History changed. Refresh the first page.":"Incident history unavailable."},
 {status,headers:{"Cache-Control":"no-store"}},
);

export async function GET(request:Request) {
 const url=new URL(request.url),names=[...url.searchParams.keys()];
 if(names.some(name=>!['offset','head'].includes(name))||names.some(name=>url.searchParams.getAll(name).length!==1))return failure(422);
 const rawOffset=url.searchParams.get("offset")??"0";
 if(!/^\d{1,5}$/.test(rawOffset))return failure(422);
 const offset=Number(rawOffset),head=url.searchParams.get("head")??undefined;
 if((offset>0&&!head)||(head!==undefined&&!/^[a-f0-9]{64}$/.test(head)))return failure(422);
 try {
  const result=await readMonitoringIncidents(offset,head);
  if(!result.ok)return failure(result.reason==="SIGN_IN"?401:result.reason==="CHANGED"?409:result.reason==="INVALID_PAGE"?422:503);
  return Response.json(result.value,{headers:{"Cache-Control":"no-store"}});
 }catch{return failure(503);}
}
