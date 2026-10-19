interface MetricCardProps {
  label: string;
  value: string;
  detail?: string;
}

export function MetricCard({ label, value, detail }: MetricCardProps) {
  return (
    <article className="metric-card">
      <p className="metric-label">{label}</p>
      <p className="metric-value">{value}</p>
      {detail && <p className="muted">{detail}</p>}
    </article>
  );
}

export function LoadingState({ message }: { message?: string } = {}) {
  return <p role="status" className="state-message">{message ?? "Loading..."}</p>;
}

export function ErrorState({ message }: { message: string }) {
  return <p role="alert" className="state-message error">{message}</p>;
}
