export type AgentManifest = {
  schema_version: "1.0";
  agent_id: string;
  agent_version: string;
  name: string;
  squad: string;
  mission: string;
  output_type: string;
  required_inputs: string[];
  allowed_tools: string[];
  implementation_status: "PLANNED" | "PARTIAL" | "IMPLEMENTED";
  timeout_seconds: number;
  manifest_ref: string;
  lifecycle: "ACTIVE" | "LIMITED" | "SHADOW" | "QUARANTINED";
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= maximum;
}

function names(value: unknown): value is string[] {
  return Array.isArray(value) && value.length <= 32 && value.every((item) => text(item, 128)) && new Set(value).size === value.length;
}

function parseManifest(value: unknown): AgentManifest | null {
  if (!record(value) || value.schema_version !== "1.0" || !text(value.agent_id, 3) || !/^A(0[1-9]|1[0-8])$/.test(value.agent_id) || !text(value.agent_version, 32) || !/^\d+\.\d+\.\d+$/.test(value.agent_version)) return null;
  if (!text(value.name, 128) || !text(value.mission, 1000) || !text(value.output_type, 128) || !text(value.squad, 32) || !["Control", "Market", "Data", "Research", "Validation", "Portfolio", "Learning", "Reliability", "Evolution", "Safety"].includes(value.squad)) return null;
  if (!names(value.required_inputs) || value.required_inputs.length === 0 || !names(value.allowed_tools) || typeof value.timeout_seconds !== "number" || !Number.isFinite(value.timeout_seconds) || value.timeout_seconds <= 0 || value.timeout_seconds > 30 || typeof value.manifest_ref !== "string" || !/^agent-manifest:[a-f0-9]{64}$/.test(value.manifest_ref)) return null;
  const implementation = value.implementation_status;
  const lifecycle = value.lifecycle;
  if (implementation !== "PLANNED" && implementation !== "PARTIAL" && implementation !== "IMPLEMENTED") return null;
  if (lifecycle !== "ACTIVE" && lifecycle !== "LIMITED" && lifecycle !== "SHADOW" && lifecycle !== "QUARANTINED") return null;
  return { schema_version: "1.0", agent_id: value.agent_id, agent_version: value.agent_version, name: value.name, squad: value.squad, mission: value.mission, output_type: value.output_type, required_inputs: [...value.required_inputs], allowed_tools: [...value.allowed_tools], implementation_status: implementation, timeout_seconds: value.timeout_seconds, manifest_ref: value.manifest_ref, lifecycle };
}

export function parseRegistry(value: unknown): AgentManifest[] | null {
  if (!record(value) || value.schema_version !== "1.0" || value.mode !== "PAPER" || !Array.isArray(value.agents) || value.agents.length > 1024) return null;
  const agents: AgentManifest[] = [];
  const identities = new Set<string>();
  for (const item of value.agents) {
    const manifest = parseManifest(item);
    if (!manifest) return null;
    const identity = `${manifest.agent_id}@${manifest.agent_version}`;
    if (identities.has(identity)) return null;
    identities.add(identity);
    agents.push(manifest);
  }
  return agents;
}
