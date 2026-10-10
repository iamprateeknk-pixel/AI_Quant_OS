import Link from "next/link";
import { parseCallBudget } from "./call-budgets";

export function CallBudgetPanel({ result }: { result: { ok: true; value: unknown } | { ok: false; reason: string } }) {
  const budget = result.ok ? parseCallBudget(result.value) : null;
  const enforced = budget?.state === "ENFORCED";
  const signIn = !result.ok && result.reason === "SIGN_IN";
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Agent call capacity</h2><span>{budget?.state ?? "UNAVAILABLE"}</span></div>
    <p className="panel-muted">Counts are reserved routine graph tool and context fetch attempts, shared across each role&apos;s versions. Failed or interrupted attempts remain counted. This call ledger does not measure tokens or charges; configured review spending is shown separately.</p>
    {enforced ? <>
      <dl className="fact-list"><div><dt>Daily period (UTC)</dt><dd>{budget.day}</dd></div><div><dt>Monthly period (UTC)</dt><dd>{budget.month}</dd></div><div><dt>Recovery spending</dt><dd>Disabled; reserved capacity is protected</dd></div></dl>
      <div className="table-scroll"><table>
        <caption className="sr-only">Reserved attempts, remaining routine calls and protected recovery capacity by agent role</caption>
        <thead><tr><th scope="col">Role</th><th scope="col">Daily attempts</th><th scope="col">Daily remaining</th><th scope="col">Daily recovery reserve</th><th scope="col">Monthly attempts</th><th scope="col">Monthly remaining</th><th scope="col">Monthly recovery reserve</th></tr></thead>
        <tbody>{budget.agents.map(row => <tr key={row.agent_id}><th scope="row">{row.agent_id}</th><td>{row.daily_calls}</td><td>{row.daily_remaining}{row.daily_remaining === 0 && " · Exhausted"}</td><td>{row.daily_recovery_reserve}</td><td>{row.monthly_calls}</td><td>{row.monthly_remaining}{row.monthly_remaining === 0 && " · Exhausted"}</td><td>{row.monthly_recovery_reserve}</td></tr>)}</tbody>
      </table></div>
      <details className="research-details"><summary>Inspect policy and journal fingerprints</summary><dl className="fact-list research-provenance"><div><dt>Policy fingerprint</dt><dd className="mono">{budget.policy_sha256}</dd></div><div><dt>Journal head</dt><dd className="mono">{budget.head_hash}</dd></div></dl></details>
    </> : <div className="empty-list" role="status"><p>{signIn ? "Sign in to view protected agent call capacity." : budget?.state === "DISABLED" ? "Agent call limits are not configured. Usage and remaining capacity are not measured." : "Verified call capacity is unavailable. No usage or remaining capacity is inferred."}</p></div>}
    <div className="health-navigation">{signIn ? <Link className="health-link" href="/risk">Sign in at Risk Center</Link> : <form method="get" action="/costs"><button type="submit" className="health-link">Refresh call capacity</button></form>}<Link className="health-link" href="/agents">View agent versions</Link></div>
  </section>;
}
