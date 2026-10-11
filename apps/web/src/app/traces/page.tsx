import {readAgentTraceBinding, readCollectorTrace, readGraphRuns, readTracingStatus} from "@/features/operator-session/server";
import {GraphRunListPanel} from "@/features/traces/trace-panels";
import {TracingStatusPanel} from "@/features/traces/tracing-status-panel";
import {AgentTraceLookupPanel} from "@/features/traces/agent-trace-lookup-panel";
import {CollectorTracePanel} from "@/features/traces/collector-trace-panel";

export default async function TracesPage({searchParams}:{searchParams:Promise<Record<string,string|string[]|undefined>>}) {
  const query = await searchParams;
  const valid = (query.after === undefined || typeof query.after === "string") && (query.head === undefined || typeof query.head === "string");
  const [result, diagnostics, advisory, spans] = await Promise.all([valid ? readGraphRuns(query.after as string|undefined,query.head as string|undefined) : Promise.resolve({ok:false as const,reason:"INVALID_PAGE"}), readTracingStatus(), query.run === undefined ? Promise.resolve(null) : typeof query.run === "string" ? readAgentTraceBinding(query.run) : Promise.resolve({ok:false as const,reason:"INVALID_TRACE"}), query.sdk === undefined ? Promise.resolve(null) : typeof query.sdk === "string" ? readCollectorTrace(query.sdk) : Promise.resolve({ok:false as const,reason:"INVALID_TRACE"})]);
  return <div className="page-content"><div className="page-heading"><div><p className="eyebrow">Trace explorer</p><h1>Traces</h1><p>Inspect current export observations and saved agent execution outcomes.</p></div><span className="mode-tag">PAPER OBSERVATIONS</span></div><TracingStatusPanel result={diagnostics}/><AgentTraceLookupPanel runId={typeof query.run === "string" ? query.run : ""} result={advisory}/><CollectorTracePanel traceId={typeof query.sdk === "string" ? query.sdk : ""} result={spans}/><GraphRunListPanel result={result} after={typeof query.after === "string" ? query.after : ""} head={typeof query.head === "string" ? query.head : undefined}/></div>;
}
