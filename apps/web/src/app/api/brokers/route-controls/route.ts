import { brokerRouteControlReceipt, submitBrokerRouteControl } from "@/features/operator-session/server";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  return submitBrokerRouteControl(request);
}

export async function GET(request: Request) {
  return brokerRouteControlReceipt(new URL(request.url).searchParams.get("action_id") ?? "");
}
