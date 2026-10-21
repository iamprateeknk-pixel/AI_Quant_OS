import { getCurrencyPortfolio } from "@/lib/control-api";
import { TableScrollRegion } from "../shared/table-scroll-region";

function issueLabel(issue: string | null): string {
  switch (issue) {
    case "MISSING_FX_RATE": return "Price missing";
    case "STALE_FX_RATE": return "Price expired";
    case "UNTRUSTED_FX_RATE": return "Price untrusted";
    case null: return "Ready";
    default: return "Valuation unavailable";
  }
}

export async function CurrencyExposurePanel() {
  const result = await getCurrencyPortfolio();
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Currency exposure</h2><span>{result.ok ? result.value.reporting_currency : "UNAVAILABLE"}</span></div>
    {result.ok ? <>
      {!result.value.valuation_available && <p className="panel-muted">A fresh conversion price is required to calculate all exposure values.</p>}
      {result.value.currencies.length ? <TableScrollRegion label="Currency exposure table"><table>
        <thead><tr><th>Currency</th><th>Filled balance</th><th>Pending balance range</th><th>Worst case, {result.value.reporting_currency}</th><th>Price status</th></tr></thead>
        <tbody>{result.value.currencies.map((item) => <tr key={item.currency}>
          <td>{item.currency}</td><td className="mono">{item.filled_balance}</td>
          <td className="mono">{item.minimum_balance !== null && item.maximum_balance !== null ? `${item.minimum_balance} to ${item.maximum_balance}` : "Unavailable"}</td>
          <td className="mono">{item.worst_case_exposure_reporting ?? "Unavailable"}</td><td>{issueLabel(item.issue)}</td>
        </tr>)}</tbody>
      </table></TableScrollRegion> : <div className="empty-list">No currency balances or pending orders recorded.</div>}
      <p className="panel-muted">Both currency legs and fees are included. Pending orders are counted separately so unfilled buys and sells cannot cancel each other&apos;s risk.</p>
    </> : <div className="empty-list">Currency exposure service unavailable.</div>}
  </section>;
}
