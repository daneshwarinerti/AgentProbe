import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SuiteBuilderPage } from "./SuiteBuilderPage";

describe("SuiteBuilderPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state while suites are fetched", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));

    render(<SuiteBuilderPage />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });

  it("submits a case with its expected input and saves it through the API", async () => {
    let savedCase: Record<string, unknown> | null = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/suite-cases/stats")) {
        return Response.json({ total: 0, approved: 0, draft: 0, categories: {}, agents: {} });
      }
      if (url.includes("/suite-packs")) return Response.json({ items: [] });
      if (init?.method === "POST" && url.endsWith("/suite-cases")) {
        savedCase = JSON.parse(String(init.body)) as Record<string, unknown>;
        return Response.json({
          id: "ui-case",
          case: savedCase.case,
          target_id: null,
          status: "approved",
          provenance: "manual",
          human_reviewed: true,
          created_at: "2026-01-01T00:00:00Z",
        });
      }
      if (url.includes("/suite-cases")) {
        return Response.json({
          items: savedCase
            ? [{
                id: "ui-case",
                case: savedCase.case,
                target_id: null,
                status: "approved",
                provenance: "manual",
                human_reviewed: true,
                created_at: "2026-01-01T00:00:00Z",
              }]
            : [],
          total: savedCase ? 1 : 0,
        });
      }
      return Response.json({ error: { message: "Unexpected request" } }, { status: 500 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SuiteBuilderPage />);

    fireEvent.change(await screen.findByLabelText("Case id"), { target: { value: "ui-case" } });
    fireEvent.change(screen.getByLabelText("Input"), { target: { value: "say hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Create case" }));

    await waitFor(() => expect(savedCase).not.toBeNull());
    expect(savedCase).toMatchObject({
      case: { id: "ui-case", input: "say hello", category: "general" },
    });
  });
});
