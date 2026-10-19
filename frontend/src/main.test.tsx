import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RunsPage } from "./pages/RunsPage";

describe("RunsPage", () => {
  it("shows a loading state while run history is fetched", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));
    render(<RunsPage />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    vi.unstubAllGlobals();
  });
});
