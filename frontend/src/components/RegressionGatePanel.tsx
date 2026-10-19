import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { CaseBucket, GateEvaluationResult } from "../api";
import { api } from "../api";

export interface RegressionGatePanelProps {
  gate?: GateEvaluationResult | null;
  runId?: string;
  runStatus?: string;
  hasSafetyViolations?: boolean;
  onBaselinePromoted?: () => void;
}

export function RegressionGatePanel({
  gate: initialGate,
  runId,
  runStatus = "completed",
  hasSafetyViolations = false,
  onBaselinePromoted,
}: RegressionGatePanelProps) {
  const [gate, setGate] = useState<GateEvaluationResult | null>(initialGate ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noBaseline, setNoBaseline] = useState(false);

  // Promotion modal & state
  const [showPromoteConfirm, setShowPromoteConfirm] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [promoteSuccess, setPromoteSuccess] = useState<string | null>(null);
  const [promoteError, setPromoteError] = useState<string | null>(null);

  // Filter bucket
  const [bucketFilter, setBucketFilter] = useState<string>("all");

  useEffect(() => {
    if (initialGate) {
      setGate(initialGate);
      setNoBaseline(false);
      return;
    }
    if (!runId) return;

    let active = true;
    setLoading(true);
    setError(null);
    setNoBaseline(false);

    api
      .checkGate(runId)
      .then((res) => {
        if (active) {
          setGate(res);
          setNoBaseline(false);
        }
      })
      .catch((caught: unknown) => {
        if (!active) return;
        const msg = caught instanceof Error ? caught.message : String(caught);
        if (msg.includes("No approved baseline exists") || msg.includes("404")) {
          setNoBaseline(true);
        } else {
          setError(msg);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [initialGate, runId]);

  async function handleConfirmPromote() {
    const targetRunId = gate?.candidate_run_id || runId;
    if (!targetRunId) return;

    setPromoting(true);
    setPromoteError(null);
    setPromoteSuccess(null);
    try {
      await api.promoteBaseline(targetRunId);
      setPromoteSuccess(`Run ${targetRunId} successfully promoted to approved baseline!`);
      setShowPromoteConfirm(false);
      if (onBaselinePromoted) onBaselinePromoted();
      // Reload gate if runId is available
      if (runId) {
        try {
          const updated = await api.checkGate(runId);
          setGate(updated);
          setNoBaseline(false);
        } catch {
          // ignore reload error
        }
      }
    } catch (caught) {
      setPromoteError(
        caught instanceof Error ? caught.message : "Failed to promote baseline.",
      );
    } finally {
      setPromoting(false);
    }
  }

  const effectiveRunId = gate?.candidate_run_id || runId;
  const canPromote =
    Boolean(effectiveRunId) &&
    runStatus === "completed" &&
    !hasSafetyViolations &&
    (!gate || !gate.cases.some((c) => c.safety_violations.length > 0));

  const promoteDisabledReason =
    runStatus !== "completed"
      ? "Only completed runs can be promoted."
      : hasSafetyViolations
      ? "Runs with safety failures cannot be promoted."
      : undefined;

  if (loading) {
    return (
      <section className="panel" aria-label="Regression gate evaluation">
        <h2>Module 4: Regression Gate</h2>
        <p className="state-message">Checking regression gate against approved baseline…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="panel" aria-label="Regression gate evaluation">
        <h2>Module 4: Regression Gate</h2>
        <p className="error">Gate check error: {error}</p>
      </section>
    );
  }

  if (noBaseline && !gate) {
    return (
      <section className="panel" aria-label="Regression gate evaluation">
        <div className="gate-heading-row">
          <div>
            <h2>Module 4: Regression Gate</h2>
            <p className="muted">
              No approved baseline exists for this run's suite and target yet.
            </p>
          </div>
          {effectiveRunId && (
            <button
              className="button"
              disabled={!canPromote}
              title={promoteDisabledReason}
              onClick={() => setShowPromoteConfirm(true)}
              type="button"
            >
              Promote to Baseline
            </button>
          )}
        </div>
        {promoteDisabledReason && (
          <p className="muted" style={{ fontSize: "0.85rem", color: "#ffaaaa" }}>
            {promoteDisabledReason}
          </p>
        )}
        {promoteSuccess && <p className="result-status pass">{promoteSuccess}</p>}
        {promoteError && <p className="error">{promoteError}</p>}

        {showPromoteConfirm && (
          <div className="modal-backdrop" role="dialog" aria-modal="true">
            <div className="modal-dialog">
              <h3>Confirm Baseline Promotion</h3>
              <p>
                Are you sure you want to promote run <strong>{effectiveRunId}</strong> as the
                approved baseline?
              </p>
              <p className="muted" style={{ fontSize: "0.85rem" }}>
                Future runs against this suite and target will be evaluated against this run to detect regressions.
              </p>
              <div className="modal-actions">
                <button
                  className="button secondary"
                  onClick={() => setShowPromoteConfirm(false)}
                  disabled={promoting}
                  type="button"
                >
                  Cancel
                </button>
                <button
                  className="button"
                  onClick={() => void handleConfirmPromote()}
                  disabled={promoting}
                  type="button"
                >
                  {promoting ? "Promoting…" : "Confirm Promotion"}
                </button>
              </div>
            </div>
          </div>
        )}
      </section>
    );
  }

  if (!gate) return null;

  const filteredCases = gate.cases.filter((c) => {
    if (bucketFilter === "all") return true;
    return c.bucket === bucketFilter;
  });

  const agentRegressions = Object.values(gate.per_agent_regressions || {});

  return (
    <section className="panel" aria-label="Regression gate evaluation">
      <div className="gate-heading-row">
        <div>
          <h2>Module 4: Regression Gate</h2>
          <p className="eyebrow" style={{ marginTop: "0.2rem" }}>
            Candidate: <span className="mono">{gate.candidate_run_id}</span> vs Baseline:{" "}
            <span className="mono">{gate.baseline_run_id}</span>
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <span
            className={`gate-badge ${gate.passed ? "pass" : "fail"}`}
            role="status"
            aria-label={`Gate Verdict: ${gate.passed ? "Passed" : "Failed"}`}
          >
            GATE {gate.passed ? "PASSED" : "FAILED"}
          </span>
          {effectiveRunId && (
            <button
              className="button"
              disabled={!canPromote}
              title={promoteDisabledReason}
              onClick={() => setShowPromoteConfirm(true)}
              type="button"
            >
              Promote to Baseline
            </button>
          )}
        </div>
      </div>

      {promoteDisabledReason && (
        <p className="muted" style={{ fontSize: "0.85rem", color: "#ffaaaa", margin: "0.4rem 0" }}>
          {promoteDisabledReason}
        </p>
      )}
      {promoteSuccess && <p className="result-status pass">{promoteSuccess}</p>}
      {promoteError && <p className="error">{promoteError}</p>}

      {/* Metrics Grid */}
      <div className="gate-metrics-grid">
        <div className="gate-metric-card">
          <p className="metric-label">Pass Rate Delta</p>
          <p
            className={`metric-value ${
              gate.pass_rate_delta >= 0 ? "text-success" : "text-danger"
            }`}
          >
            {formatSignedPercent(gate.pass_rate_delta)}
          </p>
          <p className="muted" style={{ fontSize: "0.75rem", margin: 0 }}>
            Baseline: {(gate.baseline_pass_rate * 100).toFixed(1)}% → Candidate:{" "}
            {(gate.candidate_pass_rate * 100).toFixed(1)}%
          </p>
        </div>

        <div className="gate-metric-card">
          <p className="metric-label">Latency Delta</p>
          <p
            className={`metric-value ${
              gate.latency_delta_pct !== null && gate.latency_delta_pct > gate.latency_budget
                ? "text-danger"
                : "text-neutral"
            }`}
          >
            {gate.latency_delta_pct !== null ? formatSignedPercent(gate.latency_delta_pct) : "n/a"}
          </p>
          <p className="muted" style={{ fontSize: "0.75rem", margin: 0 }}>
            Budget: +{(gate.latency_budget * 100).toFixed(0)}%
          </p>
        </div>

        <div className="gate-metric-card">
          <p className="metric-label">Cost Delta</p>
          <p
            className={`metric-value ${
              gate.cost_delta_pct !== null && gate.cost_delta_pct > gate.cost_budget
                ? "text-danger"
                : "text-neutral"
            }`}
          >
            {gate.cost_delta_pct !== null ? formatSignedPercent(gate.cost_delta_pct) : "n/a"}
          </p>
          <p className="muted" style={{ fontSize: "0.75rem", margin: 0 }}>
            Budget: +{(gate.cost_budget * 100).toFixed(0)}%
          </p>
        </div>

        <div className="gate-metric-card">
          <p className="metric-label">Case Classifications</p>
          <div className="chip-row">
            <span className="bucket-chip regressed" title="Passed in baseline, now failing">
              {gate.summary_counts.regressed || 0} Regressed
            </span>
            <span className="bucket-chip fixed" title="Failed in baseline, now passing">
              {gate.summary_counts.fixed || 0} Fixed
            </span>
            <span
              className="bucket-chip persistent_failure"
              title="Failed in both baseline and candidate"
            >
              {gate.summary_counts.persistent_failure || 0} Failed
            </span>
            <span className="bucket-chip passing" title="Passed in both">
              {gate.summary_counts.passing || 0} Passed
            </span>
          </div>
        </div>
      </div>

      {/* Reasons Callout */}
      {gate.reasons.length > 0 && (
        <div className="gate-reasons-box" role="alert">
          <strong>Gate Failure Reasons:</strong>
          <ul>
            {gate.reasons.map((reason, idx) => (
              <li key={idx}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Per-Agent Regression Attribution Banner */}
      {agentRegressions.length > 0 && (
        <div className="agent-regression-banner" aria-label="Per-agent regression attribution">
          <h3>Per-Agent Regression Attribution</h3>
          <ul>
            {agentRegressions.map((attr) => (
              <li key={attr.agent_name}>
                <strong>{attr.summary}</strong>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Cases Comparison Table (Regressed cases first) */}
      <div style={{ marginTop: "1.5rem" }}>
        <div className="table-header-row">
          <h3>Case Comparison (Regressed Cases First)</h3>
          <div className="filter-controls">
            <label style={{ flexDirection: "row", alignItems: "center", gap: "0.5rem" }}>
              Filter:
              <select
                value={bucketFilter}
                onChange={(e) => setBucketFilter(e.target.value)}
                style={{ width: "auto", minHeight: "2rem", padding: "0.2rem 0.5rem" }}
              >
                <option value="all">All Cases ({gate.cases.length})</option>
                <option value="regressed">Regressed ({gate.summary_counts.regressed || 0})</option>
                <option value="fixed">Fixed ({gate.summary_counts.fixed || 0})</option>
                <option value="persistent_failure">
                  Persistent Failure ({gate.summary_counts.persistent_failure || 0})
                </option>
                <option value="passing">Passing ({gate.summary_counts.passing || 0})</option>
              </select>
            </label>
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Case ID</th>
                <th>Classification</th>
                <th>First Failing Agent</th>
                <th>Baseline (Before)</th>
                <th>Candidate (After)</th>
                <th>Trace</th>
              </tr>
            </thead>
            <tbody>
              {filteredCases.map((item) => (
                <tr
                  key={item.case_id}
                  className={item.bucket === "regressed" ? "row-regressed" : ""}
                >
                  <td className="mono" style={{ fontWeight: 650 }}>
                    {item.case_id}
                  </td>
                  <td>
                    <span className={`bucket-pill ${item.bucket}`}>{formatBucket(item.bucket)}</span>
                  </td>
                  <td>
                    {item.first_failing_agent ? (
                      <span className="status-pill danger" style={{ fontSize: "0.75rem" }}>
                        {item.first_failing_agent}
                      </span>
                    ) : item.agents_involved.length > 0 ? (
                      <span className="mono muted" style={{ fontSize: "0.75rem" }}>
                        {item.agents_involved.join(", ")}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    <div>
                      <span
                        className={`result-status ${item.baseline_passed ? "pass" : "fail"}`}
                        style={{ fontSize: "0.75rem", padding: "0.15rem 0.4rem" }}
                      >
                        {item.baseline_passed ? "Pass" : "Fail"}
                      </span>{" "}
                      <span className="muted" style={{ fontSize: "0.75rem" }}>
                        {item.baseline_latency_ms.toFixed(0)} ms
                      </span>
                    </div>
                    {item.baseline_error && (
                      <p className="trace-meta error" style={{ fontSize: "0.75rem", margin: "0.2rem 0" }}>
                        {item.baseline_error}
                      </p>
                    )}
                    {item.baseline_output && (
                      <p
                        className="muted"
                        style={{
                          fontSize: "0.75rem",
                          margin: "0.2rem 0",
                          maxWidth: "260px",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {item.baseline_output}
                      </p>
                    )}
                  </td>
                  <td>
                    <div>
                      <span
                        className={`result-status ${item.candidate_passed ? "pass" : "fail"}`}
                        style={{ fontSize: "0.75rem", padding: "0.15rem 0.4rem" }}
                      >
                        {item.candidate_passed ? "Pass" : "Fail"}
                      </span>{" "}
                      <span className="muted" style={{ fontSize: "0.75rem" }}>
                        {item.candidate_latency_ms.toFixed(0)} ms
                      </span>
                    </div>
                    {item.candidate_error && (
                      <p className="trace-meta error" style={{ fontSize: "0.75rem", margin: "0.2rem 0" }}>
                        {item.candidate_error}
                      </p>
                    )}
                    {item.candidate_output && (
                      <p
                        className="muted"
                        style={{
                          fontSize: "0.75rem",
                          margin: "0.2rem 0",
                          maxWidth: "260px",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {item.candidate_output}
                      </p>
                    )}
                  </td>
                  <td>
                    {effectiveRunId && (
                      <Link
                        className="text-link"
                        to={`/runs/${encodeURIComponent(effectiveRunId)}/cases/${encodeURIComponent(
                          item.case_id,
                        )}`}
                      >
                        Trace
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Promotion confirmation modal */}
      {showPromoteConfirm && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal-dialog">
            <h3>Confirm Baseline Promotion</h3>
            <p>
              Are you sure you want to promote run <strong>{effectiveRunId}</strong> as the
              approved baseline?
            </p>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Future runs against this suite and target will be evaluated against this run to detect regressions.
            </p>
            <div className="modal-actions">
              <button
                className="button secondary"
                onClick={() => setShowPromoteConfirm(false)}
                disabled={promoting}
                type="button"
              >
                Cancel
              </button>
              <button
                className="button"
                onClick={() => void handleConfirmPromote()}
                disabled={promoting}
                type="button"
              >
                {promoting ? "Promoting…" : "Confirm Promotion"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function formatSignedPercent(value: number): string {
  const percentage = value * 100;
  return `${percentage > 0 ? "+" : ""}${percentage.toFixed(1)}%`;
}

function formatBucket(bucket: CaseBucket): string {
  switch (bucket) {
    case "regressed":
      return "Regressed";
    case "fixed":
      return "Fixed";
    case "persistent_failure":
      return "Persistent Failure";
    case "passing":
      return "Passing";
  }
}
