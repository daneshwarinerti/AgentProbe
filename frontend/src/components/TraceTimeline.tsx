import type { TraceStep } from "../api";

function formatValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value, null, 2);
}

export function TraceTimeline({ steps }: { steps: TraceStep[] }) {
  if (steps.length === 0) {
    return <p className="empty-state">No trace steps were recorded for this repeat.</p>;
  }

  return (
    <ol className="timeline">
      {steps.map((step, index) => (
        <li className="timeline-item" key={`${step.kind}-${index}`}>
          <div className="timeline-heading">
            <span className={`step-kind ${step.kind}`}>{step.kind}</span>
            <strong>{step.tool_name ?? step.agent_name ?? `Step ${index + 1}`}</strong>
            <span className="muted">{step.latency_ms.toFixed(1)} ms</span>
          </div>
          <div className="trace-grid">
            {step.args && <TraceValue label="Arguments" value={step.args} />}
            {step.prompt && <TraceValue label="Prompt" value={step.prompt} />}
            {step.response !== null && <TraceValue label="Response" value={step.response} />}
            {step.result !== null && <TraceValue label="Result" value={step.result} />}
            {step.error && <TraceValue label="Error" value={step.error} />}
          </div>
          <p className="muted trace-meta">
            Tokens: {step.input_tokens ?? "—"} in / {step.output_tokens ?? "—"} out
            {" · "}Cost: {formatCost(step.cost_usd)}
          </p>
        </li>
      ))}
    </ol>
  );
}

function TraceValue({ label, value }: { label: string; value: unknown }) {
  return (
    <details className="trace-value">
      <summary>{label}</summary>
      <pre>{formatValue(value)}</pre>
    </details>
  );
}

function formatCost(value: number | null): string {
  return value === null ? "n/a" : `$${value.toFixed(6)}`;
}
