import { getMarket } from "@/lib/control-api";

export default async function MarketPage({ params }: { params: Promise<{ instrument: string }> }) {
  const { instrument } = await params;
  const market = await getMarket(instrument);
  return <div className="page-content"><div className="page-heading"><div><p className="eyebrow">Market detail</p><h1>{instrument}</h1><p>Simulated paper quotes only. Chart, liquidity, and strategy intents require a validated market feed.</p></div><span className="mode-tag">{market.ok ? market.value.freshness : "FEED NOT CONNECTED"}</span></div><section className="panel content-panel"><div className="panel-heading"><h2>Paper quote</h2><span>{market.ok ? market.value.source : "UNAVAILABLE"}</span></div>{market.ok ? <dl className="fact-list"><div><dt>Bid</dt><dd>{market.value.bid}</dd></div><div><dt>Ask</dt><dd>{market.value.ask}</dd></div><div><dt>Observed at</dt><dd>{market.value.observed_at}</dd></div><div><dt>World state version</dt><dd>{market.value.world_state_version}</dd></div><div><dt>Freshness</dt><dd>{market.value.freshness}</dd></div></dl> : <div className="empty-chart large"><div className="chart-grid" aria-hidden="true" /><div className="empty-chart-copy"><strong>No market observations</strong><p>The browser cannot submit orders or supply authoritative quotes.</p></div></div>}</section></div>;
}
