import { read } from "@/lib/control-api";
import { sessionStatus } from "@/features/operator-session/server";
import { parseRegistry } from "@/features/agents/registry";

export async function GET(request: Request) {
  const failure = (status = 503) => Response.json(
    { detail: status === 401 ? "Sign in to read protected agent observations." : "Agent registry observations unavailable." },
    { status, headers: { "Cache-Control": "no-store" } },
  );
  if (new URL(request.url).search) {
    return Response.json({ detail: "Agent observation query overrides are not supported." }, { status: 422, headers: { "Cache-Control": "no-store" } });
  }
  try {
    const session = await sessionStatus();
    if (!session.ok) return failure(session.status === 401 ? 401 : 503);
    const registry = await read("/api/v1/agents/registry", parseRegistry);
    return Response.json({ observed_at: new Date().toISOString(), registry }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return failure();
  }
}
