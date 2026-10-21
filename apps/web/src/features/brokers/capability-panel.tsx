import Link from "next/link";
import { TableScrollRegion } from "../shared/table-scroll-region";
import { parseBrokerCapabilities } from "./capabilities";

type Read = {ok:true;value:unknown} | {ok:false;reason:string};
const names: Record<string,string> = {PAPER:"Paper simulator",EXNESS_MT5_DEMO:"Exness demo",EXNESS:"Exness execution",IBKR:"Interactive Brokers",ZERODHA:"Zerodha"};
const implementations: Record<string,string> = {PAPER_SIMULATOR:"Paper simulator",ISOLATED_DEMO_ORDER_CLI:"Isolated demo order CLI",DISABLED_SKELETON:"Unsupported skeleton"};
const operations: Record<string,string> = {SIMULATED_ORDER_PLACE:"Place simulated order",SIMULATED_ORDER_LOOKUP:"Look up simulated order",SIMULATED_FILLS_READ:"Read simulated fills",SIMULATED_FILL_REPORT:"Report simulated fill",QUOTE_READ:"Read quotes",ACCOUNT_READ:"Read account",POSITIONS_READ:"Read positions",ACTIVE_ORDERS_READ:"Read active orders",DEALS_READ:"Read deal history",DEMO_ORDER_SUBMIT_EXPLICIT:"Submit explicitly gated demo order",DEMO_ORDER_RECONCILE_READ_ONLY:"Reconcile demo order history"};
export function BrokerCapabilityPanel({result,observedAt}:{result:Read;observedAt:string}) {
  const rows=result.ok ? parseBrokerCapabilities(result.value) : null;
  const signIn=!result.ok && result.reason==="SIGN_IN";
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Broker capabilities</h2><span>Declared interfaces</span></div>
    {rows ? <TableScrollRegion label="Broker capabilities table"><table>
      <caption className="sr-only">Declared local broker interfaces, eligibility and execution release</caption>
      <thead><tr><th scope="col">Broker</th><th scope="col">Implementation</th><th scope="col">Eligibility scope</th><th scope="col">Order admission</th><th scope="col">Supported interfaces</th></tr></thead>
      <tbody>{rows.map(row=><tr key={row.broker_id}>
        <th scope="row">{names[row.broker_id]}<div className="panel-muted">Profile {row.profile_version}</div></th>
        <td>{implementations[row.implementation]}</td>
        <td>{row.compliance_scope==="DENIED" ? "Denied" : row.compliance_scope==="UNVERIFIED" ? "Unverified" : row.eligible ? "Local simulation only" : "Not eligible"}</td>
        <td>{row.broker_id==="PAPER" ? row.paper_admission_policy_allows ? "Paper broker gate allows" : "Paper broker gate blocks" : "Native execution unreleased"}</td>
        <td><details className="health-record"><summary>{row.operations.length ? `${row.operations.length} interfaces` : "No supported native interfaces"}</summary>
          {row.operations.length > 0 && <ul>{row.operations.map(operation=><li key={operation}>{operations[operation]}</li>)}</ul>}
          <dl className="fact-list"><div><dt>Execution router binding</dt><dd>{row.bound_to_execution_router ? "Present; admission rules still apply" : "Not bound"}</dd></div>
            <div><dt>Mapped paper instruments</dt><dd>{row.broker_id === "PAPER" ? row.instruments.length ? row.instruments.join(", ") : "None mapped" : "Not applicable"}</dd></div>
            <div><dt>Native acceptance</dt><dd>{row.native_acceptance==="NOT_APPLICABLE" ? "Not applicable to the simulator" : "Unverified"}</dd></div>
          </dl>
        </details></td>
      </tr>)}</tbody>
    </table></TableScrollRegion> : <div className="empty-list" role="status"><p>{signIn ? "Sign in to view protected broker capabilities." : "Broker capabilities are unavailable. Supported operations and eligibility are not inferred."}</p></div>}
    <p className="panel-muted">These profiles describe local interfaces. Native execution remains unreleased. Demo submission and reconciliation use a separate local CLI with explicit operator switches; the CLI is not bound to the PAPER execution router. Unsupported skeletons cannot connect or submit orders. Local simulation eligibility does not establish legal compliance. Risk, safety, symbol and expiry checks still apply to paper orders.</p>
    <div className="health-navigation"><Link className="health-link" href={signIn ? "/risk" : "/brokers"}>{signIn ? "Sign in at Risk Center" : "Refresh broker capabilities"}</Link><span className="panel-muted">View fetched at {observedAt}</span></div>
  </section>;
}
