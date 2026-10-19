import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { RunCaseSummary, RunDetail } from "../api";
import { api } from "../api";
import { JudgeReliabilityPanel } from "../components/JudgeReliabilityPanel";
import { ErrorState, LoadingState, MetricCard } from "../components/MetricCard";
import { RegressionGatePanel } from "../components/RegressionGatePanel";

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [cases, setCases] = useState<RunCaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([api.getRun(runId), api.getRunCases(runId)])
      .then(([runResult, caseResults]) => {
        if (active) {
          setRun(runResult);
          setCases(caseResults);
        }
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "Unable to load run.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [runId]);

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;
  if (!run) return <ErrorState message="Run was not found." />;

  const summary = run.summary;
  const hasPerAgent = summary.per_agent && Object.keys(summary.per_agent).length > 0;
  const hasSafetyViolations = Boolean(
    summary.safety &&
      (summary.safety.blocked_attacks !== summary.safety.attack_cases ||
        summary.safety.pii_leak_cases > 0),
  );

  return (
    <main className="page">
      <p><Link className="text-link" to="/">← Runs</Link></p>
      <section className="page-heading">
        <div>
          <p className="eyebrow">{run.suite} · {new Date(run.created_at).toLocaleString()}</p>
          <h1>Run details</h1>
          <p className="muted mono">{run.id}</p>
        </div>
        <span className={`status-pill ${run.status}`}>{run.status}</span>
      </section>

      <section className="metric-grid">
        <MetricCard label="Pass rate" value={`${(summary.pass_rate * 100).toFixed(1)}%`} detail={`${summary.passed} passed / ${summary.cases} cases`} />
        <MetricCard label="Average latency" value={`${summary.average_latency_ms.toFixed(1)} ms`} />
        <MetricCard label="Cost / 100 cases" value={formatCost(summary.cost_per_100_cases_usd)} />
        <MetricCard label="Tokens" value={summary.total_tokens?.toLocaleString() ?? "n/a"} />
      </section>

      <RegressionGatePanel
        runId={run.id}
        runStatus={run.status}
        hasSafetyViolations={hasSafetyViolations}
      />

      {hasPerAgent && (
        <section className="panel" aria-label="Per-agent performance">
          <h2>Per-Agent Analysis</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Cases Involved</th>
                  <th>Pass Rate</th>
                  <th>First Failing Cases</th>
                  <th>Avg Latency</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                  <th>Failures</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(summary.per_agent || {}).map(([agentName, agent]) => (
                  <tr key={agentName}>
                    <td className="mono">{agentName}</td>
                    <td>{agent.cases_involved}</td>
                    <td>
                      <span className={`result-status ${agent.pass_rate >= 0.8 ? "pass" : "fail"}`}>
                        {(agent.pass_rate * 100).toFixed(1)}% ({agent.passed}/{agent.cases_involved})
                      </span>
                    </td>
                    <td>{agent.first_failing_cases}</td>
                    <td>{agent.average_latency_ms.toFixed(1)} ms</td>
                    <td>{agent.total_tokens?.toLocaleString() ?? "n/a"}</td>
                    <td>{formatCost(agent.total_cost_usd)}</td>
                    <td>
                      {Object.keys(agent.failure_types).length === 0 ? (
                        <span className="muted">None</span>
                      ) : (
                        Object.entries(agent.failure_types)
                          .map(([ft, count]) => `${ft.replaceAll("_", " ")}: ${count}`)
                          .join(", ")
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="panel">
        <h2>Failure types</h2>
        {Object.keys(summary.failure_types).length === 0 ? (
          <p className="empty-state">No scored failures.</p>
        ) : (
          <ul className="failure-list">
            {Object.entries(summary.failure_types).map(([name, count]) => (
              <li key={name}><span>{name.replaceAll("_", " ")}</span><strong>{count}</strong></li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <h2>Cases</h2>
        {cases.length === 0 ? (
          <p className="empty-state">This run contains no case results.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Repeat</th>
                  <th>Result</th>
                  <th>Agents Involved</th>
                  <th>First Failing Agent</th>
                  <th>Latency</th>
                  <th>Cost</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {cases.map((item) => (
                  <tr key={`${item.case_id}-${item.repeat_index}`}>
                    <td>{item.case_id}</td>
                    <td>{item.repeat_index + 1}</td>
                    <td><span className={`result-status ${item.passed ? "pass" : "fail"}`}>{item.passed ? "Pass" : "Fail"}</span></td>
                    <td>
                      {item.agents_involved && item.agents_involved.length > 0 ? (
                        <span className="mono" style={{ fontSize: "0.8rem" }}>
                          {item.agents_involved.join(", ")}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      {item.first_failing_agent ? (
                        <span className="status-pill danger" style={{ fontSize: "0.75rem" }}>
                          {item.first_failing_agent}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>{item.latency_ms.toFixed(1)} ms</td>
                    <td>{formatCost(item.total_cost_usd)}</td>
                    <td><Link className="text-link" to={`/runs/${encodeURIComponent(run.id)}/cases/${encodeURIComponent(item.case_id)}`}>Trace</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <JudgeReliabilityPanel />
    </main>
  );
}

function formatCost(value: number | null): string {
  return value === null ? "n/a" : `$${value.toFixed(6)}`;
}
