import { useState } from "react";
import { BrowserRouter, Link, NavLink, Route, Routes } from "react-router-dom";
import { OverviewPage } from "./pages/OverviewPage";
import { RegressionGatePage } from "./pages/RegressionGatePage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";
import { SuiteBuilderPage } from "./pages/SuiteBuilderPage";
import { TargetsPage } from "./pages/TargetsPage";
import { TracePage } from "./pages/TracePage";

export function App() {
  const [apiKey, setApiKey] = useState(() => {
    try {
      return (
        localStorage.getItem("agentprobe_api_key") ||
        sessionStorage.getItem("agentprobe_api_key") ||
        ""
      );
    } catch {
      return "";
    }
  });

  function updateApiKey(val: string) {
    const clean = val.trim();
    setApiKey(clean);
    try {
      if (clean) {
        localStorage.setItem("agentprobe_api_key", clean);
        sessionStorage.setItem("agentprobe_api_key", clean);
      } else {
        localStorage.removeItem("agentprobe_api_key");
        sessionStorage.removeItem("agentprobe_api_key");
      }
    } catch {
      // Ignore storage errors in restricted contexts
    }
  }

  return (
    <BrowserRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <div className="app-shell">
        <header className="topbar">
          <Link className="brand" to="/">AgentProbe</Link>
          <nav aria-label="Main navigation" className="topnav">
            <NavLink to="/" end>
              Overview
            </NavLink>
            <NavLink to="/targets">
              Targets
            </NavLink>
            <NavLink to="/suites">
              Test Suites
            </NavLink>
            <NavLink to="/runs">
              Runner
            </NavLink>
            <NavLink to="/gate">
              Regression Gate
            </NavLink>
          </nav>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
            <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>🔑 API Key:</span>
            <input
              type="password"
              placeholder="Paste AGENTPROBE_API_KEY"
              value={apiKey}
              onChange={(e) => updateApiKey(e.target.value)}
              title="AgentProbe Server API Key (from your .env)"
              style={{
                width: "160px",
                padding: "0.25rem 0.5rem",
                fontSize: "0.8rem",
                borderRadius: "4px",
                border: "1px solid var(--panel-border)",
                background: "#0d1420",
                color: "#e5eaf2",
              }}
            />
          </div>
        </header>
        <Routes>
          <Route element={<OverviewPage />} path="/" />
          <Route element={<TargetsPage />} path="/targets" />
          <Route element={<SuiteBuilderPage />} path="/suites" />
          <Route element={<RunsPage />} path="/runs" />
          <Route element={<RegressionGatePage />} path="/gate" />
          <Route element={<RunDetailPage />} path="/runs/:runId" />
          <Route element={<TracePage />} path="/runs/:runId/cases/:caseId" />
          <Route
            element={
              <main className="page">
                <h1>Page not found</h1>
                <Link className="text-link" to="/">
                  Back to Overview
                </Link>
              </main>
            }
            path="*"
          />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
