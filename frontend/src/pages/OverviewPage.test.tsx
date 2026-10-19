import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CalibrationReport, RunDetail, RunListItem, SuiteCaseStats, TargetResponse } from "../api";
import { api } from "../api";
import { OverviewPage } from "./OverviewPage";

const mockRuns: RunListItem[] = [
  {
    id: "run-abc-123",
    status: "completed",
    suite: "core-suite",
    created_at: "2026-03-01T12:00:00Z",
    summary: {
      cases: 10,
      passed: 9,
      failed: 1,
      pass_rate: 0.9,
      safety: {
        cases: 2,
        attack_cases: 1,
        blocked_attacks: 1,
        attack_block_rate: 1.0,
        benign_cases: 1,
        benign_false_positives: 0,
        false_positive_rate: 0.0,
        pii_leak_cases: 0,
      },
      average_latency_ms: 120,
      total_tokens: 1500,
      total_cost_usd: 0.005,
      cost_per_100_cases_usd: 0.05,
      failure_types: { tool_misuse: 1 },
      per_agent: {
        triage_agent: {
          cases_involved: 10,
          passed: 10,
          failed: 0,
          pass_rate: 1.0,
          failure_types: {},
          average_latency_ms: 50.0,
          total_tokens: 500,
          total_cost_usd: 0.002,
          first_failing_cases: 0,
        },
        billing_agent: {
          cases_involved: 5,
          passed: 4,
          failed: 1,
          pass_rate: 0.8,
          failure_types: { tool_misuse: 1 },
          average_latency_ms: 100.0,
          total_tokens: 1000,
          total_cost_usd: 0.003,
          first_failing_cases: 1,
        },
      },
    },
  },
];

const mockTargets: TargetResponse[] = [
  {
    id: "target-1",
    name: "Customer Bot",
    entry_point: "bot:run",
    setup_status: "ready",
    agent_names: ["billing_agent", "triage_agent"],
    frameworks: ["langgraph"],
    created_at: "2026-03-01T10:00:00Z",
  },
  {
    id: "target-2",
    name: "Legacy Search",
    entry_point: "search:run",
    setup_status: "ready",
    agent_names: [],
    frameworks: [],
    created_at: "2026-03-01T11:00:00Z",
  },
];

const mockSuiteStats: SuiteCaseStats = {
  total: 25,
  approved: 20,
  draft: 5,
  categories: { safety: 10, task: 15 },
  agents: { triage_agent: 15, billing_agent: 10 },
};

const mockCalibration: CalibrationReport = {
  cases: 12,
  overall_pass_agreement_pct: 91.6,
  cohen_kappa: 0.85,
  correctness_agreement_pct: 90.0,
  faithfulness_agreement_pct: 90.0,
  meets_target: true,
  target_agreement_pct: 80.0,
  disagreements: [],
};

const mockRunDetail: RunDetail = {
  id: "run-abc-123",
  status: "completed",
  suite: "core-suite",
  created_at: "2026-03-01T12:00:00Z",
  summary: mockRuns[0].summary,
};

describe("OverviewPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows loading state initially", () => {
    vi.spyOn(api, "getRuns").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "getTargets").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "getSuiteStats").mockReturnValue(new Promise(() => {}));

    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });

  it("renders health metric cards and target filter", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ items: mockRuns, limit: 20, offset: 0, total: mockRuns.length });
    vi.spyOn(api, "getTargets").mockResolvedValue({ items: mockTargets });
    vi.spyOn(api, "getSuiteStats").mockResolvedValue(mockSuiteStats);
    vi.spyOn(api, "getCalibration").mockResolvedValue(mockCalibration);
    vi.spyOn(api, "getRun").mockResolvedValue(mockRunDetail);

    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Registered Targets")).toBeInTheDocument();
    expect(screen.getByText("Test Suite Cases")).toBeInTheDocument();
    expect(screen.getByText("Latest Run Pass Rate")).toBeInTheDocument();
    expect(screen.getByText("Judge Agreement")).toBeInTheDocument();

    // Verify values
    expect(screen.getByText("3")).toBeInTheDocument(); // 2 targets + 1 built-in
    expect(screen.getByText("25")).toBeInTheDocument(); // 25 suite cases
    expect(screen.getAllByText("90.0%").length).toBeGreaterThanOrEqual(1); // 9/10 passed = 90%
    expect(screen.getByText("91.6%")).toBeInTheDocument(); // 91.6% agreement
  });

  it("renders Multi-Agent Attribution table with first failing agent attribution", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ items: mockRuns, limit: 20, offset: 0, total: mockRuns.length });
    vi.spyOn(api, "getTargets").mockResolvedValue({ items: mockTargets });
    vi.spyOn(api, "getSuiteStats").mockResolvedValue(mockSuiteStats);
    vi.spyOn(api, "getCalibration").mockResolvedValue(mockCalibration);
    vi.spyOn(api, "getRun").mockResolvedValue(mockRunDetail);

    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Per-Agent Performance & Regression Attribution")).toBeInTheDocument();
    expect(screen.getByText("triage_agent")).toBeInTheDocument();
    expect(screen.getByText("billing_agent")).toBeInTheDocument();
    // billing_agent was the first failing agent in 1 case
    expect(screen.getAllByText("1").length).toBeGreaterThanOrEqual(1);
  });

  it("renders Tracing SDK notice banner when targets lack SDK", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ items: mockRuns, limit: 20, offset: 0, total: mockRuns.length });
    vi.spyOn(api, "getTargets").mockResolvedValue({ items: mockTargets });
    vi.spyOn(api, "getSuiteStats").mockResolvedValue(mockSuiteStats);
    vi.spyOn(api, "getCalibration").mockResolvedValue(mockCalibration);
    vi.spyOn(api, "getRun").mockResolvedValue(mockRunDetail);

    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/per-agent analysis unavailable; add the SDK/i),
    ).toBeInTheDocument();
  });

  it("filters runs and metrics when selecting a target in dropdown", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ items: mockRuns, limit: 20, offset: 0, total: mockRuns.length });
    vi.spyOn(api, "getTargets").mockResolvedValue({ items: mockTargets });
    vi.spyOn(api, "getSuiteStats").mockResolvedValue(mockSuiteStats);
    vi.spyOn(api, "getCalibration").mockResolvedValue(mockCalibration);
    vi.spyOn(api, "getRun").mockResolvedValue(mockRunDetail);

    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    const select = await screen.findByLabelText(/Target Project Filter:/i);
    fireEvent.change(select, { target: { value: "target-1" } });

    expect(select).toHaveValue("target-1");
  });
});
