import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type {
  CompareRunsResponse,
  ModelComparison,
  ModelComparisonListItem,
  RunListItem,
  RunProgress,
  SuitePack,
} from "../api";
import { api } from "../api";
import { ErrorState, LoadingState } from "../components/MetricCard";
import { JudgeReliabilityPanel } from "../components/JudgeReliabilityPanel";
import { RegressionGatePanel } from "../components/RegressionGatePanel";

export function RunsPage() {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [comparisons, setComparisons] = useState<ModelComparisonListItem[]>([]);
  const [suitePacks, setSuitePacks] = useState<SuitePack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedA, setSelectedA] = useState("");
  const [selectedB, setSelectedB] = useState("");
  const [runComparison, setRunComparison] = useState<CompareRunsResponse | null>(null);
  const [modelComparison, setModelComparison] = useState<ModelComparison | null>(null);
  const [comparisonId, setComparisonId] = useState("");
  const [suite, setSuite] = useState("all");
  const [repeats, setRepeats] = useState(1);
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem("agentprobe_api_key") ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Runner controls and live progress state
  const [runSuite, setRunSuite] = useState("all");
  const [runRepeats, setRunRepeats] = useState(1);
  const [runConcurrency, setRunConcurrency] = useState(4);
  const [runTimeout, setRunTimeout] = useState(30);
  const [runCallSpacing, setRunCallSpacing] = useState(0);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeProgress, setActiveProgress] = useState<RunProgress | null>(null);
  const [startingRun, setStartingRun] = useState(false);
  const [cancellingRun, setCancellingRun] = useState(false);

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const [runResponse, comparisonResponse, packResponse] = await Promise.all([
        api.getRuns(),
        api.getModelComparisons(),
        api.getSuitePacks(),
      ]);
      setRuns(runResponse.items);
      setComparisons(comparisonResponse.items);
      setSuitePacks(packResponse.items);
      setSelectedA((current) => current || runResponse.items[0]?.id || "");
      setSelectedB((current) => current || runResponse.items[1]?.id || "");
      const latestComparison = comparisonResponse.items[0];
      setComparisonId(latestComparison?.id ?? "");
      if (latestComparison) {
        setModelComparison(await api.getModelComparison(latestComparison.id));
      } else {
        setModelComparison(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load runs.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadData();
  }, []);

  async function compareSelectedRuns() {
    setActionError(null);
    try {
      setRunComparison(await api.compareRuns(selectedA, selectedB));
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to compare runs.");
    }
  }

  async function startEvaluationRun(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    sessionStorage.setItem("agentprobe_api_key", apiKey);
    setStartingRun(true);
    setActionError(null);
    const newRunId = crypto.randomUUID();
    setActiveRunId(newRunId);
    setActiveProgress({
      run_id: newRunId,
      status: "running",
      total_cases: 0,
      completed_cases: 0,
      passed_cases: 0,
      failed_cases: 0,
      error_cases: 0,
      estimated_eta_seconds: null,
      elapsed_seconds: 0,
      is_running: true,
      is_cancelled: false,
    });

    const pollTimer = setInterval(async () => {
      try {
        const prog = await api.getRunProgress(newRunId);
        setActiveProgress(prog);
        if (prog.status !== "running") {
          clearInterval(pollTimer);
        }
      } catch {
        // ignore transient poll error
      }
    }, 800);

    try {
      await api.createRun({
        run_id: newRunId,
        suite: runSuite,
        repeats: runRepeats,
        concurrency: runConcurrency,
        timeout_seconds: runTimeout,
        call_spacing_seconds: runCallSpacing,
      });
      clearInterval(pollTimer);
      const finalProg = await api.getRunProgress(newRunId).catch(() => null);
      if (finalProg) setActiveProgress(finalProg);
      await loadData();
    } catch (caught) {
      clearInterval(pollTimer);
      setActionError(caught instanceof Error ? caught.message : "Run failed.");
    } finally {
      setStartingRun(false);
    }
  }

  async function cancelRun() {
    if (!activeRunId) return;
    setCancellingRun(true);
    try {
      await api.cancelRun(activeRunId);
      const prog = await api.getRunProgress(activeRunId).catch(() => null);
      if (prog) setActiveProgress(prog);
      await loadData();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to cancel run.");
    } finally {
      setCancellingRun(false);
    }
  }

  async function startModelComparison(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    sessionStorage.setItem("agentprobe_api_key", apiKey);
    setSubmitting(true);
    setActionError(null);
    try {
      const result = await api.createModelComparison({ suite, repeats });
      setModelComparison(result);
      setComparisonId(result.id);
      await loadData();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to start comparison.");
    } finally {
      setSubmitting(false);
    }
  }

  async function openModelComparison(id: string) {
    setComparisonId(id);
    setActionError(null);
    try {
      setModelComparison(await api.getModelComparison(id));
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to load comparison.");
    }
  }

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;

  return (
    <main className="page">
      <section className="page-heading">
        <div>
          <p className="eyebrow">Evaluation history</p>
          <h1>Runs</h1>
        </div>
        <button className="button secondary" onClick={() => void loadData()} type="button">
          Refresh
        </button>
      </section>

      {actionError && <p className="error" style={{ marginBottom: "1rem" }}>{actionError}</p>}

      {/* Live Runner Progress Banner */}
      {activeProgress && (
        <section className="panel" style={{ borderLeft: "4px solid var(--accent)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <p className="eyebrow">
                {activeProgress.status === "running" ? "Run in progress" : `Run ${activeProgress.status}`}
              </p>
              <h3>
                {activeProgress.completed_cases} / {activeProgress.total_cases || "?"} cases done
              </h3>
            </div>
            {activeProgress.status === "running" && (
              <button
                className="button secondary"
                onClick={() => void cancelRun()}
                disabled={cancellingRun}
                type="button"
              >
                {cancellingRun ? "Cancelling..." : "Cancel Run"}
              </button>
            )}
          </div>
          <div className="metric-grid" style={{ marginTop: "1rem" }}>
            <div className="metric-card">
              <p className="metric-label">Passed</p>
              <p className="metric-value" style={{ color: "#65d6be" }}>{activeProgress.passed_cases}</p>
            </div>
            <div className="metric-card">
              <p className="metric-label">Failed</p>
              <p className="metric-value" style={{ color: "#ff9b9b" }}>{activeProgress.failed_cases}</p>
            </div>
            <div className="metric-card">
              <p className="metric-label">Execution Errors</p>
              <p className="metric-value">{activeProgress.error_cases}</p>
            </div>
            <div className="metric-card">
              <p className="metric-label">Estimated ETA</p>
              <p className="metric-value">
                {activeProgress.estimated_eta_seconds != null
                  ? `${Math.round(activeProgress.estimated_eta_seconds)}s`
                  : activeProgress.status === "running" ? "Estimating..." : "Done"}
              </p>
            </div>
          </div>
        </section>
      )}

      {/* Evaluation Runner Controls */}
      <section className="panel">
        <h2>Start evaluation run</h2>
        <p className="muted">
          Configure concurrency, per-case timeouts, repeat counts, and call spacing for rate limits.
        </p>
        <form className="comparison-form" onSubmit={(event) => void startEvaluationRun(event)}>
          <label>Suite
            <select value={runSuite} onChange={(e) => setRunSuite(e.target.value)}>
              <option value="all">All bundled suites</option>
              {suitePacks.map((p) => (
                <option key={p.id} value={p.id}>{p.name} ({p.cases} cases)</option>
              ))}
              <option value="custom">Custom saved cases</option>
            </select>
          </label>
          <label>Repeats (N)
            <input
              type="number"
              min={1}
              max={10}
              value={runRepeats}
              onChange={(e) => setRunRepeats(Number(e.target.value))}
            />
          </label>
          <label>Concurrency
            <input
              type="number"
              min={1}
              max={20}
              value={runConcurrency}
              onChange={(e) => setRunConcurrency(Number(e.target.value))}
            />
          </label>
          <label>Case Timeout (s)
            <input
              type="number"
              min={1}
              value={runTimeout}
              onChange={(e) => setRunTimeout(Number(e.target.value))}
            />
          </label>
          <label>Call Spacing (s)
            <input
              type="number"
              min={0}
              step={0.1}
              value={runCallSpacing}
              onChange={(e) => setRunCallSpacing(Number(e.target.value))}
            />
          </label>
          <button className="button" disabled={startingRun} type="submit">
            {startingRun ? "Running..." : "Run Evaluation"}
          </button>
        </form>
      </section>

      <section className="panel">
        <h2>Saved runs</h2>
        {runs.length === 0 ? (
          <p className="empty-state">No runs have been recorded yet. Run the API evaluation first.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Created</th><th>Suite</th><th>Pass rate</th><th>Latency</th><th>Cost / 100</th><th /></tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>{formatDate(run.created_at)}</td>
                    <td>{run.suite}</td>
                    <td>{formatPercent(run.summary.pass_rate)}</td>
                    <td>{formatLatency(run.summary.average_latency_ms)}</td>
                    <td>{formatCost(run.summary.cost_per_100_cases_usd)}</td>
                    <td><Link className="text-link" to={`/runs/${encodeURIComponent(run.id)}`}>Details</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Compare saved runs</h2>
        {runs.length < 2 ? (
          <p className="empty-state">Record at least two runs to compare their pass rates.</p>
        ) : (
          <>
            <div className="form-row">
              <label>Run A<select value={selectedA} onChange={(event) => setSelectedA(event.target.value)}>
                {runs.map((run) => <option key={run.id} value={run.id}>{run.suite} · {formatDate(run.created_at)}</option>)}
              </select></label>
              <label>Run B<select value={selectedB} onChange={(event) => setSelectedB(event.target.value)}>
                {runs.map((run) => <option key={run.id} value={run.id}>{run.suite} · {formatDate(run.created_at)}</option>)}
              </select></label>
              <button className="button" disabled={!selectedA || !selectedB} onClick={() => void compareSelectedRuns()} type="button">
                Compare
              </button>
            </div>
            {runComparison && (
              <>
                <p className="result-line">
                  Pass rate change (B − A): <strong>{formatSignedPercent(runComparison.pass_rate_delta)}</strong>
                </p>
                {runComparison.gate && (
                  <div style={{ marginTop: "1rem" }}>
                    <RegressionGatePanel gate={runComparison.gate} />
                  </div>
                )}
              </>
            )}
          </>
        )}
      </section>

      <section className="panel">
        <h2>Compare configured model with Gemini</h2>
        <p className="muted">
          Uses the same selected suite for both models. Cost stays n/a unless token prices are configured.
          The API key is kept in this browser tab only.
        </p>
        <form className="comparison-form" onSubmit={(event) => void startModelComparison(event)}>
          <label>Local API key
            <input
              autoComplete="off"
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="AGENTPROBE_API_KEY"
              type="password"
              value={apiKey}
            />
          </label>
          <label>Suite
            <input list="suite-pack-options" onChange={(event) => setSuite(event.target.value)} required value={suite} />
            <datalist id="suite-pack-options">
              <option value="custom">Custom approved cases</option>
              {suitePacks.map((pack) => <option key={pack.id} value={pack.id}>{pack.name}</option>)}
            </datalist>
          </label>
          <label>Repeats
            <input
              max={10}
              min={1}
              onChange={(event) => setRepeats(Number(event.target.value))}
              type="number"
              value={repeats}
            />
          </label>
          <button className="button" disabled={submitting} type="submit">
            {submitting ? "Comparing…" : "Run model comparison"}
          </button>
        </form>
        {comparisons.length > 0 && (
          <label className="comparison-select">Saved model comparison
            <select value={comparisonId} onChange={(event) => void openModelComparison(event.target.value)}>
              {comparisons.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.primary_model} vs {item.comparison_model} · {formatDate(item.created_at)}
                </option>
              ))}
            </select>
          </label>
        )}
        {actionError && <p role="alert" className="error">{actionError}</p>}
        {modelComparison && <ModelComparisonSummary result={modelComparison} />}
      </section>

      <JudgeReliabilityPanel />
    </main>
  );
}

function ModelComparisonSummary({ result }: { result: ModelComparison }) {
  return (
    <div className="comparison-results">
      {[result.primary, result.comparison].map((model) => (
        <article className="model-result" key={model.model}>
          <h3>{model.model}</h3>
          <dl>
            <div><dt>Accuracy</dt><dd>{formatPercent(model.accuracy)}</dd></div>
            <div><dt>Latency / case</dt><dd>{formatLatency(model.average_latency_ms)}</dd></div>
            <div><dt>Estimated cost / 100 cases</dt><dd>{formatCost(model.cost_per_100_cases_usd)}</dd></div>
            <div><dt>Tokens</dt><dd>{model.total_tokens?.toLocaleString() ?? "n/a"}</dd></div>
            <div><dt>Execution errors</dt><dd>{model.execution_errors}</dd></div>
          </dl>
        </article>
      ))}
    </div>
  );
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function formatSignedPercent(value: number): string {
  const percentage = value * 100;
  return `${percentage > 0 ? "+" : ""}${percentage.toFixed(1)}%`;
}

function formatLatency(value: number): string {
  return `${value.toFixed(1)} ms`;
}

function formatCost(value: number | null): string {
  return value === null ? "n/a" : `$${value.toFixed(4)}`;
}
