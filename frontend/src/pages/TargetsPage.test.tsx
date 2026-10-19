import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TargetResponse } from "../api";
import { api } from "../api";
import { TargetsPage } from "./TargetsPage";

const mockTargets: TargetResponse[] = [
  {
    id: "t-1",
    name: "Customer Support Agent",
    entry_point: "customer_bot.main:run_agent",
    setup_status: "ready",
    agent_names: ["triage_agent", "refund_agent"],
    frameworks: ["langgraph"],
    created_at: "2026-03-01T10:00:00Z",
  },
  {
    id: "t-2",
    name: "Legacy Search Agent",
    entry_point: "search.agent:entry",
    setup_status: "ready",
    agent_names: [],
    frameworks: [],
    created_at: "2026-03-02T10:00:00Z",
  },
];

describe("TargetsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows loading state while fetching targets", () => {
    vi.spyOn(api, "getTargets").mockReturnValue(new Promise(() => {}));
    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });

  it("renders target list and displays agents and status", async () => {
    vi.spyOn(api, "getTargets").mockResolvedValue({ items: mockTargets });

    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Customer Support Agent")).toBeInTheDocument();
    expect(screen.getByText("Legacy Search Agent")).toBeInTheDocument();
    expect(screen.getByText("triage_agent, refund_agent")).toBeInTheDocument();
    expect(screen.getByText("None detected")).toBeInTheDocument();
  });

  it("opens upload wizard modal when clicking Upload Target", async () => {
    vi.spyOn(api, "getTargets").mockResolvedValue({ items: mockTargets });

    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    );

    await screen.findByText("Customer Support Agent");
    fireEvent.click(screen.getByRole("button", { name: /\+ Upload Project ZIP/i }));

    expect(screen.getByText(/Project Archive \(\.zip/i)).toBeInTheDocument();
    expect(screen.getByText("Target Display Name")).toBeInTheDocument();
    expect(screen.getByText(/Entry Point \(package\.module:function\)/i)).toBeInTheDocument();
  });

  it("opens inspect modal when clicking Inspect", async () => {
    vi.spyOn(api, "getTargets").mockResolvedValue({ items: mockTargets });
    vi.spyOn(api, "inspectTarget").mockResolvedValue({
      target_id: "t-1",
      frameworks: ["langgraph"],
      candidates: [
        {
          name: "triage_agent",
          confidence: "high",
          framework: "langgraph",
          evidence: ["StateGraph pattern found"],
        },
      ],
      agent_names: ["triage_agent", "refund_agent"],
      notice: "Heuristic scan complete",
      tracing_sdk_detected: true,
    });

    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    );

    await screen.findByText("Customer Support Agent");
    const inspectButtons = screen.getAllByRole("button", { name: /Inspect/i });
    fireEvent.click(inspectButtons[0]);

    await waitFor(() => {
      expect(screen.getByText(/Inspect:\s*Customer Support Agent/i)).toBeInTheDocument();
    });
  });

  it("opens dry run modal when clicking Dry Run", async () => {
    vi.spyOn(api, "getTargets").mockResolvedValue({ items: mockTargets });

    render(
      <MemoryRouter>
        <TargetsPage />
      </MemoryRouter>,
    );

    await screen.findByText("Customer Support Agent");
    const dryRunButtons = screen.getAllByRole("button", { name: /Dry Run/i });
    fireEvent.click(dryRunButtons[0]);

    expect(screen.getByText(/Dry Run:\s*Customer Support Agent/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Execute Dry Run/i })).toBeInTheDocument();
  });
});
