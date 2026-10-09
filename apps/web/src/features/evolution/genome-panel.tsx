import Link from "next/link";
import { parseGenomes } from "./genomes";

export function GenomePanel({result}:{result:{ok:true;value:unknown}|{ok:false;reason:string}}) {
  const page=result.ok ? parseGenomes(result.value) : null;
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Archived agent genomes</h2><span>{page ? `${page.total} RETAINED` : "UNAVAILABLE"}</span></div>
    <p className="panel-muted">Baselines are saved implementations. Challengers have not been executed or accepted. Objectives and protocol fingerprints describe evaluation plans; they are not results. Champion selection, optimization, promotion and rollback evidence remain pending.</p>
    {page ? <>
      {page.genomes.map(row => <article className="research-dossier" key={row.genome_ref}>
        <header><h3>{row.agent_id} · {row.agent_version}</h3><span>{row.state}</span></header>
        <dl className="fact-list"><div><dt>Objective</dt><dd>{row.objective.direction} {row.objective.metric}</dd></div><div><dt>Required improvement</dt><dd>{row.objective.minimum_improvement ?? "Consult the original retained objective for its exact value"}</dd></div><div><dt>Execution / promotion</dt><dd>Unverified / Not authorized</dd></div></dl>
        <details className="research-details"><summary>Inspect lineage and fingerprints for {row.agent_id} {row.agent_version}</summary>
          <dl className="fact-list research-provenance">{[["Genome",row.genome_ref],["Parent genome",row.parent_ref ?? "Root baseline"],["Manifest",row.manifest_ref],["Evaluation protocol fingerprint",row.objective.protocol_sha256]].map(([name,value]) => <div key={name}><dt>{name}</dt><dd className="mono">{value}</dd></div>)}</dl>
          <h4>Saved source fingerprints</h4><dl className="fact-list research-provenance">{row.source_inventory.map(source => <div key={source.path}><dt>{source.path}</dt><dd className="mono">{source.sha256}</dd></div>)}</dl>
        </details>
      </article>)}
      {page.genomes.length === 0 && <div className="empty-list">{page.total === 0 ? "No genomes archived." : "No genomes on this page."}</div>}
      <div className="health-navigation"><Link className="health-link" href="/evolution">Refresh first page</Link>{page.offset > 0 && <Link className="health-link" href={`/evolution?offset=${Math.max(0,page.offset-page.limit)}&head=${page.head_hash}`}>Previous genomes</Link>}{page.next_offset !== null && <Link className="health-link" href={`/evolution?offset=${page.next_offset}&head=${page.head_hash}`}>Next genomes</Link>}</div>
      <p className="panel-muted mono health-head">Genome journal head: {page.head_hash}</p>
    </> : <div className="empty-list" role="status"><p>{!result.ok && result.reason === "SIGN_IN" ? "Sign in to view protected genome archives." : !result.ok && result.reason === "CHANGED" ? "The genome journal changed. Refresh the first page." : "Verified genome archives are unavailable. No evaluation or promotion outcome is inferred."}</p><Link className="health-link" href={!result.ok && result.reason === "SIGN_IN" ? "/risk" : "/evolution"}>{!result.ok && result.reason === "SIGN_IN" ? "Sign in at Risk Center" : "Refresh first page"}</Link></div>}
  </section>;
}
