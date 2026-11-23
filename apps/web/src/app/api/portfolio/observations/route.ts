import {getCurrencyPortfolio,getPositions} from "@/lib/control-api";
import {sessionStatus} from "@/features/operator-session/server";

export async function GET(request:Request){
 const failure=(status=503)=>Response.json({detail:status===401?"Sign in to read protected portfolio observations.":"Paper portfolio observations unavailable."},{status,headers:{"Cache-Control":"no-store"}});
 if(new URL(request.url).search)return Response.json({detail:"Portfolio observation query overrides are not supported."},{status:422,headers:{"Cache-Control":"no-store"}});
 try{
  const session=await sessionStatus();
  if(!session.ok)return failure(session.status===401?401:503);
  const[positions,currencyPortfolio]=await Promise.all([getPositions(),getCurrencyPortfolio()]);
  return Response.json({observed_at:new Date().toISOString(),positions,currencyPortfolio},{headers:{"Cache-Control":"no-store"}});
 }catch{return failure();}
}
