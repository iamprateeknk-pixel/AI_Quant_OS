import { getFills, getOrders } from "@/lib/control-api";
import { sessionStatus } from "@/features/operator-session/server";

export async function GET(request: Request) {
  const failure = (status = 503) => Response.json(
    { detail: status === 401 ? "Sign in to read protected paper order observations." : "Paper order observations unavailable." },
    { status, headers: { "Cache-Control": "no-store" } },
  );
  if (new URL(request.url).search) {
    return Response.json({ detail: "Order observation query overrides are not supported." }, { status: 422, headers: { "Cache-Control": "no-store" } });
  }
  try {
    const session = await sessionStatus();
    if (!session.ok) return failure(session.status === 401 ? 401 : 503);
    const [orders, fills] = await Promise.all([getOrders(), getFills()]);
    return Response.json({ observed_at: new Date().toISOString(), orders, fills }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return failure();
  }
}
