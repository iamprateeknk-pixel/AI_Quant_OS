import Link from "next/link";
import { parseDossiers } from "./dossiers";

export function DossierPanel({ result }: { result: {ok:true;value:unknown} | {ok:false;reason:string} }) {
  const page=result.ok ? parseDossiers(result.value) : null;
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Archived candidate dossiers</h2><span>{page ? `${page.total} RETAINED` : "UNAVAILABLE"}</span></div>
    <p className="panel-muted">These are saved research results. CHALLENGER means validation is incomplete; DEMOTED means a declared forward-shadow reality-gap threshold was exceeded. Gate PASS is historical evidence for that gate; it grants no deployment, allocation or promotion authority.</p>
    {page ? <>
      {page.candidates.map((row) => <article className="research-dossier" key={row.candidate_id}>
        <header><h3>{row.experiment_id}</h3><span>{row.state} · revision {row.revision}</span></header>
        <p>{row.hypothesis}</p><dl className="fact-list"><div><dt>Research outcome</dt><dd>{row.outcome}</dd></div><div><dt>Selected strategy</dt><dd>{row.strategy_ref ?? "No strategy selected"}</dd></div><div><dt>Walk-forward folds / recorded attempts</dt><dd>{row.fold_count} / {row.attempt_count}</dd></div><div><dt>Promotion</dt><dd>Not authorized</dd></div></dl>
        {row.details_truncated && <p className="panel-muted">This is a shortened preview. Consult the original retained dossier for full text and all records.</p>}
        {row.reasons.length > 0 && <><h4>Archived reason previews</h4><ul>{row.reasons.map((reason,index) => <li key={index}>{reason}</li>)}</ul></>}
        <details className="research-details"><summary>Inspect validation and provenance for {row.experiment_id}</summary>
          {row.gates.length ? <div className="table-scroll"><table><caption className="sr-only">Recorded validation gates</caption><thead><tr><th scope="col">Gate</th><th scope="col">Result</th><th scope="col">Reason preview</th></tr></thead><tbody>{row.gates.map((gate,index) => <tr key={`${gate.name}-${index}`}><th scope="row">{gate.name}</th><td>{gate.state}</td><td>{gate.reason}</td></tr>)}</tbody></table></div> : <p>No gate evidence recorded in this dossier.</p>}
          <dl className="fact-list research-provenance">{[['Candidate',row.candidate_id],['Hypothesis',row.hypothesis_id],['Report fingerprint',row.report_sha256],['Plan fingerprint',row.plan_sha256],['Last candidate event',row.last_event_sha256]].map(([name,value]) => <div key={name}><dt>{name}</dt><dd className="mono">{value}</dd></div>)}</dl>
        </details>
      </article>)}
      {page.candidates.length === 0 && <div className="empty-list">{page.total === 0 ? "No candidate dossiers registered." : "No dossiers on this page."}</div>}
      <div className="health-navigation"><Link className="health-link" href="/research">Refresh first page</Link>{page.offset > 0 && <Link className="health-link" href={`/research?offset=${Math.max(0,page.offset-page.limit)}&head=${page.head_hash}`}>Previous dossiers</Link>}{page.next_offset !== null && <Link className="health-link" href={`/research?offset=${page.next_offset}&head=${page.head_hash}`}>Next dossiers</Link>}</div>
      <p className="panel-muted mono health-head">Candidate journal head: {page.head_hash}</p>
    </> : <div className="empty-list" role="status"><p>{!result.ok && result.reason === "SIGN_IN" ? "Sign in to view protected research dossiers." : !result.ok && result.reason === "CHANGED" ? "The candidate journal changed. Refresh the first page." : "Verified research dossiers are unavailable. No research outcome is inferred."}</p><Link className="health-link" href={!result.ok && result.reason === "SIGN_IN" ? "/risk" : "/research"}>{!result.ok && result.reason === "SIGN_IN" ? "Sign in at Risk Center" : "Refresh first page"}</Link></div>}
  </section>;
}
