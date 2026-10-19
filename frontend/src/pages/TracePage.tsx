import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { TraceResponse } from "../api";
import { api } from "../api";
import { ErrorState, LoadingState } from "../components/MetricCard";
import { TraceTimeline } from "../components/TraceTimeline";

export function TracePage() {
  const { runId = "", caseId = "" } = useParams();
  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.getTrace(runId, caseId)
      .then((result) => { if (active) setTrace(result); })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "Unable to load trace.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [runId, caseId]);

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;
  if (!trace) return <ErrorState message="Trace was not found." />;

  return (
    <main className="page">
      <p>
        <Link className="text-link" to={`/runs/${encodeURIComponent(runId)}`}>← Run details</Link>
      </p>
      <section className="page-heading">
        <div>
          <p className="eyebrow">Trace viewer</p>
          <h1>{trace.case_id}</h1>
          <p className="muted mono">Run {trace.run_id}</p>
        </div>
      </section>
      {trace.repeats.map((repeat) => (
        <section className="panel trace-repeat" key={repeat.execution.repeat_index}>
          <div className="trace-repeat-heading">
            <h2>Repeat {repeat.execution.repeat_index + 1}</h2>
            <span className={`result-status ${repeat.score.passed && repeat.safety.passed ? "pass" : "fail"}`}>
              {repeat.score.passed && repeat.safety.passed ? "Pass" : "Fail"}
            </span>
          </div>
          {repeat.execution.output && (
            <div className="answer-box"><strong>Final answer</strong><p>{repeat.execution.output}</p></div>
          )}
          {repeat.execution.error && <p role="alert" className="error">{repeat.execution.error}</p>}
          <div className="trace-verdicts">
            <p>
              Rule score: <strong>{repeat.score.passed ? "Pass" : "Fail"}</strong>
              {repeat.score.failure_categories.length > 0 &&
                ` · ${repeat.score.failure_categories.join(", ")}`}
            </p>
            <p>
              Safety: <strong>{repeat.safety.passed ? "Pass" : "Fail"}</strong>
              {repeat.safety.pii_leaks.length > 0 &&
                ` · PII detected: ${repeat.safety.pii_leaks.join(", ")}`}
            </p>
          </div>
          <TraceTimeline steps={repeat.execution.steps} />
        </section>
      ))}
    </main>
  );
}
