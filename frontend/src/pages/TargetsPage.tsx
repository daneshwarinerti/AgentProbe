import { useEffect, useState } from "react";
import type { DryRunTargetResponse, InspectTargetResponse, TargetResponse } from "../api";
import { api } from "../api";
import { ErrorState, LoadingState } from "../components/MetricCard";

export function TargetsPage() {
  const [targets, setTargets] = useState<TargetResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Upload wizard state
  const [showUploadWizard, setShowUploadWizard] = useState(false);
  const [targetName, setTargetName] = useState("");
  const [entryPoint, setEntryPoint] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [envVars, setEnvVars] = useState<Array<{ key: string; value: string }>>([]);
  const [inputMode, setInputMode] = useState<"text" | "document">("text");
  const [docField, setDocField] = useState("document_path");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
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

  // Inspect modal state
  const [inspectingTarget, setInspectingTarget] = useState<TargetResponse | null>(null);
  const [inspectionResult, setInspectionResult] = useState<InspectTargetResponse | null>(null);
  const [inspectLoading, setInspectLoading] = useState(false);
  const [inspectError, setInspectError] = useState<string | null>(null);
  const [agentNamesInput, setAgentNamesInput] = useState<string>("");
  const [savingAgents, setSavingAgents] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);

  // Dry run state
  const [dryRunTarget, setDryRunTarget] = useState<TargetResponse | null>(null);
  const [dryRunInput, setDryRunInput] = useState("Hello agent, what can you do?");
  const [dryRunLoading, setDryRunLoading] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<DryRunTargetResponse | null>(null);
  const [dryRunError, setDryRunError] = useState<string | null>(null);

  // Deleting
  const [deletingId, setDeletingId] = useState<string | null>(null);

  async function loadTargets() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getTargets();
      setTargets(res.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load targets.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadTargets();
  }, []);

  function addEnvRow() {
    setEnvVars([...envVars, { key: "", value: "" }]);
  }

  function removeEnvRow(index: number) {
    setEnvVars(envVars.filter((_, i) => i !== index));
  }

  function updateEnvRow(index: number, key: string, value: string) {
    const updated = [...envVars];
    updated[index] = { key, value };
    setEnvVars(updated);
  }

  async function handleUploadSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFile) {
      setUploadError("Please select a project .zip archive.");
      return;
    }
    if (!entryPoint.trim()) {
      setUploadError("Please specify a valid Python entry point (package.module:function).");
      return;
    }

    setUploading(true);
    setUploadError(null);
    if (apiKey.trim()) {
      sessionStorage.setItem("agentprobe_api_key", apiKey.trim());
    }

    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("entry_point", entryPoint.trim());
    if (targetName.trim()) {
      formData.append("name", targetName.trim());
    }

    // Build environment JSON
    const envObj: Record<string, string> = {};
    for (const { key, value } of envVars) {
      if (key.trim()) {
        envObj[key.trim()] = value;
      }
    }
    formData.append("env_json", JSON.stringify(envObj));

    // Input mapping
    const inputMapping =
      inputMode === "text"
        ? { mode: "text" }
        : { mode: "document", document_path_field: docField.trim() || "document_path" };
    formData.append("input_mapping_json", JSON.stringify(inputMapping));

    try {
      await api.uploadTarget(formData);
      setShowUploadWizard(false);
      setTargetName("");
      setEntryPoint("");
      setSelectedFile(null);
      setEnvVars([]);
      await loadTargets();
    } catch (caught) {
      setUploadError(caught instanceof Error ? caught.message : "Target upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function handleInspect(target: TargetResponse) {
    setInspectingTarget(target);
    setInspectionResult(null);
    setInspectLoading(true);
    setInspectError(null);
    setSaveSuccess(null);

    try {
      const res = await api.inspectTarget(target.id);
      setInspectionResult(res);
      setAgentNamesInput(res.agent_names.join(", "));
    } catch (caught) {
      setInspectError(caught instanceof Error ? caught.message : "Inspection failed.");
    } finally {
      setInspectLoading(false);
    }
  }

  async function handleSaveAgentNames() {
    if (!inspectingTarget) return;
    setSavingAgents(true);
    setSaveSuccess(null);

    const names = agentNamesInput
      .split(",")
      .map((n) => n.trim())
      .filter(Boolean);

    try {
      const res = await api.inspectTarget(inspectingTarget.id, names);
      setInspectionResult(res);
      setAgentNamesInput(res.agent_names.join(", "));
      setSaveSuccess("Agent names saved successfully!");
      await loadTargets();
    } catch (caught) {
      setInspectError(caught instanceof Error ? caught.message : "Failed to save agent names.");
    } finally {
      setSavingAgents(false);
    }
  }

  async function handleDryRun(target: TargetResponse) {
    setDryRunTarget(target);
    setDryRunResult(null);
    setDryRunError(null);
    setDryRunLoading(false);
  }

  async function executeDryRun() {
    if (!dryRunTarget) return;
    if (apiKey.trim()) {
      try {
        localStorage.setItem("agentprobe_api_key", apiKey.trim());
        sessionStorage.setItem("agentprobe_api_key", apiKey.trim());
      } catch {}
    }
    setDryRunLoading(true);
    setDryRunError(null);
    setDryRunResult(null);

    try {
      let inputPayload: string | Record<string, unknown> = dryRunInput;
      try {
        const parsed = JSON.parse(dryRunInput);
        if (typeof parsed === "object" && parsed !== null) {
          inputPayload = parsed;
        }
      } catch {
        // use string input directly
      }

      const res = await api.dryRunTarget(dryRunTarget.id, inputPayload);
      setDryRunResult(res);
    } catch (caught) {
      setDryRunError(caught instanceof Error ? caught.message : "Dry run failed.");
    } finally {
      setDryRunLoading(false);
    }
  }

  async function handleDelete(targetId: string) {
    if (!confirm("Are you sure you want to delete this target? Uploaded files and target-specific test cases will be removed.")) {
      return;
    }
    setDeletingId(targetId);
    try {
      await api.deleteTarget(targetId);
      await loadTargets();
    } catch (caught) {
      alert(caught instanceof Error ? caught.message : "Failed to delete target.");
    } finally {
      setDeletingId(null);
    }
  }

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;

  return (
    <main className="page">
      <section className="page-heading">
        <div>
          <p className="eyebrow">Module 1 · Foundation</p>
          <h1>Target Projects</h1>
          <p className="muted">
            Upload custom agent projects (.zip), inspect detected agents and frameworks, and verify execution with dry-runs.
          </p>
        </div>
        <button
          className="button"
          onClick={() => setShowUploadWizard(!showUploadWizard)}
          type="button"
        >
          {showUploadWizard ? "Cancel Upload" : "+ Upload Project ZIP"}
        </button>
      </section>

      {/* Upload Wizard Form */}
      {showUploadWizard && (
        <section className="panel" aria-label="Upload Project Wizard">
          <h2>Upload Target Project</h2>
          <div className="sandbox-notice">
            <span style={{ fontWeight: 700 }}>Security Note:</span> Uploaded code and dependency installers run with your local user permissions; subprocess isolation is not a security sandbox. Upload only code you trust.
          </div>

          <form className="suite-form" onSubmit={(e) => void handleUploadSubmit(e)} style={{ marginTop: "1rem" }}>
            <div className="form-row">
              <label>
                Project Archive (.zip, max 100 MB) *
                <input
                  type="file"
                  accept=".zip"
                  required
                  onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                />
              </label>

              <label>
                Target Display Name
                <input
                  type="text"
                  placeholder="e.g. Customer Support Bot"
                  value={targetName}
                  onChange={(e) => setTargetName(e.target.value)}
                />
              </label>
            </div>

            <label>
              Entry Point (package.module:function) *
              <input
                type="text"
                placeholder="e.g. my_agent.main:run_agent or app:agent"
                required
                value={entryPoint}
                onChange={(e) => setEntryPoint(e.target.value)}
              />
              <span className="muted" style={{ fontSize: "0.78rem" }}>
                Must be an importable function accepting input and returning output.
              </span>
            </label>

            <div className="form-row">
              <label>
                Input Mapping Mode
                <select
                  value={inputMode}
                  onChange={(e) => setInputMode(e.target.value as "text" | "document")}
                >
                  <option value="text">Text input (direct string parameter)</option>
                  <option value="document">Document path (file path parameter)</option>
                </select>
              </label>
              {inputMode === "document" && (
                <label>
                  Document Path Field Name
                  <input
                    type="text"
                    value={docField}
                    onChange={(e) => setDocField(e.target.value)}
                  />
                </label>
              )}
            </div>

            {/* Environment Variables for Target */}
            <div style={{ marginTop: "0.5rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontWeight: 650, fontSize: "0.9rem" }}>Target Environment Variables (Optional)</span>
                <button className="button secondary" onClick={addEnvRow} type="button" style={{ padding: "0.3rem 0.6rem", fontSize: "0.8rem" }}>
                  + Add Target Env Var
                </button>
              </div>
              <p className="muted" style={{ fontSize: "0.82rem", margin: "0.3rem 0 0.5rem 0" }}>
                Pass secrets directly into the uploaded target agent's subprocess (e.g. <code>OPENAI_API_KEY</code>, database passwords). Leave empty if not required.
              </p>

              {envVars.length > 0 && (
                <div style={{ display: "grid", gap: "0.5rem", marginTop: "0.5rem" }}>
                  {envVars.map((row, idx) => (
                    <div key={idx} style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                      <input
                        type="text"
                        placeholder="NAME (e.g. OPENAI_API_KEY)"
                        value={row.key}
                        onChange={(e) => updateEnvRow(idx, e.target.value, row.value)}
                        style={{ flex: 1 }}
                      />
                      <input
                        type="password"
                        placeholder="VALUE"
                        value={row.value}
                        onChange={(e) => updateEnvRow(idx, row.key, e.target.value)}
                        style={{ flex: 1 }}
                      />
                      <button
                        className="button danger"
                        onClick={() => removeEnvRow(idx)}
                        type="button"
                        style={{ padding: "0.4rem 0.7rem" }}
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* AgentProbe Server Write API Key */}
            <div style={{ marginTop: "1rem" }}>
              <label style={{ display: "block" }}>
                <span style={{ fontWeight: 650, fontSize: "0.9rem" }}>AgentProbe Local API Key</span>
                <span className="muted" style={{ display: "block", fontSize: "0.82rem", margin: "0.2rem 0 0.4rem 0" }}>
                  Required if write authorization is enabled on the backend (matches <code>AGENTPROBE_API_KEY</code> in your <code>.env</code>).
                </span>
                <input
                  type="password"
                  placeholder="Paste AGENTPROBE_API_KEY from .env"
                  value={apiKey}
                  onChange={(e) => {
                    setApiKey(e.target.value);
                    sessionStorage.setItem("agentprobe_api_key", e.target.value.trim());
                  }}
                />
              </label>
            </div>

            {uploadError && <p className="error">{uploadError}</p>}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.8rem", marginTop: "1rem" }}>
              <button
                className="button secondary"
                onClick={() => setShowUploadWizard(false)}
                type="button"
                disabled={uploading}
              >
                Cancel
              </button>
              <button className="button" type="submit" disabled={uploading}>
                {uploading ? "Extracting & Setting up…" : "Upload & Initialize"}
              </button>
            </div>
          </form>
        </section>
      )}

      {/* Target Projects Table */}
      <section className="panel">
        <h2>Registered Targets</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Target Name</th>
                <th>Entry Point</th>
                <th>Setup Status</th>
                <th>Agents</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {/* Built-in Sample Agent */}
              <tr style={{ background: "rgba(101, 214, 190, 0.05)" }}>
                <td>
                  <strong>Built-in LangGraph Agent</strong>{" "}
                  <span className="status-pill completed" style={{ fontSize: "0.72rem" }}>Built-in</span>
                </td>
                <td className="mono">backend.sample_agent:agent</td>
                <td><span className="status-pill completed">ready</span></td>
                <td>
                  <span className="mono" style={{ fontSize: "0.8rem" }}>researcher, coder</span>
                </td>
                <td><span className="muted">—</span></td>
                <td>
                  <span className="muted">Default Sample</span>
                </td>
              </tr>

              {targets.map((t) => (
                <tr key={t.id}>
                  <td>
                    <strong>{t.name}</strong>
                    <p className="muted mono" style={{ fontSize: "0.75rem", margin: 0 }}>{t.id}</p>
                  </td>
                  <td className="mono">{t.entry_point}</td>
                  <td>
                    <span className={`status-pill ${t.setup_status}`}>{t.setup_status}</span>
                  </td>
                  <td>
                    {t.agent_names && t.agent_names.length > 0 ? (
                      <span className="mono" style={{ fontSize: "0.8rem" }}>
                        {t.agent_names.join(", ")}
                      </span>
                    ) : (
                      <span className="muted">None detected</span>
                    )}
                  </td>
                  <td>{new Date(t.created_at).toLocaleDateString()}</td>
                  <td>
                    <div style={{ display: "flex", gap: "0.4rem" }}>
                      <button
                        className="button secondary"
                        onClick={() => void handleInspect(t)}
                        style={{ padding: "0.25rem 0.55rem", fontSize: "0.8rem" }}
                        type="button"
                      >
                        Inspect
                      </button>
                      <button
                        className="button secondary"
                        onClick={() => void handleDryRun(t)}
                        style={{ padding: "0.25rem 0.55rem", fontSize: "0.8rem" }}
                        type="button"
                      >
                        Dry Run
                      </button>
                      <button
                        className="button danger"
                        onClick={() => void handleDelete(t.id)}
                        disabled={deletingId === t.id}
                        style={{ padding: "0.25rem 0.55rem", fontSize: "0.8rem" }}
                        type="button"
                      >
                        {deletingId === t.id ? "…" : "Delete"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Inspect Modal */}
      {inspectingTarget && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal-dialog" style={{ maxWidth: "600px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3>Inspect: {inspectingTarget.name}</h3>
              <button
                className="button secondary"
                onClick={() => setInspectingTarget(null)}
                style={{ padding: "0.2rem 0.5rem" }}
                type="button"
              >
                ✕
              </button>
            </div>

            {inspectLoading && <p className="state-message">Inspecting project files heuristically…</p>}
            {inspectError && <p className="error">{inspectError}</p>}

            {inspectionResult && (
              <div style={{ marginTop: "1rem" }}>
                <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.8rem" }}>
                  <span className="status-pill" style={{ background: "#1c2b3e" }}>
                    Frameworks: {(inspectionResult.frameworks ?? []).length > 0 ? inspectionResult.frameworks.join(", ") : "None detected"}
                  </span>
                  <span
                    className={`status-pill ${
                      inspectionResult.tracing_sdk_detected ? "completed" : "danger"
                    }`}
                  >
                    {inspectionResult.tracing_sdk_detected
                      ? "Tracing SDK: Detected"
                      : "Tracing SDK: Not Detected"}
                  </span>
                </div>

                {!inspectionResult.tracing_sdk_detected && (
                  <div className="sdk-notice-banner">
                    ⚠️ <strong>Notice:</strong> per-agent analysis unavailable; add the AgentProbe SDK (agentprobe_sdk) to enable per-agent tracing and attribution.
                  </div>
                )}

                <p className="muted" style={{ fontSize: "0.85rem", margin: "0.5rem 0" }}>
                  {inspectionResult.notice}
                </p>

                {/* Candidate Agents */}
                <h4 style={{ margin: "1rem 0 0.4rem" }}>Candidate Agents Detected</h4>
                {(inspectionResult.candidates ?? []).length === 0 ? (
                  <p className="empty-state">No agent patterns detected automatically.</p>
                ) : (
                  <ul className="pack-list">
                    {(inspectionResult.candidates ?? []).map((c, idx) => (
                      <li key={idx} style={{ gridTemplateColumns: "1fr 1fr auto" }}>
                        <div>
                          <strong>{c.name}</strong> <span className="muted">({c.framework})</span>
                        </div>
                        <span className="muted" style={{ fontSize: "0.8rem" }}>
                          {(c.evidence ?? []).join("; ")}
                        </span>
                        <span
                          className={`bucket-pill ${
                            c.confidence === "high"
                              ? "fixed"
                              : c.confidence === "medium"
                              ? "persistent_failure"
                              : "regressed"
                          }`}
                        >
                          {c.confidence} confidence
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                {/* Editable Agent Names */}
                <div style={{ marginTop: "1.2rem" }}>
                  <label>
                    Active Agent Names (comma-separated):
                    <input
                      type="text"
                      value={agentNamesInput}
                      onChange={(e) => setAgentNamesInput(e.target.value)}
                      placeholder="e.g. researcher, coder, reviewer"
                    />
                  </label>
                  <p className="muted" style={{ fontSize: "0.78rem" }}>
                    You can edit, add, or remove candidate agent names manually.
                  </p>
                </div>

                {saveSuccess && <p className="result-status pass" style={{ marginTop: "0.5rem" }}>{saveSuccess}</p>}

                <div className="modal-actions">
                  <button
                    className="button secondary"
                    onClick={() => setInspectingTarget(null)}
                    type="button"
                  >
                    Close
                  </button>
                  <button
                    className="button"
                    onClick={() => void handleSaveAgentNames()}
                    disabled={savingAgents}
                    type="button"
                  >
                    {savingAgents ? "Saving…" : "Save Agents"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Dry Run Modal */}
      {dryRunTarget && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal-dialog" style={{ maxWidth: "600px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3>Dry Run: {dryRunTarget.name}</h3>
              <button
                className="button secondary"
                onClick={() => setDryRunTarget(null)}
                style={{ padding: "0.2rem 0.5rem" }}
                type="button"
              >
                ✕
              </button>
            </div>

            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Executes a sample invocation in the target's dedicated isolated virtual environment.
            </p>

            <label style={{ marginTop: "0.8rem" }}>
              Sample Input:
              <textarea
                value={dryRunInput}
                onChange={(e) => setDryRunInput(e.target.value)}
                rows={3}
              />
            </label>

            {!apiKey && (
              <label style={{ marginTop: "0.8rem", display: "block" }}>
                <span style={{ fontSize: "0.85rem", fontWeight: 650 }}>
                  AgentProbe API Key (from .env):
                </span>
                <input
                  type="password"
                  placeholder="Paste AGENTPROBE_API_KEY"
                  value={apiKey}
                  onChange={(e) => {
                    const clean = e.target.value.trim();
                    setApiKey(clean);
                    try {
                      localStorage.setItem("agentprobe_api_key", clean);
                      sessionStorage.setItem("agentprobe_api_key", clean);
                    } catch {}
                  }}
                  style={{ marginTop: "0.3rem" }}
                />
              </label>
            )}

            {dryRunLoading && <p className="state-message">Running dry run in isolated subprocess…</p>}
            {dryRunError && <p className="error">Execution Error: {dryRunError}</p>}

            {dryRunResult && (
              <div style={{ marginTop: "1rem" }}>
                <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                  <span
                    className={`result-status ${dryRunResult.succeeded ? "pass" : "fail"}`}
                  >
                    {dryRunResult.succeeded ? "Dry Run Succeeded" : "Dry Run Failed"}
                  </span>
                  <span className="muted" style={{ fontSize: "0.8rem" }}>
                    Tracing SDK: {dryRunResult.trace_available ? "Active" : "Not used"}
                  </span>
                </div>

                {dryRunResult.notice && (
                  <p className="muted" style={{ fontSize: "0.8rem", margin: "0.5rem 0" }}>
                    {dryRunResult.notice}
                  </p>
                )}

                <div style={{ marginTop: "0.8rem" }}>
                  <span style={{ fontWeight: 650, fontSize: "0.85rem" }}>Final Answer / Output:</span>
                  <pre
                    style={{
                      background: "#101827",
                      padding: "0.8rem",
                      borderRadius: "6px",
                      fontSize: "0.85rem",
                      maxHeight: "180px",
                      overflow: "auto",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {dryRunResult.final_answer || "(empty output)"}
                  </pre>
                </div>

                {dryRunResult.error && (
                  <div style={{ marginTop: "0.8rem" }}>
                    <span style={{ fontWeight: 650, fontSize: "0.85rem", color: "#ff8888" }}>
                      Error Details:
                    </span>
                    <pre
                      style={{
                        background: "#2b1419",
                        color: "#ffaaaa",
                        padding: "0.8rem",
                        borderRadius: "6px",
                        fontSize: "0.8rem",
                        maxHeight: "140px",
                        overflow: "auto",
                        whiteSpace: "pre-wrap",
                      }}
                    >
                      {dryRunResult.error}
                    </pre>
                  </div>
                )}
              </div>
            )}

            <div className="modal-actions">
              <button
                className="button secondary"
                onClick={() => setDryRunTarget(null)}
                type="button"
              >
                Close
              </button>
              <button
                className="button"
                onClick={() => void executeDryRun()}
                disabled={dryRunLoading}
                type="button"
              >
                {dryRunLoading ? "Executing…" : "Execute Dry Run"}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
