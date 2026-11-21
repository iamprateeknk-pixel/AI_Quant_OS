/* eslint-disable @next/next/no-html-link-for-pages -- Reload private snapshots through a new document request, avoiding the client route cache. */
import { parseDemoPortfolio } from "./demo-portfolio";
import { TableScrollRegion } from "../shared/table-scroll-region";

type Read = {ok:true;value:unknown}|{ok:false;reason:string};
export function DemoPortfolioPanel({result,observedAt}:{result:Read;observedAt:string}) {
  const view = result.ok ? parseDemoPortfolio(result.value,observedAt) : null;
  const saved = view?.record;
  const issue = !result.ok && result.reason === "SIGN_IN" ? "Sign in to view the private demo account snapshot." : view && !view.configured ? "Demo account observations are not configured." : view?.reason === "EMPTY" ? "No demo account snapshot has been saved." : view?.reason === "CLOCK" ? "The snapshot was withheld because the source clock requires review." : "Demo account snapshot is unavailable. No balance or position is inferred.";
  return <section className="panel content-panel" aria-labelledby="demo-portfolio-heading">
    <div className="panel-heading"><h2 id="demo-portfolio-heading">Demo account snapshot</h2><span>{view?.state === "STALE" ? "Snapshot expired" : saved ? "Saved observation" : "Unavailable"}</span></div>
    <p className="panel-muted">Exness MT5 demo · Read only. Native account freshness remains unverified. This snapshot cannot change the paper portfolio or authorize orders.</p>
    {saved ? <>
      {view?.state === "STALE" && <p className="empty-list" role="status">This saved source window has expired. Current account state is unknown. Amounts below are retained observations.</p>}
      <dl className="fact-list">
        <div><dt>Recorded balance</dt><dd>{saved.account.balance} {saved.account.currency}</dd></div>
        <div><dt>Recorded credit</dt><dd>{saved.account.credit} {saved.account.currency}</dd></div>
        <div><dt>Recorded equity</dt><dd>{saved.account.equity} {saved.account.currency}</dd></div>
        <div><dt>Recorded profit</dt><dd>{saved.account.profit} {saved.account.currency}</dd></div>
        <div><dt>Recorded margin</dt><dd>{saved.account.margin} {saved.account.currency}</dd></div>
        <div><dt>Recorded free margin</dt><dd>{saved.account.margin_free} {saved.account.currency}</dd></div>
        <div><dt>Account mode</dt><dd>{saved.account.margin_mode.toLowerCase()} · Leverage 1:{saved.account.leverage}</dd></div>
        <div><dt>Observed, UTC</dt><dd><time dateTime={saved.observed_at}>{saved.observed_at}</time></dd></div>
        <div><dt>Source window expires, UTC</dt><dd><time dateTime={saved.valid_until}>{saved.valid_until}</time></dd></div>
      </dl>
      <h3>Saved demo positions</h3>
      {saved.positions.length ? <TableScrollRegion label="Saved demo positions table"><table><caption className="panel-muted">Recorded positions in lots; amounts in {saved.account.currency}</caption><thead><tr><th>Instrument / venue</th><th>Ticket</th><th>Side</th><th>Lots</th><th>Opening price</th><th>Recorded price</th><th>Profit</th><th>Swap</th></tr></thead><tbody>{saved.positions.map(p=><tr key={p.ticket}><td>{p.instrument_id} / {p.venue_symbol}</td><td className="mono">{p.ticket}</td><td>{p.side}</td><td>{p.volume_lots}</td><td>{p.price_open}</td><td>{p.price_current}</td><td>{p.profit} {p.currency}</td><td>{p.swap} {p.currency}</td></tr>)}</tbody></table></TableScrollRegion> : <p className="empty-list">No open positions in this saved observation.</p>}
      <h3>Pending demo orders</h3>
      {saved.orders.length ? <TableScrollRegion label="Pending demo orders table"><table><caption className="panel-muted">Orders observed at the saved source time</caption><thead><tr><th>Instrument / venue</th><th>Ticket</th><th>Kind</th><th>Recorded state</th><th>Initial lots</th><th>Remaining lots</th><th>Order price</th><th>Stop limit price</th></tr></thead><tbody>{saved.orders.map(o=><tr key={o.ticket}><td>{o.instrument_id} / {o.venue_symbol}</td><td className="mono">{o.ticket}</td><td>{o.kind.replaceAll("_"," ")}</td><td>{o.state.replaceAll("_"," ")}</td><td>{o.initial_lots}</td><td>{o.remaining_lots}</td><td>{o.price_open}</td><td>{o.stop_limit_price}</td></tr>)}</tbody></table></TableScrollRegion> : <p className="empty-list">No pending orders in this saved observation.</p>}
      <details className="health-record"><summary>Inspect observation references</summary><dl className="fact-list">
        <div><dt>Published, UTC</dt><dd>{view?.published_at}</dd></div>
        <div><dt>Retained history start, UTC</dt><dd>{saved.history_start}</dd></div>
        <div><dt>Retained history through, UTC</dt><dd>{saved.history_through}</dd></div>
        <div><dt>Service run</dt><dd className="mono">{view?.run_id}</dd></div>
        <div><dt>Source attempt</dt><dd className="mono">{saved.source_attempt_id}</dd></div>
        <div><dt>Source receipt</dt><dd className="mono">{saved.source_receipt_ref}</dd></div>
        <div><dt>Account scope reference</dt><dd className="mono">{saved.account_scope_ref}</dd></div>
      </dl></details>
      <p className="panel-muted">Account, positions and orders were read sequentially. Complete history and terminal ownership are unverified. Quantities use current symbol metadata; historical units and currency conversion are not inferred.</p>
    </> : <div className="empty-list" role="status"><p>{issue}</p>{!result.ok && result.reason === "SIGN_IN" && <a className="health-link" href="/risk">Sign in at Risk Center</a>}</div>}
    <div className="health-navigation"><a className="health-link" href="/portfolio">Reload portfolio observations</a><span className="panel-muted">Expiry checked when this view was fetched at {observedAt}. Reload for another check.</span></div>
  </section>;
}
