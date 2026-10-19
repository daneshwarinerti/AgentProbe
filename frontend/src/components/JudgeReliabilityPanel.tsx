import { useEffect, useState } from "react";
import type { CalibrationReport } from "../api";
import { api } from "../api";
import { MetricCard } from "./MetricCard";

export function JudgeReliabilityPanel() {
  const [report, setReport] = useState<CalibrationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  async function loadCalibration() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getCalibration();
      setReport(data);
    } catch {
      // 404 or missing calibration is normal before initial run
      setReport(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadCalibration();
  }, []);

  async function handleRunCalibration() {
    setRunning(true);
    setActionMessage(null);
    setError(null);
    try {
      const updated = await api.runCalibration();
      setReport(updated);
      setActionMessage("Calibration run completed successfully.");
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Failed to run calibration.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="panel" aria-label="Judge reliability">
      <div className="trace-repeat-heading" style={{ marginBottom: "1rem" }}>
        <div>
          <h2>Judge Reliability & Calibration</h2>
          <p className="muted" style={{ margin: "0.2rem 0" }}>
            Measures judge agreement with human labels (target: ≥ 80% agreement).
          </p>
        </div>
        <button
          type="button"
          className="button secondary"
          disabled={running}
          onClick={handleRunCalibration}
        >
          {running ? "Running Calibration…" : "Run Calibration"}
        </button>
      </div>

      {actionMessage && <p className="status-pill completed" style={{ marginBottom: "1rem" }}>{actionMessage}</p>}
      {error && <p className="error" style={{ marginBottom: "1rem" }}>{error}</p>}

      {loading ? (
        <p className="muted">Loading calibration report…</p>
      ) : !report ? (
        <div className="empty-state">
          <p>No calibration data recorded yet.</p>
          <p className="muted">Click &quot;Run Calibration&quot; to calibrate the LLM judge against labeled human fixtures.</p>
        </div>
      ) : (
        <div>
          <div className="metric-grid" style={{ marginBottom: "1rem" }}>
            <MetricCard
              label="Overall Agreement"
              value={`${report.overall_pass_agreement_pct.toFixed(1)}%`}
              detail={`${report.cases} labeled cases`}
            />
            <MetricCard
              label="Cohen's Kappa (κ)"
              value={report.cohen_kappa !== null ? report.cohen_kappa.toFixed(2) : "n/a"}
              detail={report.cohen_kappa !== null && report.cohen_kappa >= 0.6 ? "Substantial agreement" : "Moderate agreement"}
            />
            <MetricCard
              label="Correctness Agreement"
              value={`${report.correctness_agreement_pct.toFixed(1)}%`}
            />
            <MetricCard
              label="Faithfulness Agreement"
              value={`${report.faithfulness_agreement_pct.toFixed(1)}%`}
            />
          </div>

          <div style={{ display: "flex", gap: "1rem", alignItems: "center", marginBottom: "1rem" }}>
            <span className={`status-pill ${report.meets_target ? "completed" : "danger"}`}>
              {report.meets_target ? "✓ Meets Target (≥ 80%)" : "⚠ Below Target (< 80%)"}
            </span>
            <span className="muted">
              Target threshold: {report.target_agreement_pct.toFixed(0)}%
            </span>
          </div>

          <h3 style={{ marginTop: "1.2rem" }}>Disagreements ({report.disagreements.length})</h3>
          {report.disagreements.length === 0 ? (
            <p className="muted">Zero disagreements between judge and human labels.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Case ID</th>
                    <th>Human Verdict</th>
                    <th>Judge Verdict</th>
                    <th>Human Correct / Faithful</th>
                    <th>Judge Correct / Faithful</th>
                  </tr>
                </thead>
                <tbody>
                  {report.disagreements.map((d) => (
                    <tr key={d.case_id}>
                      <td className="mono">{d.case_id}</td>
                      <td>
                        <span className={`result-status ${d.human_passed ? "pass" : "fail"}`}>
                          {d.human_passed ? "Pass" : "Fail"}
                        </span>
                      </td>
                      <td>
                        <span className={`result-status ${d.judge_passed ? "pass" : "fail"}`}>
                          {d.judge_passed ? "Pass" : "Fail"}
                        </span>
                      </td>
                      <td>
                        {d.human_correct ? "✓" : "✗"} / {d.human_faithful ? "✓" : "✗"}
                      </td>
                      <td>
                        {d.judge_correct ? "✓" : "✗"} / {d.judge_faithful ? "✓" : "✗"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

