import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GateEvaluationResult } from "../api";
import { api } from "../api";
import { RegressionGatePanel } from "./RegressionGatePanel";

const mockGatePassed: GateEvaluationResult = {
  passed: true,
  verdict: "pass",
  reasons: [],
  baseline_run_id: "base-123",
  candidate_run_id: "cand-456",
  suite: "safety",
  target_id: "target-1",
  pass_rate_threshold: 0.0,
  latency_budget: 0.2,
  cost_budget: 0.2,
  baseline_pass_rate: 1.0,
  candidate_pass_rate: 1.0,
  pass_rate_delta: 0.0,
  baseline_average_latency_ms: 120.0,
  candidate_average_latency_ms: 125.0,
  latency_delta_pct: 0.041,
  baseline_total_cost_usd: 0.01,
  candidate_total_cost_usd: 0.01,
  cost_delta_pct: 0.0,
  summary_counts: {
    regressed: 0,
    fixed: 0,
    persistent_failure: 0,
    passing: 2,
  },
  per_agent_regressions: {},
  cases: [
    {
      case_id: "case-1",
      bucket: "passing",
      baseline_passed: true,
      candidate_passed: true,
      first_failing_agent: null,
      agents_involved: ["support_agent"],
      failure_types: [],
      safety_violations: [],
      baseline_output: "Success A",
      candidate_output: "Success B",
      baseline_error: null,
      candidate_error: null,
      baseline_latency_ms: 100,
      candidate_latency_ms: 105,
      baseline_cost_usd: 0.005,
      candidate_cost_usd: 0.005,
      baseline_steps: [],
      candidate_steps: [],
    },
  ],
};

const mockGateFailed: GateEvaluationResult = {
  passed: false,
  verdict: "fail",
  reasons: [
    "Safety regression detected: Case 'inj-1': attack unblocked",
    "Pass rate dropped by 25.0%, exceeding allowed threshold of 0.0%.",
  ],
  baseline_run_id: "base-123",
  candidate_run_id: "cand-789",
  suite: "safety",
  target_id: "target-1",
  pass_rate_threshold: 0.0,
  latency_budget: 0.2,
  cost_budget: 0.2,
  baseline_pass_rate: 1.0,
  candidate_pass_rate: 0.75,
  pass_rate_delta: -0.25,
  baseline_average_latency_ms: 100.0,
  candidate_average_latency_ms: 150.0,
  latency_delta_pct: 0.5,
  baseline_total_cost_usd: 0.01,
  candidate_total_cost_usd: 0.02,
  cost_delta_pct: 1.0,
  summary_counts: {
    regressed: 1,
    fixed: 0,
    persistent_failure: 0,
    passing: 1,
  },
  per_agent_regressions: {
    security_bot: {
      agent_name: "security_bot",
      regression_count: 1,
      failure_types: { wrong_tool: 1 },
      summary: "Agent security_bot caused 1 regression(s) (1 wrong_tool).",
    },
  },
  cases: [
    {
      case_id: "inj-1",
      bucket: "regressed",
      baseline_passed: true,
      candidate_passed: false,
      first_failing_agent: "security_bot",
      agents_involved: ["security_bot"],
      failure_types: ["wrong_tool"],
      safety_violations: ["attack unblocked"],
      baseline_output: "Access denied",
      candidate_output: "Secret revealed",
      baseline_error: null,
      candidate_error: "Security violation",
      baseline_latency_ms: 100,
      candidate_latency_ms: 200,
      baseline_cost_usd: 0.005,
      candidate_cost_usd: 0.015,
      baseline_steps: [],
      candidate_steps: [],
    },
    {
      case_id: "case-2",
      bucket: "passing",
      baseline_passed: true,
      candidate_passed: true,
      first_failing_agent: null,
      agents_involved: ["support_agent"],
      failure_types: [],
      safety_violations: [],
      baseline_output: "Help text",
      candidate_output: "Help text",
      baseline_error: null,
      candidate_error: null,
      baseline_latency_ms: 100,
      candidate_latency_ms: 100,
      baseline_cost_usd: 0.005,
      candidate_cost_usd: 0.005,
      baseline_steps: [],
      candidate_steps: [],
    },
  ],
};

describe("RegressionGatePanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders visual PASS badge and metrics when gate passes", () => {
    render(
      <MemoryRouter>
        <RegressionGatePanel gate={mockGatePassed} runStatus="completed" />
      </MemoryRouter>,
    );

    expect(screen.getByText("GATE PASSED")).toBeInTheDocument();
    expect(screen.getByText("Pass Rate Delta")).toBeInTheDocument();
    expect(screen.getByText("case-1")).toBeInTheDocument();
    expect(screen.getByText("Passing")).toBeInTheDocument();
  });

  it("renders visual FAIL badge, reasons, and per-agent regression attribution", () => {
    render(
      <MemoryRouter>
        <RegressionGatePanel gate={mockGateFailed} runStatus="completed" />
      </MemoryRouter>,
    );

    expect(screen.getByText("GATE FAILED")).toBeInTheDocument();
    expect(screen.getByText("Gate Failure Reasons:")).toBeInTheDocument();
    expect(
      screen.getByText("Safety regression detected: Case 'inj-1': attack unblocked"),
    ).toBeInTheDocument();

    // Per-agent attribution
    expect(
      screen.getByText("Agent security_bot caused 1 regression(s) (1 wrong_tool)."),
    ).toBeInTheDocument();

    // Regressed cases first in table
    expect(screen.getByText("inj-1")).toBeInTheDocument();
    expect(screen.getByText("Regressed")).toBeInTheDocument();
    expect(screen.getByText("security_bot")).toBeInTheDocument();
  });

  it("disables Promote to Baseline if run has safety violations", () => {
    render(
      <MemoryRouter>
        <RegressionGatePanel
          gate={mockGateFailed}
          runStatus="completed"
          hasSafetyViolations={true}
        />
      </MemoryRouter>,
    );

    const promoteBtn = screen.getByRole("button", { name: "Promote to Baseline" });
    expect(promoteBtn).toBeDisabled();
    expect(
      screen.getByText("Runs with safety failures cannot be promoted."),
    ).toBeInTheDocument();
  });

  it("opens confirmation modal and promotes baseline on human confirmation", async () => {
    const promoteSpy = vi.spyOn(api, "promoteBaseline").mockResolvedValue({
      run_id: "cand-456",
      status: "promoted",
    });

    render(
      <MemoryRouter>
        <RegressionGatePanel
          gate={mockGatePassed}
          runId="cand-456"
          runStatus="completed"
          hasSafetyViolations={false}
        />
      </MemoryRouter>,
    );

    const promoteBtn = screen.getByRole("button", { name: "Promote to Baseline" });
    expect(promoteBtn).toBeEnabled();

    // Click promote
    fireEvent.click(promoteBtn);

    // Modal appears
    expect(screen.getByText("Confirm Baseline Promotion")).toBeInTheDocument();
    const confirmBtn = screen.getByRole("button", { name: "Confirm Promotion" });

    // Confirm promotion
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(promoteSpy).toHaveBeenCalledWith("cand-456");
      expect(
        screen.getByText("Run cand-456 successfully promoted to approved baseline!"),
      ).toBeInTheDocument();
    });
  });
});

