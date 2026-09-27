export default async function TracePage({ params }: { params: Promise<{ trace_id: string }> }) {
  const { trace_id } = await params;
  return <div className="page-content"><div className="page-heading"><div><p className="eyebrow">Trace explorer</p><h1>Trace {trace_id}</h1><p>Market → intent → risk → order causality will appear when tracing is connected.</p></div><span className="mode-tag">TRACE SERVICE PENDING</span></div><section className="panel content-panel"><div className="panel-heading"><h2>Causal chain</h2><span>UNAVAILABLE</span></div><div className="empty-service"><span className="empty-service-mark">—</span><strong>No trace data connected</strong></div></section></div>;
}
