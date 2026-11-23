import { protectedRead } from "../operator-session/server";
import { parseAgentOperationalQuality, type AgentOperationalQualityRead } from "./operational-quality";

export async function readAgentOperationalQuality(): Promise<AgentOperationalQualityRead> {
  const response = await protectedRead("/api/v1/agents/operational-quality", false, 65536);
  if (!response.ok) return response;
  const value = parseAgentOperationalQuality(response.value);
  return value ? { ok: true, value } : { ok: false, reason: "INVALID_RESPONSE" };
}
