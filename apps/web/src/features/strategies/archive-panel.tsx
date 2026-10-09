import Link from "next/link";
import {parseStrategies} from "./archives";

export function StrategyArchivePanel({result}:{result:{ok:true;value:unknown}|{ok:false;reason:string}}) {
  const page=result.ok?parseStrategies(result.value):null;
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Archived strategy implementations</h2><span>{page?`${page.total} RETAINED`:"UNAVAILABLE"}</span></div>
    <p className="panel-muted">Saved implementations are not approved or active strategies. Trading eligibility, performance, allocation history and pause/resume workflows are not established by these archive records.</p>
    {page?<>
      {page.strategies.map(row=><article className="research-dossier" key={row.strategy_ref}>
        <header><h3>{row.strategy_ref}</h3><span>ARCHIVED IMPLEMENTATION</span></header>
        <dl className="fact-list"><div><dt>Input observations</dt><dd>{row.input_kind === "BAR" ? "Price bars" : "Market ticks"}</dd></div><div><dt>Promotion</dt><dd>Not authorized</dd></div></dl>
        <details className="research-details"><summary>Inspect saved defaults and fingerprints for {row.strategy_ref}</summary>
          <h4>Saved parameter defaults</h4>{row.parameters.length?<dl className="fact-list">{row.parameters.map(parameter=><div key={parameter.name}><dt>{parameter.name}</dt><dd>{parameter.value}</dd></div>)}</dl>:<p>No parameter defaults recorded.</p>}
          <dl className="fact-list research-provenance"><div><dt>Implementation fingerprint</dt><dd className="mono">{row.implementation_sha256}</dd></div></dl>
          {row.details_truncated && <p className="panel-muted">Some source names are shortened in this preview. Consult the original retained manifest for full names.</p>}
          <h4>Saved source fingerprints</h4><dl className="fact-list research-provenance">{row.source_inventory.map((source,index)=><div key={index}><dt>{source.path}</dt><dd className="mono">{source.sha256}</dd></div>)}</dl>
        </details>
      </article>)}
      {page.strategies.length===0 && <div className="empty-list">{page.total===0?"No strategy implementations archived.":"No implementations on this page."}</div>}
      <div className="health-navigation"><Link className="health-link" href="/strategies">Refresh first page</Link><Link className="health-link" href="/research">View research dossiers</Link>{page.offset>0 && <Link className="health-link" href={`/strategies?offset=${Math.max(0,page.offset-page.limit)}&head=${page.head_hash}`}>Previous implementations</Link>}{page.next_offset!==null && <Link className="health-link" href={`/strategies?offset=${page.next_offset}&head=${page.head_hash}`}>Next implementations</Link>}</div>
      <p className="panel-muted mono health-head">Archive catalog fingerprint: {page.head_hash}</p>
    </>:<div className="empty-list" role="status"><p>{!result.ok && result.reason==="SIGN_IN"?"Sign in to view protected strategy archives.":!result.ok && result.reason==="CHANGED"?"The strategy catalog changed. Refresh the first page.":"Verified strategy archives are unavailable. No eligibility or performance is inferred."}</p><Link className="health-link" href={!result.ok && result.reason==="SIGN_IN"?"/risk":"/strategies"}>{!result.ok && result.reason==="SIGN_IN"?"Sign in at Risk Center":"Refresh first page"}</Link></div>}
  </section>;
}
