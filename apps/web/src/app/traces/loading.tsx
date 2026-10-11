export default function LoadingTraces() {
  return <div className="page-content" aria-busy="true"><h1>Traces</h1><p role="status">Reading saved trace observations…</p><p>No outcomes are inferred while the read is pending.</p></div>;
}
