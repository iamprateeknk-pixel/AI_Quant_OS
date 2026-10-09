import {readGraphRuns} from "@/features/operator-session/server";
import {GraphRunListPanel} from "@/features/traces/trace-panels";

export default async function TracesPage({searchParams}:{searchParams:Promise<Record<string,string|string[]|undefined>>}) {
  const query = await searchParams;
  const valid = (query.after === undefined || typeof query.after === "string") && (query.head === undefined || typeof query.head === "string");
  const result = valid ? await readGraphRuns(query.after as string|undefined,query.head as string|undefined) : {ok:false as const,reason:"INVALID_PAGE"};
  return <div className="page-content"><div className="page-heading"><div><p className="eyebrow">Trace explorer</p><h1>Traces</h1><p>Inspect saved agent execution outcomes, transition times and original evidence references.</p></div><span className="mode-tag">ARCHIVED EVIDENCE</span></div><GraphRunListPanel result={result} after={typeof query.after === "string" ? query.after : ""} head={typeof query.head === "string" ? query.head : undefined}/></div>;
}
