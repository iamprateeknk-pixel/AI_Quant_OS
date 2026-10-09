import Link from "next/link";
import {readGraphTrace} from "@/features/operator-session/server";
import {GraphTracePanel} from "@/features/traces/trace-panels";

export default async function TracePage({ params }: { params: Promise<{ trace_id: string }> }) {
  const { trace_id } = await params;
  const result = await readGraphTrace(trace_id);
  return <div className="page-content"><div className="page-heading"><div><p className="eyebrow">Trace explorer</p><h1>Agent execution</h1><p className="mono trace-reference">{trace_id}</p><Link href="/traces" prefetch={false}>Back to saved executions</Link></div><span className="mode-tag">ARCHIVED EVIDENCE</span></div><GraphTracePanel result={result} runId={trace_id}/></div>;
}
