import {readCallBudget,readReviewSpending,readSharedSpending} from "@/features/operator-session/server";

export async function GET(request:Request){
 const failure=(status:number)=>Response.json({detail:status===401?"Sign in to read protected cost observations.":"Cost observations unavailable."},{status,headers:{"Cache-Control":"no-store"}});
 if(new URL(request.url).search)return failure(422);
 try{
  const[callBudget,reviewSpending,sharedSpending]=await Promise.all([readCallBudget(),readReviewSpending(),readSharedSpending()]);
  return Response.json({observed_at:new Date().toISOString(),callBudget,reviewSpending,sharedSpending},{status:200,headers:{"Cache-Control":"no-store"}});
 }catch{return failure(503);}
}
