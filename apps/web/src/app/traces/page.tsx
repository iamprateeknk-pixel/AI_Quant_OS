import {readGraphRuns, readTracingStatus} from "@/features/operator-session/server";
import {GraphRunListPanel} from "@/features/traces/trace-panels";
import {TracingStatusPanel} from "@/features/traces/tracing-status-panel";

export default async function TracesPage({searchParams}:{searchParams:Promise<Record<string,string|string[]|undefined>>}) {
  const query = await searchParams;
  const valid = (query.after === undefined || typeof query.after === "string") && (query.head === undefined || typeof query.head === "string");
  const [result, diagnostics] = await Promise.all([valid ? readGraphRuns(query.after as string|undefined,query.head as string|undefined) : Promise.resolve({ok:false as const,reason:"INVALID_PAGE"}), readTracingStatus()]);
  return <div className="page-content"><div className="page-heading"><div><p className="eyebrow">Trace explorer</p><h1>Traces</h1><p>Inspect current export observations and saved agent execution outcomes.</p></div><span className="mode-tag">PAPER OBSERVATIONS</span></div><TracingStatusPanel result={diagnostics}/><GraphRunListPanel result={result} after={typeof query.after === "string" ? query.after : ""} head={typeof query.head === "string" ? query.head : undefined}/></div>;
}
