import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type {
  CalibrationReport,
  RunDetail,
  RunListItem,
  SuiteCaseStats,
  TargetResponse,
} from "../api";
import { api } from "../api";
import { ErrorState, LoadingState, MetricCard } from "../components/MetricCard";

export function OverviewPage() {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [targets, setTargets] = useState<TargetResponse[]>([]);
  const [suiteStats, setSuiteStats] = useState<SuiteCaseStats | null>(null);
  const [calibration, setCalibration] = useState<CalibrationReport | null>(null);
  const [latestRunDetail, setLatestRunDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Target switcher
  const [selectedTargetId, setSelectedTargetId] = useState<string>("all");

  async function loadDashboardData() {
    setLoading(true);
    setError(null);
    try {
      const [runsRes, targetsRes, statsRes] = await Promise.all([
        api.getRuns(),
        api.getTargets().catch(() => ({ items: [] })),
        api.getSuiteStats().catch(() => null),
      ]);

      setRuns(runsRes.items);
      setTargets(targetsRes.items);
      setSuiteStats(statsRes);

      // Load latest run detail if available
      if (runsRes.items.length > 0) {
        try {
          const detail = await api.getRun(runsRes.items[0].id);
          setLatestRunDetail(detail);
        } catch {
          // ignore detail fetch error
        }
      }

      // Load calibration report if available
      try {
        const cal = await api.getCalibration();
        setCalibration(cal);
      } catch {
        // calibration not run yet
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load dashboard data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDashboardData();
  }, []);

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;

  // Filter runs by selected target
  const filteredRuns = runs.filter((r) => {
    if (selectedTargetId === "all") return true;
    if (selectedTargetId === "sample") return !r.target_id;
    return r.target_id === selectedTargetId;
  });

  const latestRun = filteredRuns[0] || runs[0];
  const latestSummary = latestRunDetail?.summary || latestRun?.summary;

  // Aggregate multi-agent stats across latest runs or summary
  const perAgentMap = latestSummary?.per_agent || {};
  const hasPerAgent = Object.keys(perAgentMap).length > 0;

  // Check if current target or any registered target lacks SDK
  const selectedTargetObj = targets.find((t) => t.id === selectedTargetId);
  const isNonSdkTarget =
    (selectedTargetId !== "all" &&
      selectedTargetId !== "sample" &&
      selectedTargetObj &&
      !hasPerAgent) ||
    targets.some((t) => !t.agent_names || t.agent_names.length === 0);

  return (
    <main className="page">
      <section className="page-heading">
        <div>
          <p className="eyebrow">Module 5 · Central Hub</p>
          <h1>AgentProbe Overview</h1>
          <p className="muted">
            Unified multi-agent evaluation platform for safety, regression testing, and performance attribution.
          </p>
        </div>

        {/* Target Switcher */}
        <div style={{ minWidth: "240px" }}>
          <label style={{ fontSize: "0.85rem", fontWeight: 650 }}>
            Target Project Filter:
            <select
              value={selectedTargetId}
              onChange={(e) => setSelectedTargetId(e.target.value)}
              style={{ minHeight: "2.4rem", marginTop: "0.25rem" }}
            >
              <option value="all">All Projects & Runs</option>
              <option value="sample">Built-in LangGraph Agent</option>
              {targets.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      {/* Tracing SDK Notice when per-agent trace is unavailable */}
      {isNonSdkTarget && (
        <div className="sdk-notice-banner" role="alert">
          ℹ️ <strong>Notice:</strong> per-agent analysis unavailable; add the SDK (agentprobe_sdk) to enable it.
        </div>
      )}

      {/* Top-Level Health Metrics Grid */}
      <section className="metric-grid" aria-label="System Health Overview">
        <MetricCard
          label="Registered Targets"
          value={String(targets.length + 1)}
          detail={`${targets.length} custom + 1 built-in`}
        />
        <MetricCard
          label="Test Suite Cases"
          value={String(suiteStats?.total || 0)}
          detail={`${suiteStats?.approved || 0} approved · ${suiteStats?.draft || 0} drafts`}
        />
        <MetricCard
          label="Latest Run Pass Rate"
          value={latestSummary ? `${(latestSummary.pass_rate * 100).toFixed(1)}%` : "No runs"}
          detail={
            latestSummary
              ? `${latestSummary.passed} passed / ${latestSummary.cases} cases`
              : "Execute a run to populate"
          }
        />
        <MetricCard
          label="Judge Agreement"
          value={
            calibration && calibration.overall_pass_agreement_pct != null
              ? `${calibration.overall_pass_agreement_pct.toFixed(1)}%`
              : "Uncalibrated"
          }
          detail={
            calibration
              ? calibration.meets_target
                ? "✓ Meets target (≥80%)"
                : "Below target (<80%)"
              : "Calibrate judge in settings"
          }
        />
      </section>

      {/* Multi-Agent Per-Agent Analysis */}
      <section className="panel" aria-label="Multi-Agent Attribution">
        <div className="table-header-row">
          <div>
            <h2>Per-Agent Performance & Regression Attribution</h2>
            <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>
              Metrics breakdown by individual agent participating in the evaluated workflows.
            </p>
          </div>
          {latestRun && (
            <Link className="button secondary" to={`/runs/${encodeURIComponent(latestRun.id)}`}>
              View Full Run Trace
            </Link>
          )}
        </div>

        {!hasPerAgent ? (
          <div className="empty-state" style={{ padding: "1.5rem 0" }}>
            <p>No per-agent trace metrics available for the selected view.</p>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              To enable per-agent metrics, ensure the target wraps agent calls with{" "}
              <code>@agentprobe_sdk.trace_agent</code> or runs the built-in multi-agent runner.
            </p>
          </div>
        ) : (
          <div className="table-wrap" style={{ marginTop: "1rem" }}>
            <table>
              <thead>
                <tr>
                  <th>Agent Name</th>
                  <th>Cases Involved</th>
                  <th>Pass Rate</th>
                  <th>First Failing Cases</th>
                  <th>Avg Latency</th>
                  <th>Cost Estimate</th>
                  <th>Top Failure Reasons</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(perAgentMap).map(([agentName, agent]) => (
                  <tr key={agentName}>
                    <td>
                      <span className="mono" style={{ fontWeight: 650 }}>
                        {agentName}
                      </span>
                    </td>
                    <td>{agent.cases_involved}</td>
                    <td>
                      <span
                        className={`result-status ${
                          agent.pass_rate >= 0.8 ? "pass" : "fail"
                        }`}
                      >
                        {(agent.pass_rate * 100).toFixed(1)}% ({agent.passed}/{agent.cases_involved})
                      </span>
                    </td>
                    <td>
                      {agent.first_failing_cases > 0 ? (
                        <span className="status-pill danger">{agent.first_failing_cases}</span>
                      ) : (
                        <span className="muted">0</span>
                      )}
                    </td>
                    <td>{agent.average_latency_ms.toFixed(1)} ms</td>
                    <td>
                      {agent.total_cost_usd !== null
                        ? `$${agent.total_cost_usd.toFixed(4)}`
                        : "n/a"}
                    </td>
                    <td>
                      {Object.keys(agent.failure_types).length === 0 ? (
                        <span className="muted">None</span>
                      ) : (
                        Object.entries(agent.failure_types)
                          .map(([ft, count]) => `${ft.replaceAll("_", " ")} (${count})`)
                          .join(", ")
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Quick Navigation to 5 Modules */}
      <section className="panel" aria-label="Module Quick Actions">
        <h2>Five-Module Product Workflow</h2>
        <div className="coverage-grid" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
          <div className="model-result">
            <h3>1. Target Projects</h3>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Upload local zip archives, inspect frameworks and agent candidates, and run dry-run checks.
            </p>
            <Link className="button secondary" to="/targets" style={{ marginTop: "0.5rem", display: "inline-block" }}>
              Manage Targets →
            </Link>
          </div>

          <div className="model-result">
            <h3>2. Test Suite Builder</h3>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Author, edit, import/export test cases, generate LLM drafts, and review safety packs.
            </p>
            <Link className="button secondary" to="/suites" style={{ marginTop: "0.5rem", display: "inline-block" }}>
              Open Suite Builder →
            </Link>
          </div>

          <div className="model-result">
            <h3>3. Agent Runner</h3>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Execute test suites against local targets with concurrency, timeouts, and live progress.
            </p>
            <Link className="button secondary" to="/runs" style={{ marginTop: "0.5rem", display: "inline-block" }}>
              Launch Runner →
            </Link>
          </div>

          <div className="model-result">
            <h3>4. AI Evaluation & Scoring</h3>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              NFKC normalization, subsequence tool matching, failure taxonomy, and safety checks.
            </p>
            <Link className="button secondary" to="/runs" style={{ marginTop: "0.5rem", display: "inline-block" }}>
              View Scored Runs →
            </Link>
          </div>

          <div className="model-result">
            <h3>5. Regression Gate</h3>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Compare candidate runs against approved baselines, classify buckets, and gate promotions.
            </p>
            <Link className="button secondary" to="/gate" style={{ marginTop: "0.5rem", display: "inline-block" }}>
              Check Gate →
            </Link>
          </div>
        </div>
      </section>

      {/* Recent Evaluation Runs */}
      <section className="panel" aria-label="Recent Runs">
        <div className="table-header-row">
          <h2>Recent Evaluation Runs</h2>
          <Link className="text-link" to="/runs">
            View All Runs →
          </Link>
        </div>

        {filteredRuns.length === 0 ? (
          <p className="empty-state">No evaluation runs recorded yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Suite</th>
                  <th>Status</th>
                  <th>Pass Rate</th>
                  <th>Latency</th>
                  <th>Cost / 100</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredRuns.slice(0, 10).map((run) => (
                  <tr key={run.id}>
                    <td>{new Date(run.created_at).toLocaleString()}</td>
                    <td>{run.suite}</td>
                    <td>
                      <span className={`status-pill ${run.status}`}>{run.status}</span>
                    </td>
                    <td>
                      {run.summary ? (
                        <span
                          className={`result-status ${
                            run.summary.pass_rate >= 0.8 ? "pass" : "fail"
                          }`}
                        >
                          {(run.summary.pass_rate * 100).toFixed(1)}%
                        </span>
                      ) : (
                        <span className="muted">N/A</span>
                      )}
                    </td>
                    <td>
                      {run.summary?.average_latency_ms != null
                        ? `${run.summary.average_latency_ms.toFixed(0)} ms`
                        : "n/a"}
                    </td>
                    <td>
                      {run.summary?.cost_per_100_cases_usd !== null &&
                      run.summary?.cost_per_100_cases_usd !== undefined
                        ? `$${run.summary.cost_per_100_cases_usd.toFixed(4)}`
                        : "n/a"}
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: "0.4rem" }}>
                        <Link
                          className="button secondary"
                          to={`/runs/${encodeURIComponent(run.id)}`}
                          style={{ padding: "0.2rem 0.5rem", fontSize: "0.78rem" }}
                        >
                          Details
                        </Link>
                        <Link
                          className="button secondary"
                          to={`/gate?run_id=${encodeURIComponent(run.id)}`}
                          style={{ padding: "0.2rem 0.5rem", fontSize: "0.78rem" }}
                        >
                          Gate
                        </Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
