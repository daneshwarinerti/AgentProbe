import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GateEvaluationResult, RunListItem } from "../api";
import { api } from "../api";
import { RegressionGatePage } from "./RegressionGatePage";

const mockRuns: RunListItem[] = [
  {
    id: "candidate-run-1",
    status: "completed",
    suite: "default",
    created_at: "2026-03-01T12:00:00Z",
    summary: {
      cases: 5,
      passed: 5,
      failed: 0,
      pass_rate: 1.0,
      safety: {
        cases: 0,
        attack_cases: 0,
        blocked_attacks: 0,
        attack_block_rate: null,
        benign_cases: 0,
        benign_false_positives: 0,
        false_positive_rate: null,
        pii_leak_cases: 0,
      },
      average_latency_ms: 140.0,
      total_tokens: 1000,
      total_cost_usd: 0.018,
      cost_per_100_cases_usd: 0.36,
      failure_types: {},
    },
  },
  {
    id: "baseline-run-1",
    status: "completed",
    suite: "default",
    created_at: "2026-03-01T10:00:00Z",
    summary: {
      cases: 5,
      passed: 4,
      failed: 1,
      pass_rate: 0.8,
      safety: {
        cases: 0,
        attack_cases: 0,
        blocked_attacks: 0,
        attack_block_rate: null,
        benign_cases: 0,
        benign_false_positives: 0,
        false_positive_rate: null,
        pii_leak_cases: 0,
      },
      average_latency_ms: 150.0,
      total_tokens: 1000,
      total_cost_usd: 0.02,
      cost_per_100_cases_usd: 0.40,
      failure_types: {},
    },
  },
];

const mockGate: GateEvaluationResult = {
  passed: true,
  verdict: "pass",
  reasons: [],
  baseline_run_id: "baseline-run-1",
  candidate_run_id: "candidate-run-1",
  suite: "default",
  target_id: "target-1",
  pass_rate_threshold: 0.9,
  latency_budget: 0.2,
  cost_budget: 0.2,
  baseline_pass_rate: 0.8,
  candidate_pass_rate: 1.0,
  pass_rate_delta: 0.2,
  baseline_average_latency_ms: 150.0,
  candidate_average_latency_ms: 140.0,
  latency_delta_pct: -0.067,
  baseline_total_cost_usd: 0.02,
  candidate_total_cost_usd: 0.018,
  cost_delta_pct: -0.1,
  summary_counts: {
    regressed: 0,
    fixed: 1,
    persistent_failure: 0,
    passing: 4,
  },
  per_agent_regressions: {},
  cases: [
    {
      case_id: "case-1",
      bucket: "passing",
      baseline_passed: true,
      candidate_passed: true,
      first_failing_agent: null,
      agents_involved: ["triage_agent"],
      failure_types: [],
      safety_violations: [],
      baseline_output: "out1",
      candidate_output: "out1",
      baseline_error: null,
      candidate_error: null,
      baseline_latency_ms: 100,
      candidate_latency_ms: 95,
      baseline_cost_usd: 0.001,
      candidate_cost_usd: 0.001,
      baseline_steps: [],
      candidate_steps: [],
    },
  ],
};

describe("RegressionGatePage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders page heading and controls", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ items: mockRuns, limit: 20, offset: 0, total: mockRuns.length });
    vi.spyOn(api, "checkGate").mockResolvedValue(mockGate);

    render(
      <MemoryRouter>
        <RegressionGatePage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "Regression Gate" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Candidate Run")).toBeInTheDocument();
    expect(screen.getByLabelText("Baseline Run")).toBeInTheDocument();
  });

  it("fetches gate evaluation and renders gate results", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ items: mockRuns, limit: 20, offset: 0, total: mockRuns.length });
    const checkGateSpy = vi.spyOn(api, "checkGate").mockResolvedValue(mockGate);

    render(
      <MemoryRouter initialEntries={["/gate?run_id=candidate-run-1"]}>
        <RegressionGatePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(checkGateSpy).toHaveBeenCalledWith("candidate-run-1", undefined, {
        passRateThreshold: 0.9,
      });
    });

    expect(await screen.findByText("GATE PASSED")).toBeInTheDocument();
  });

  it("handles no baseline found error by showing promotion callout", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ items: [mockRuns[0]], limit: 20, offset: 0, total: 1 });
    vi.spyOn(api, "checkGate").mockRejectedValue(new Error("No approved baseline exists (404)"));

    render(
      <MemoryRouter initialEntries={["/gate?run_id=candidate-run-1"]}>
        <RegressionGatePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("No Approved Baseline Found")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Promote Run .* as Baseline/i }),
    ).toBeInTheDocument();
  });

  it("re-evaluates gate when clicking Re-evaluate button with changed threshold", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ items: mockRuns, limit: 20, offset: 0, total: mockRuns.length });
    const checkGateSpy = vi.spyOn(api, "checkGate").mockResolvedValue(mockGate);

    render(
      <MemoryRouter initialEntries={["/gate?run_id=candidate-run-1"]}>
        <RegressionGatePage />
      </MemoryRouter>,
    );

    await screen.findByText("GATE PASSED");

    const thresholdInput = screen.getByLabelText(/Pass Rate Threshold/i);
    fireEvent.change(thresholdInput, { target: { value: "0.95" } });

    const reEvaluateBtn = screen.getByRole("button", { name: /Re-evaluate Gate/i });
    fireEvent.click(reEvaluateBtn);

    await waitFor(() => {
      expect(checkGateSpy).toHaveBeenCalledWith("candidate-run-1", undefined, {
        passRateThreshold: 0.95,
      });
    });
  });
});
