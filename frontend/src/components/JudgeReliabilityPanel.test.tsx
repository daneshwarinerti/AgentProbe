import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CalibrationReport } from "../api";
import { api } from "../api";
import { JudgeReliabilityPanel } from "./JudgeReliabilityPanel";

const mockReport: CalibrationReport = {
  cases: 20,
  overall_pass_agreement_pct: 85.0,
  cohen_kappa: 0.7,
  correctness_agreement_pct: 90.0,
  faithfulness_agreement_pct: 85.0,
  meets_target: true,
  target_agreement_pct: 80.0,
  disagreements: [
    {
      case_id: "calibration-wrong-order-status",
      human_correct: false,
      human_faithful: false,
      judge_correct: true,
      judge_faithful: false,
      human_passed: false,
      judge_passed: true,
    },
  ],
};

describe("JudgeReliabilityPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders calibration metrics and disagreements when data exists", async () => {
    vi.spyOn(api, "getCalibration").mockResolvedValue(mockReport);

    render(<JudgeReliabilityPanel />);

    expect(screen.getByText("Loading calibration report…")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("85.0%").length).toBeGreaterThanOrEqual(1);
    });

    expect(screen.getByText("0.70")).toBeInTheDocument();
    expect(screen.getByText("✓ Meets Target (≥ 80%)")).toBeInTheDocument();
    expect(screen.getByText("Disagreements (1)")).toBeInTheDocument();
    expect(screen.getByText("calibration-wrong-order-status")).toBeInTheDocument();
  });

  it("allows re-running calibration from the panel", async () => {
    vi.spyOn(api, "getCalibration").mockRejectedValue(new Error("404 Not Found"));
    const runSpy = vi.spyOn(api, "runCalibration").mockResolvedValue(mockReport);

    render(<JudgeReliabilityPanel />);

    await waitFor(() => {
      expect(screen.getByText("No calibration data recorded yet.")).toBeInTheDocument();
    });

    const runBtn = screen.getByRole("button", { name: "Run Calibration" });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(screen.getByText("Calibration run completed successfully.")).toBeInTheDocument();
    });

    expect(runSpy).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText("85.0%").length).toBeGreaterThanOrEqual(1);
  });
});
