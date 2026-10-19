import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TraceTimeline } from "./TraceTimeline";

describe("TraceTimeline", () => {
  it("shows model and tool events with measured usage", () => {
    render(
      <TraceTimeline
        steps={[
          {
            kind: "llm",
            agent_name: "support",
            handoff_to: null,
            error: null,
            prompt: [{ role: "user", content: "Check my order" }],
            response: { tool_calls: ["get_order_status"] },
            tool_name: null,
            args: null,
            result: null,
            input_tokens: 20,
            output_tokens: 4,
            latency_ms: 30.5,
            cost_usd: null,
          },
          {
            kind: "tool",
            agent_name: "support",
            handoff_to: null,
            error: null,
            prompt: null,
            response: null,
            tool_name: "get_order_status",
            args: { order_id: "ORD-1001" },
            result: { status: "shipped" },
            input_tokens: null,
            output_tokens: null,
            latency_ms: 4.2,
            cost_usd: null,
          },
        ]}
      />,
    );

    expect(screen.getByText("llm")).toBeInTheDocument();
    expect(screen.getByText("get_order_status")).toBeInTheDocument();
    expect(screen.getByText(/20 in \/ 4 out/)).toBeInTheDocument();
    expect(screen.queryByText("No trace steps were recorded for this repeat.")).not.toBeInTheDocument();
  });
});
