import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { GateEvaluationResult, RunListItem } from "../api";
import { api } from "../api";
import { ErrorState, LoadingState } from "../components/MetricCard";
import { RegressionGatePanel } from "../components/RegressionGatePanel";

export function RegressionGatePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialRunId = searchParams.get("run_id") || "";

  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>(initialRunId);
  const [selectedBaselineId, setSelectedBaselineId] = useState<string>("");
  const [loadingRuns, setLoadingRuns] = useState<boolean>(true);
  const [runsError, setRunsError] = useState<string | null>(null);

  // Threshold controls
  const [passRateThreshold, setPassRateThreshold] = useState<number>(0.9);
  const [latencyBudget, setLatencyBudget] = useState<string>("");
  const [costBudget, setCostBudget] = useState<string>("");

  // Evaluated gate result
  const [gateResult, setGateResult] = useState<GateEvaluationResult | null>(null);
  const [evaluating, setEvaluating] = useState<boolean>(false);
  const [evalError, setEvalError] = useState<string | null>(null);
  const [noBaselineFound, setNoBaselineFound] = useState<boolean>(false);

  // Load run history on mount
  useEffect(() => {
    let active = true;
    setLoadingRuns(true);
    api
      .getRuns()
      .then((res) => {
        if (!active) return;
        setRuns(res.items);
        setLoadingRuns(false);
        // If no run selected yet but items exist, pick the first completed run
        if (!initialRunId && res.items.length > 0) {
          const firstCompleted = res.items.find((r) => r.status === "completed") || res.items[0];
          setSelectedRunId(firstCompleted.id);
          setSearchParams({ run_id: firstCompleted.id }, { replace: true });
        }
      })
      .catch((err) => {
        if (!active) return;
        setRunsError(err instanceof Error ? err.message : String(err));
        setLoadingRuns(false);
      });

    return () => {
      active = false;
    };
  }, []);

  // Update URL and reset evaluation when candidate run selection changes
  const handleSelectRun = (runId: string) => {
    setSelectedRunId(runId);
    setSearchParams(runId ? { run_id: runId } : {}, { replace: true });
    setGateResult(null);
    setEvalError(null);
    setNoBaselineFound(false);
  };

  // Evaluate gate with options
  const evaluateGate = async () => {
    if (!selectedRunId) return;
    setEvaluating(true);
    setEvalError(null);
    setNoBaselineFound(false);

    try {
      const options: {
        passRateThreshold?: number;
        latencyBudget?: number;
        costBudget?: number;
      } = {};

      if (!isNaN(passRateThreshold) && passRateThreshold > 0) {
        options.passRateThreshold = passRateThreshold;
      }
      if (latencyBudget && !isNaN(Number(latencyBudget))) {
        options.latencyBudget = Number(latencyBudget);
      }
      if (costBudget && !isNaN(Number(costBudget))) {
        options.costBudget = Number(costBudget);
      }

      const res = await api.checkGate(
        selectedRunId,
        selectedBaselineId ? selectedBaselineId : undefined,
        options,
      );
      setGateResult(res);
    } catch (caught: unknown) {
      const msg = caught instanceof Error ? caught.message : String(caught);
      if (msg.includes("No approved baseline exists") || msg.includes("404")) {
        setNoBaselineFound(true);
      } else {
        setEvalError(msg);
      }
    } finally {
      setEvaluating(false);
    }
  };

  // Auto-evaluate when selectedRunId changes
  useEffect(() => {
    if (selectedRunId) {
      evaluateGate();
    }
  }, [selectedRunId]);

  const selectedRun = runs.find((r) => r.id === selectedRunId);
  const baselineCandidateRuns = runs.filter(
    (r) => r.id !== selectedRunId && r.status === "completed",
  );

  return (
    <main className="page" data-testid="regression-gate-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Module 4</p>
          <h1>Regression Gate</h1>
        </div>
      </div>

      <p className="muted" style={{ marginTop: "-0.5rem", marginBottom: "1.5rem" }}>
        Automated regression prevention and release readiness evaluation. Compare candidate test
        runs against approved baselines with configurable pass rate, latency, and cost thresholds.
      </p>

      {/* Configuration & Selector Panel */}
      <section className="panel" aria-label="Gate Evaluation Controls">
        <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Gate Evaluation Configuration</h2>
        {loadingRuns ? (
          <LoadingState message="Loading runs..." />
        ) : runsError ? (
          <ErrorState message={runsError} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                gap: "1rem",
              }}
            >
              {/* Candidate Run Picker */}
              <div className="field">
                <label
                  htmlFor="candidate-run-select"
                  style={{ display: "block", fontWeight: 600, marginBottom: "0.3rem" }}
                >
                  Candidate Run (to evaluate):
                </label>
                <select
                  id="candidate-run-select"
                  aria-label="Candidate Run"
                  className="input"
                  style={{ width: "100%", padding: "0.5rem" }}
                  value={selectedRunId}
                  onChange={(e) => handleSelectRun(e.target.value)}
                >
                  <option value="">-- Select a Run --</option>
                  {runs.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.id.slice(0, 8)}... - Suite: {r.suite || "default"} | Status: {r.status}{" "}
                      {r.summary ? `(${r.summary.passed}/${r.summary.cases} cases)` : ""}
                    </option>
                  ))}
                </select>
              </div>

              {/* Baseline Run Picker */}
              <div className="field">
                <label
                  htmlFor="baseline-run-select"
                  style={{ display: "block", fontWeight: 600, marginBottom: "0.3rem" }}
                >
                  Baseline Run:
                </label>
                <select
                  id="baseline-run-select"
                  aria-label="Baseline Run"
                  className="input"
                  style={{ width: "100%", padding: "0.5rem" }}
                  value={selectedBaselineId}
                  onChange={(e) => setSelectedBaselineId(e.target.value)}
                >
                  <option value="">Auto (Latest approved baseline for suite/target)</option>
                  {baselineCandidateRuns.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.id.slice(0, 8)}... - Suite: {r.suite || "default"} (Pass rate:{" "}
                      {r.summary
                        ? `${Math.round(r.summary.pass_rate * 100)}%`
                        : "N/A"}
                      )
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Threshold overrides */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: "1rem",
                padding: "0.8rem",
                background: "rgba(0,0,0,0.15)",
                borderRadius: "6px",
                border: "1px solid var(--panel-border)",
              }}
            >
              <div className="field">
                <label
                  htmlFor="gate-pass-rate"
                  style={{ display: "block", fontSize: "0.85rem", color: "var(--muted)" }}
                >
                  Pass Rate Threshold (0.0 - 1.0):
                </label>
                <input
                  id="gate-pass-rate"
                  type="number"
                  step="0.05"
                  min="0"
                  max="1"
                  value={passRateThreshold}
                  onChange={(e) => setPassRateThreshold(parseFloat(e.target.value))}
                  className="input"
                  style={{ width: "100%", padding: "0.4rem" }}
                />
              </div>

              <div className="field">
                <label
                  htmlFor="gate-latency-budget"
                  style={{ display: "block", fontSize: "0.85rem", color: "var(--muted)" }}
                >
                  Max Latency Budget (seconds):
                </label>
                <input
                  id="gate-latency-budget"
                  type="number"
                  step="1"
                  min="0"
                  placeholder="e.g. 30"
                  value={latencyBudget}
                  onChange={(e) => setLatencyBudget(e.target.value)}
                  className="input"
                  style={{ width: "100%", padding: "0.4rem" }}
                />
              </div>

              <div className="field">
                <label
                  htmlFor="gate-cost-budget"
                  style={{ display: "block", fontSize: "0.85rem", color: "var(--muted)" }}
                >
                  Max Cost Budget (USD $):
                </label>
                <input
                  id="gate-cost-budget"
                  type="number"
                  step="0.01"
                  min="0"
                  placeholder="e.g. 0.50"
                  value={costBudget}
                  onChange={(e) => setCostBudget(e.target.value)}
                  className="input"
                  style={{ width: "100%", padding: "0.4rem" }}
                />
              </div>

              <div style={{ display: "flex", alignItems: "flex-end" }}>
                <button
                  type="button"
                  className="button"
                  onClick={evaluateGate}
                  disabled={!selectedRunId || evaluating}
                  style={{ width: "100%" }}
                >
                  {evaluating ? "Evaluating..." : "Re-evaluate Gate"}
                </button>
              </div>
            </div>

            {selectedRun && (
              <div
                style={{
                  display: "flex",
                  gap: "1.5rem",
                  fontSize: "0.88rem",
                  flexWrap: "wrap",
                  color: "var(--muted)",
                  paddingTop: "0.4rem",
                }}
              >
                <span>
                  <strong>Candidate Run:</strong> {selectedRun.id}
                </span>
                <span>
                  <strong>Suite:</strong> {selectedRun.suite || "default"}
                </span>
                <Link
                  to={`/runs/${selectedRun.id}`}
                  className="text-link"
                  style={{ marginLeft: "auto" }}
                >
                  View Full Run Details &rarr;
                </Link>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Gate Results Panel */}
      {!selectedRunId ? (
        <section className="panel" style={{ textAlign: "center", padding: "3rem 1rem" }}>
          <p className="muted">Please select a candidate run above to evaluate the regression gate.</p>
          <Link to="/runs" className="button" style={{ display: "inline-block", marginTop: "1rem" }}>
            View All Runs
          </Link>
        </section>
      ) : evalError ? (
        <ErrorState message={`Gate evaluation failed: ${evalError}`} />
      ) : noBaselineFound ? (
        <section className="panel">
          <div className="agent-regression-banner" style={{ borderLeftColor: "#3b82f6" }}>
            <h3 style={{ color: "#93c5fd" }}>No Approved Baseline Found</h3>
            <p style={{ margin: "0.3rem 0" }}>
              There is currently no approved baseline for suite{" "}
              <strong>"{selectedRun?.suite || "default"}"</strong> and target{" "}
              <strong>"{selectedRun?.target_id || "sample-agent"}"</strong>.
            </p>
            <p className="muted" style={{ fontSize: "0.88rem", marginBottom: "1rem" }}>
              Promoting this candidate run will establish the first golden baseline for future
              regression detection and release verification.
            </p>
            <button
              type="button"
              className="button"
              onClick={async () => {
                try {
                  await api.promoteBaseline(selectedRunId);
                  await evaluateGate();
                } catch (e) {
                  setEvalError(e instanceof Error ? e.message : String(e));
                }
              }}
            >
              Promote Run {selectedRunId.slice(0, 8)} as Baseline
            </button>
          </div>
        </section>
      ) : (
        <RegressionGatePanel
          gate={gateResult}
          runId={selectedRunId}
          runStatus={selectedRun?.status}
          onBaselinePromoted={() => evaluateGate()}
        />
      )}
    </main>
  );
}
