import {cookies} from "next/headers";
import {backendOrigin} from "@/features/operator-session/boundary";
import {proxyRealtimeResponse} from "@/features/realtime/stream-proxy";

export async function GET(request: Request) {
  const failure=(status:number)=>Response.json({detail:status===401?"Sign in to receive current paper state.":"Realtime state unavailable."},{status,headers:{"Cache-Control":"no-store"}});
  if(new URL(request.url).search) return failure(422);
  const token=(await cookies()).get("ai_quant_operator")?.value;
  if(!token) return failure(401);
  try{
    const base=backendOrigin(process.env.AI_QUANT_API_URL??"http://127.0.0.1:8000");
    const signal=AbortSignal.any([request.signal,AbortSignal.timeout(25000)]);
    const upstream=await fetch(base+"/api/v1/realtime/events",{headers:{"X-Operator-Session":token},cache:"no-store",redirect:"error",signal});
    if(!upstream.ok){await upstream.body?.cancel();return failure(upstream.status===401?401:503);}
    return proxyRealtimeResponse(upstream,signal);
  }catch{return failure(503);}
}
