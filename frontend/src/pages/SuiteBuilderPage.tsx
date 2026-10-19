import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { EvaluationCase, SavedSuiteCase, SuiteCaseStats, SuitePack } from "../api";
import { api } from "../api";
import { ErrorState, LoadingState } from "../components/MetricCard";

const emptyCase: EvaluationCase = {
  id: "",
  input: "",
  expected_tools: [],
  expected_answer_contains: [],
  forbidden: [],
  expected_agents: [],
  expected_handoffs: [],
  expected_first_failing_agent: null,
  target_agent: null,
  max_steps: 5,
  category: "general",
  tags: [],
};

export function SuiteBuilderPage() {
  const [cases, setCases] = useState<SavedSuiteCase[]>([]);
  const [stats, setStats] = useState<SuiteCaseStats | null>(null);
  const [packs, setPacks] = useState<SuitePack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [caseStatus, setCaseStatus] = useState("all");
  const [targetFilter, setTargetFilter] = useState("");
  const [targetId, setTargetId] = useState("");
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem("agentprobe_api_key") ?? "");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<EvaluationCase>(emptyCase);
  const [toolJson, setToolJson] = useState("[]");
  const [forbiddenJson, setForbiddenJson] = useState("[]");
  const [handoffJson, setHandoffJson] = useState("[]");
  const [importFormat, setImportFormat] = useState<"json" | "yaml">("yaml");
  const [importContent, setImportContent] = useState("");
  const [generationCount, setGenerationCount] = useState(20);
  const [toolNames, setToolNames] = useState("");
  const [working, setWorking] = useState(false);

  const loadData = useCallback(async (status = caseStatus) => {
    setLoading(true);
    setError(null);
    try {
      const scopedTarget = targetFilter.trim() || undefined;
      const [caseResponse, statsResponse, packResponse] = await Promise.all([
        api.getSuiteCases(status, scopedTarget),
        api.getSuiteStats(scopedTarget),
        api.getSuitePacks(),
      ]);
      setCases(caseResponse.items);
      setStats(statsResponse);
      setPacks(packResponse.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load test suites.");
    } finally {
      setLoading(false);
    }
  }, [caseStatus, targetFilter]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  function saveKey() {
    sessionStorage.setItem("agentprobe_api_key", apiKey);
  }

  function startEditing(record: SavedSuiteCase) {
    setEditingId(record.id);
    setForm(record.case);
    setToolJson(JSON.stringify(record.case.expected_tools, null, 2));
    setForbiddenJson(JSON.stringify(record.case.forbidden, null, 2));
    setHandoffJson(JSON.stringify(record.case.expected_handoffs, null, 2));
    setTargetId(record.target_id ?? "");
    setActionError(null);
  }

  function resetForm() {
    setEditingId(null);
    setForm(emptyCase);
    setToolJson("[]");
    setForbiddenJson("[]");
    setHandoffJson("[]");
  }

  async function saveCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    saveKey();
    setWorking(true);
    setActionError(null);
    try {
      const caseData: EvaluationCase = {
        ...form,
        expected_tools: JSON.parse(toolJson) as EvaluationCase["expected_tools"],
        forbidden: JSON.parse(forbiddenJson) as EvaluationCase["forbidden"],
        expected_handoffs: JSON.parse(handoffJson) as EvaluationCase["expected_handoffs"],
      };
      const payload = { case: caseData, ...(targetId.trim() ? { target_id: targetId.trim() } : {}) };
      if (editingId) await api.updateSuiteCase(editingId, payload);
      else await api.createSuiteCase(payload);
      resetForm();
      await loadData();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to save test case.");
    } finally {
      setWorking(false);
    }
  }

  async function mutateCase(action: () => Promise<unknown>, successMessage?: string) {
    saveKey();
    setWorking(true);
    setActionError(null);
    try {
      await action();
      if (successMessage) setActionError(successMessage);
      await loadData();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to update test case.");
    } finally {
      setWorking(false);
    }
  }

  async function importCases() {
    saveKey();
    setWorking(true);
    setActionError(null);
    try {
      const result = await api.importSuiteCases(
        importFormat,
        importContent,
        targetId.trim() || undefined,
      );
      setImportContent("");
      setActionError(`Imported ${result.total} approved case(s).`);
      await loadData();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to import cases.");
    } finally {
      setWorking(false);
    }
  }

  async function generateDrafts(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    saveKey();
    setWorking(true);
    setActionError(null);
    try {
      const result = await api.generateSuiteCases(
        targetId.trim(),
        generationCount,
        toolNames.split(",").map((name) => name.trim()).filter(Boolean),
      );
      setActionError(`Created ${result.total} drafts. Review and approve them before running.`);
      setCaseStatus("draft");
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Unable to generate drafts.");
    } finally {
      setWorking(false);
    }
  }

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;

  return (
    <main className="page">
      <section className="page-heading">
        <div>
          <p className="eyebrow">Module 1 · Test design</p>
          <h1>Test suites</h1>
          <p className="muted">Edit cases, review generated drafts, and choose a reusable pack.</p>
        </div>
        <button className="button secondary" onClick={() => void loadData()} type="button">Refresh</button>
      </section>

      <section className="metric-grid">
        <Metric label="Saved cases" value={stats?.total ?? 0} />
        <Metric label="Approved" value={stats?.approved ?? 0} />
        <Metric label="Drafts" value={stats?.draft ?? 0} />
        <Metric label="Reusable packs" value={packs.length} />
      </section>

      <section className="panel">
        <h2>Reusable packs</h2>
        <p className="muted">Use a pack id as the suite name when starting a run; use <code>custom</code> for approved saved cases.</p>
        <ul className="pack-list">
          {packs.map((pack) => (
            <li key={pack.id}>
              <strong>{pack.name}</strong>
              <span className="muted">{pack.cases} cases · {pack.categories.join(", ")}</span>
              <code>{pack.id}</code>
              <button
                className="button secondary"
                disabled={working}
                onClick={() => void mutateCase(
                  () => api.copySuitePack(pack.id, targetId.trim() || undefined),
                )}
                type="button"
              >
                Copy as drafts
              </button>
            </li>
          ))}
          <li><strong>Custom approved cases</strong><span className="muted">Approved global cases; target-specific cases wait for target runner support</span><code>custom</code></li>
        </ul>
      </section>

      <section className="panel">
        <h2>Suite coverage</h2>
        <div className="coverage-grid">
          <div><h3>By category</h3>{Object.entries(stats?.categories ?? {}).map(([key, count]) => <p key={key}>{key}: <strong>{count}</strong></p>)}</div>
          <div><h3>By target agent</h3>{Object.entries(stats?.agents ?? {}).map(([key, count]) => <p key={key}>{key}: <strong>{count}</strong></p>)}</div>
        </div>
      </section>

      <section className="panel">
        <h2>{editingId ? `Edit ${editingId}` : "Create a case"}</h2>
        <form className="suite-form" onSubmit={(event) => void saveCase(event)}>
          <div className="form-row">
            <label>Case id<input required maxLength={128} value={form.id} disabled={Boolean(editingId)} onChange={(event) => setForm({ ...form, id: event.target.value })} /></label>
            <label>Target id (optional)<input value={targetId} onChange={(event) => setTargetId(event.target.value)} /></label>
            <label>Target agent<input value={form.target_agent ?? ""} onChange={(event) => setForm({ ...form, target_agent: event.target.value || null })} /></label>
          </div>
          <label>Input<textarea required rows={3} value={form.input} onChange={(event) => setForm({ ...form, input: event.target.value })} /></label>
          <div className="form-row">
            <label>Expected answer terms (one per line)<textarea rows={3} value={form.expected_answer_contains.join("\n")} onChange={(event) => setForm({ ...form, expected_answer_contains: splitLines(event.target.value) })} /></label>
            <label>Category<input required value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} /></label>
            <label>Tags (comma-separated)<input value={form.tags.join(", ")} onChange={(event) => setForm({ ...form, tags: event.target.value.split(",").map((tag) => tag.trim()).filter(Boolean) })} /></label>
          </div>
          <div className="form-row">
            <label>Expected tools and args (JSON; supports per-argument exact / contains / ignore)<textarea rows={8} value={toolJson} onChange={(event) => setToolJson(event.target.value)} /></label>
            <label>Forbidden behaviors (JSON)<textarea rows={8} value={forbiddenJson} onChange={(event) => setForbiddenJson(event.target.value)} /></label>
          </div>
          <div className="form-row">
            <label>Expected agents (one per line)<textarea rows={3} value={form.expected_agents.join("\n")} onChange={(event) => setForm({ ...form, expected_agents: splitLines(event.target.value) })} /></label>
            <label>Expected handoffs (JSON)<textarea rows={3} value={handoffJson} onChange={(event) => setHandoffJson(event.target.value)} /></label>
            <label>Maximum tool calls<input type="number" min={1} value={form.max_steps} onChange={(event) => setForm({ ...form, max_steps: Number(event.target.value) })} /></label>
          </div>
          <div className="form-row">
            <label>Local API key<input autoComplete="off" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
            <button className="button" disabled={working} type="submit">{working ? "Saving…" : editingId ? "Save changes" : "Create case"}</button>
            {editingId && <button className="button secondary" onClick={resetForm} type="button">Cancel edit</button>}
          </div>
        </form>
      </section>

      <section className="panel">
        <h2>Generate draft cases</h2>
        <p className="muted">Uses the configured LLM and agent/tool names only; source files and documents are not sent. Generated cases remain excluded from runs until approved.</p>
        <form className="form-row" onSubmit={(event) => void generateDrafts(event)}>
          <label>Target id<input required value={targetId} onChange={(event) => setTargetId(event.target.value)} /></label>
          <label>Count (20–40)<input type="number" min={20} max={40} value={generationCount} onChange={(event) => setGenerationCount(Number(event.target.value))} /></label>
          <label>Known tool names (comma-separated)<input value={toolNames} onChange={(event) => setToolNames(event.target.value)} /></label>
          <button className="button" disabled={working} type="submit">{working ? "Generating…" : "Generate drafts"}</button>
        </form>
      </section>

      <section className="panel">
        <h2>Import and export</h2>
        <div className="form-row">
          <label>Format<select value={importFormat} onChange={(event) => setImportFormat(event.target.value as "json" | "yaml")}><option value="yaml">YAML</option><option value="json">JSON</option></select></label>
          <label>Import cases<textarea rows={5} value={importContent} onChange={(event) => setImportContent(event.target.value)} placeholder="Paste one case or a list of cases" /></label>
          <button className="button" disabled={working || !importContent.trim()} onClick={() => void importCases()} type="button">Validate and import</button>
          <a className="button secondary download-link" href={`/api/suite-cases/export?format=${importFormat}${targetId.trim() ? `&target_id=${encodeURIComponent(targetId.trim())}` : ""}`}>Export approved cases</a>
        </div>
      </section>

      {actionError && <p className="panel" role="status">{actionError}</p>}

      <section className="panel">
        <div className="page-heading">
          <h2>Cases</h2>
          <div className="form-row">
            <label>Target filter<input value={targetFilter} onChange={(event) => setTargetFilter(event.target.value)} /></label>
            <label className="status-filter">Show<select value={caseStatus} onChange={(event) => setCaseStatus(event.target.value)}><option value="all">All</option><option value="approved">Approved</option><option value="draft">Drafts</option></select></label>
          </div>
        </div>
        {cases.length === 0 ? <p className="empty-state">No saved cases match this filter.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Case</th><th>Agent</th><th>Category</th><th>Status</th><th>Origin</th><th>Actions</th></tr></thead>
              <tbody>{cases.map((record) => (
                <tr key={record.id}>
                  <td><strong>{record.id}</strong><div className="muted case-input-preview">{record.case.input}</div></td>
                  <td>{record.case.target_agent ?? "—"}</td>
                  <td>{record.case.category}</td>
                  <td><span className={`status-pill ${record.status}`}>{record.status}</span></td>
                  <td>{record.provenance}{record.human_reviewed ? " · reviewed" : ""}</td>
                  <td className="case-actions">
                    <button className="button secondary" disabled={working} onClick={() => startEditing(record)} type="button">Edit</button>
                    <button className="button secondary" disabled={working} onClick={() => void mutateCase(() => api.duplicateSuiteCase(record.id))} type="button">Duplicate</button>
                    {record.status === "draft" && <button className="button" disabled={working} onClick={() => void mutateCase(() => api.reviewSuiteCase(record.id, true))} type="button">Approve</button>}
                    {record.status === "approved" && record.provenance === "generated" && <button className="button secondary" disabled={working} onClick={() => void mutateCase(() => api.reviewSuiteCase(record.id, false))} type="button">Return to draft</button>}
                    <button className="button danger" disabled={working} onClick={() => void mutateCase(() => api.deleteSuiteCase(record.id))} type="button">Delete</button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <article className="metric-card"><p className="metric-label">{label}</p><p className="metric-value">{value}</p></article>;
}

function splitLines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}
