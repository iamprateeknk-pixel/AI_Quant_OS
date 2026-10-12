import Link from "next/link";
import { parseBrokerHealth } from "./health";

type Read = {ok:true;value:unknown} | {ok:false;reason:string};
const names: Record<string,string> = {PAPER:"Paper simulator",EXNESS_MT5_DEMO:"Exness demo",EXNESS:"Exness",IBKR:"Interactive Brokers",ZERODHA:"Zerodha"};
function label(value: string): string { return value.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, letter => letter.toUpperCase()); }
function metric(value: number | null, suffix = ""): string { return value === null ? "Not measured" : `${value}${suffix}`; }

export function BrokerHealthPanel({result,observedAt}:{result:Read;observedAt:string}) {
  const rows=result.ok ? parseBrokerHealth(result.value) : null;
  const signIn=!result.ok && result.reason==="SIGN_IN";
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Broker health</h2><span>Paper observations</span></div>
    {rows ? <div className="table-scroll"><table>
      <caption className="sr-only">Paper broker health observations and measurement coverage</caption>
      <thead><tr><th scope="col">Broker</th><th scope="col">Connection</th><th scope="col">Quotes / orders</th><th scope="col">Reject rate</th><th scope="col">Reconciliation</th><th scope="col">Spread</th><th scope="col">Account</th><th scope="col">Health score</th><th scope="col">Updated</th></tr></thead>
      <tbody>{rows.map(row=><tr key={row.broker_id}>
        <th scope="row">{names[row.broker_id]}</th><td>{label(row.connection_state)}</td>
        <td>{metric(row.quote_age_ms," ms")} / {metric(row.order_latency_ms," ms")}</td>
        <td>{row.reject_rate===null ? "Not measured" : `${(row.reject_rate*100).toFixed(1)}%`}</td>
        <td>{label(row.reconciliation_state)}</td><td>{label(row.spread_state)}</td><td>{label(row.account_state)}</td>
        <td>{row.health_score===null ? "No score" : `${(row.health_score*100).toFixed(1)}%`}</td>
        <td><time dateTime={row.updated_at}>{new Date(row.updated_at).toLocaleString("en-GB",{timeZone:"UTC",hour12:false})} UTC</time></td>
      </tr>)}</tbody>
    </table></div> : <div className="empty-list" role="status"><p>{signIn ? "Sign in to view protected broker health." : "Broker health is unavailable. No status or score is inferred."}</p></div>}
    <p className="panel-muted">Health observations describe the paper environment. Missing measurements remain unmeasured, and a score requires complete evidence. Live execution remains unreleased.</p>
    <div className="health-navigation"><Link className="health-link" href={signIn ? "/risk" : "/brokers"}>{signIn ? "Sign in at Risk Center" : "Refresh broker health"}</Link><span className="panel-muted">View fetched at {observedAt}</span></div>
  </section>;
}
