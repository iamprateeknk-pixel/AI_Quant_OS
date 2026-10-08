import { read } from "@/lib/control-api";
import { parseRegistry } from "./registry";

export async function AgentRegistryPanel() {
  const result = await read("/api/v1/agents/registry", parseRegistry);
  return <section className="panel content-panel">
    <div className="panel-heading"><h2>Registered agent versions</h2><span>{result.ok ? `${new Set(result.value.map((agent) => agent.agent_id)).size} ROLES · ${result.value.length} VERSIONS` : "UNAVAILABLE"}</span></div>
    <p className="panel-muted">Implementation status describes development coverage. Lifecycle is the backend restriction for this version. Registered tools are permission ceilings; they do not prove a handler is available. Agents have no capital authority.</p>
    {result.ok ? result.value.length ? <div className="table-scroll"><table className="agent-registry-table">
      <caption className="sr-only">Paper agent versions, lifecycle restrictions and declared capabilities</caption>
      <thead><tr><th scope="col">Agent / version</th><th scope="col">Squad</th><th scope="col">Development</th><th scope="col">Lifecycle</th><th scope="col">Mission and contract</th></tr></thead>
      <tbody>{result.value.map((agent) => <tr id={`${agent.agent_id}-${agent.agent_version}`} key={`${agent.agent_id}@${agent.agent_version}`}>
        <th scope="row">{agent.agent_id} · {agent.name}<br /><span className="mono">{agent.agent_version}</span></th>
        <td>{agent.squad}</td><td>{agent.implementation_status}</td><td>{agent.lifecycle}</td>
        <td><p>{agent.mission}</p><details className="agent-contract"><summary>View contract for {agent.agent_id} v{agent.agent_version}</summary><dl className="fact-list">
          <div><dt>Output</dt><dd>{agent.output_type}</dd></div>
          <div><dt>Required inputs</dt><dd>{agent.required_inputs.join(", ")}</dd></div>
          <div><dt>Declared tools</dt><dd>{agent.allowed_tools.join(", ") || "None"}</dd></div>
          <div><dt>Timeout</dt><dd>{agent.timeout_seconds} seconds</dd></div>
          <div><dt>Manifest reference</dt><dd className="mono agent-manifest-ref">{agent.manifest_ref}</dd></div>
        </dl></details></td>
      </tr>)}</tbody>
    </table></div> : <div className="empty-list">No agent versions registered.</div> : <div className="empty-list" role="status">Agent registry unavailable. {result.reason}. Refresh to try again.</div>}
  </section>;
}
