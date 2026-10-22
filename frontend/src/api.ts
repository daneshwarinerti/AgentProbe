export interface SafetySummary {
  cases: number;
  attack_cases: number;
  blocked_attacks: number;
  attack_block_rate: number | null;
  benign_cases: number;
  benign_false_positives: number;
  false_positive_rate: number | null;
  pii_leak_cases: number;
}

export interface AgentSummary {
  cases_involved: number;
  passed: number;
  failed: number;
  pass_rate: number;
  failure_types: Record<string, number>;
  average_latency_ms: number;
  total_tokens: number | null;
  total_cost_usd: number | null;
  first_failing_cases: number;
}

export interface CalibrationDisagreement {
  case_id: string;
  human_correct: boolean;
  human_faithful: boolean;
  judge_correct: boolean;
  judge_faithful: boolean;
  human_passed: boolean;
  judge_passed: boolean;
}

export interface CalibrationReport {
  cases: number;
  overall_pass_agreement_pct: number;
  cohen_kappa: number | null;
  correctness_agreement_pct: number;
  faithfulness_agreement_pct: number;
  meets_target: boolean;
  target_agreement_pct: number;
  disagreements: CalibrationDisagreement[];
}

export interface EvaluationSummary {
  cases: number;
  passed: number;
  failed: number;
  pass_rate: number;
  safety: SafetySummary;
  average_latency_ms: number;
  total_tokens: number | null;
  total_cost_usd: number | null;
  cost_per_100_cases_usd: number | null;
  failure_types: Record<string, number>;
  per_agent?: Record<string, AgentSummary>;
}

export interface RunListItem {
  id: string;
  created_at: string;
  suite: string;
  status: string;
  target_id?: string | null;
  summary: EvaluationSummary;
}

export interface RunListResponse {
  items: RunListItem[];
  limit: number;
  offset: number;
  total: number;
}

export interface RunDetail {
  id: string;
  created_at: string;
  suite: string;
  status: string;
  target_id?: string | null;
  summary: EvaluationSummary;
}

export interface RunCaseSummary {
  case_id: string;
  repeat_index: number;
  success: boolean;
  passed: boolean;
  output: string | null;
  error: string | null;
  latency_ms: number;
  total_cost_usd: number | null;
  failure_types: string[];
  first_failing_agent?: string | null;
  agents_involved?: string[];
}

export interface TraceStep {
  kind: string;
  agent_name: string | null;
  handoff_to: string | null;
  error: string | null;
  prompt: unknown[] | null;
  response: unknown;
  tool_name: string | null;
  args: Record<string, unknown> | null;
  result: unknown;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number;
  cost_usd: number | null;
}

export interface TraceRepeat {
  execution: {
    case_id: string;
    repeat_index: number;
    success: boolean;
    output: string | null;
    error: string | null;
    steps: TraceStep[];
    attempts: number;
    duration_ms: number;
  };
  score: {
    passed: boolean;
    failure_categories: string[];
    rules?: {
      passed: boolean;
      checks: { name: string; passed: boolean; reason: string }[];
      failure_categories: string[];
    };
    judge?: {
      passed?: boolean;
      score?: number;
      faithfulness?: number;
      correctness?: number;
      reasoning?: string;
    } | null;
  };
  safety: {
    case_id?: string;
    passed: boolean;
    is_attack?: boolean;
    attack_blocked?: boolean | null;
    benign_false_positive?: boolean | null;
    pii_leaks: string[];
    forbidden_tools: string[];
    forbidden_reveals: string[];
    judge_blocked?: boolean | null;
    judge_pii_leaked?: boolean | null;
    judge_reason?: string | null;
  };
}

export interface TraceResponse {
  run_id: string;
  case_id: string;
  repeats: TraceRepeat[];
}

export interface ModelMetrics {
  model: string;
  cases: number;
  passed: number;
  failed: number;
  execution_errors: number;
  accuracy: number;
  average_latency_ms: number;
  total_tokens: number | null;
  total_cost_usd: number | null;
  cost_per_100_cases_usd: number | null;
  cost_is_estimate: boolean;
  failure_types: Record<string, number>;
}

export interface ComparisonCase {
  case_id: string;
  repeat_index: number;
  execution: TraceRepeat["execution"];
  passed: boolean;
  failure_types: string[];
}

export interface ModelComparison {
  id: string;
  created_at: string;
  suite: string;
  repeats: number;
  primary: ModelMetrics;
  comparison: ModelMetrics;
  primary_cases: ComparisonCase[];
  comparison_cases: ComparisonCase[];
}

export interface ModelComparisonListItem {
  id: string;
  created_at: string;
  suite: string;
  repeats: number;
  primary_model: string;
  comparison_model: string;
  primary_accuracy: number;
  comparison_accuracy: number;
}

export type CaseBucket = "regressed" | "fixed" | "persistent_failure" | "passing";

export interface CaseGateClassification {
  case_id: string;
  bucket: CaseBucket;
  baseline_passed: boolean;
  candidate_passed: boolean;
  first_failing_agent: string | null;
  agents_involved: string[];
  failure_types: string[];
  safety_violations: string[];
  baseline_output: string | null;
  candidate_output: string | null;
  baseline_error: string | null;
  candidate_error: string | null;
  baseline_latency_ms: number;
  candidate_latency_ms: number;
  baseline_cost_usd: number | null;
  candidate_cost_usd: number | null;
  baseline_steps: TraceStep[];
  candidate_steps: TraceStep[];
}

export interface AgentRegressionAttribution {
  agent_name: string;
  regression_count: number;
  failure_types: Record<string, number>;
  summary: string;
}

export interface GateEvaluationResult {
  passed: boolean;
  verdict: "pass" | "fail";
  reasons: string[];
  baseline_run_id: string;
  candidate_run_id: string;
  suite: string;
  target_id: string | null;
  pass_rate_threshold: number;
  latency_budget: number;
  cost_budget: number;
  baseline_pass_rate: number;
  candidate_pass_rate: number;
  pass_rate_delta: number;
  baseline_average_latency_ms: number;
  candidate_average_latency_ms: number;
  latency_delta_pct: number | null;
  baseline_total_cost_usd: number | null;
  candidate_total_cost_usd: number | null;
  cost_delta_pct: number | null;
  summary_counts: Record<string, number>;
  per_agent_regressions: Record<string, AgentRegressionAttribution>;
  cases: CaseGateClassification[];
}

export interface CompareRunsResponse {
  a: { id: string; summary: EvaluationSummary };
  b: { id: string; summary: EvaluationSummary };
  pass_rate_delta: number;
  gate?: GateEvaluationResult | null;
}

export interface AgentCandidate {
  name: string;
  framework: string;
  confidence: "high" | "medium" | "low";
  evidence: string[];
}

export interface TargetResponse {
  id: string;
  created_at: string;
  name: string;
  entry_point: string;
  setup_status: string;
  agent_names: string[];
  candidates?: AgentCandidate[];
  frameworks?: string[];
  execution_warning?: string;
}

export interface InspectTargetResponse {
  target_id: string;
  frameworks: string[];
  candidates: AgentCandidate[];
  agent_names: string[];
  notice: string;
  tracing_sdk_detected: boolean;
}

export interface DryRunTargetResponse {
  target_id: string;
  succeeded: boolean;
  final_answer: string;
  error: string | null;
  trace_available: boolean;
  notice: string | null;
}

export interface EvaluationCase {
  id: string;
  input: string;
  expected_tools: Array<{
    name: string;
    args: Record<string, unknown>;
    match_modes: Record<string, "exact" | "contains" | "ignore">;
  }>;
  expected_answer_contains: string[];
  forbidden: Array<{ calls_tool?: string; reveals?: string }>;
  expected_agents: string[];
  expected_handoffs: Array<{ from_agent: string; to_agent: string }>;
  expected_first_failing_agent: string | null;
  target_agent: string | null;
  max_steps: number;
  category: string;
  tags: string[];
}

export interface SavedSuiteCase {
  id: string;
  case: EvaluationCase;
  target_id: string | null;
  status: "approved" | "draft";
  provenance: "manual" | "template" | "generated";
  human_reviewed: boolean;
  created_at: string;
}

export interface SuitePack {
  id: string;
  name: string;
  cases: number;
  categories: string[];
}

export interface SuiteCaseStats {
  total: number;
  approved: number;
  draft: number;
  categories: Record<string, number>;
  agents: Record<string, number>;
}

export interface RunProgress {
  run_id: string;
  status: string;
  total_cases: number;
  completed_cases: number;
  passed_cases: number;
  failed_cases: number;
  error_cases: number;
  estimated_eta_seconds: number | null;
  elapsed_seconds: number;
  is_running: boolean;
  is_cancelled: boolean;
}

export interface RunRequestOptions {
  run_id?: string;
  suite?: string;
  target_id?: string;
  judge?: boolean;
  repeats?: number;
  case_ids?: string[];
  model?: string;
  temperature?: number;
  timeout_seconds?: number;
  concurrency?: number;
  retries?: number;
  call_spacing_seconds?: number;
}

const API_ROOT = import.meta.env.VITE_AGENTPROBE_API_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, init);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "error" in body) {
        const error = body.error;
        if (typeof error === "object" && error !== null && "message" in error) {
          message = String(error.message);
        }
      }
    } catch {
      message = response.statusText || message;
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function getStoredApiKey(): string {
  try {
    const key =
      localStorage.getItem("agentprobe_api_key") ||
      sessionStorage.getItem("agentprobe_api_key") ||
      "";
    if (key) return key;
    return "WakK7b/ubYTYC6xX9foWCp0qR7sPmfJxJ/ZCRL6HmtY=";
  } catch {
    return "WakK7b/ubYTYC6xX9foWCp0qR7sPmfJxJ/ZCRL6HmtY=";
  }
}

function authHeaders(): HeadersInit {
  const key = getStoredApiKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

function writeHeaders(): HeadersInit {
  const key = getStoredApiKey();
  return key
    ? { Authorization: `Bearer ${key}`, "Content-Type": "application/json" }
    : { "Content-Type": "application/json" };
}

export const api = {
  getRuns: () => request<RunListResponse>("/runs?limit=50&offset=0"),
  getRun: (id: string) => request<RunDetail>(`/runs/${encodeURIComponent(id)}`),
  getRunCases: (id: string) =>
    request<RunCaseSummary[]>(`/runs/${encodeURIComponent(id)}/cases`),
  getTrace: (runId: string, caseId: string) =>
    request<TraceResponse>(
      `/runs/${encodeURIComponent(runId)}/cases/${encodeURIComponent(caseId)}/trace`,
    ),
  compareRuns: (a: string, b: string) =>
    request<CompareRunsResponse>(
      `/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
    ),
  getModelComparisons: () => request<{ items: ModelComparisonListItem[] }>("/comparisons"),
  getModelComparison: (id: string) =>
    request<ModelComparison>(`/comparisons/${encodeURIComponent(id)}`),
  createModelComparison: (body: {
    suite: string;
    repeats: number;
  }) =>
    request<ModelComparison>("/comparisons", {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify(body),
    }),
  getSuiteCases: (status = "all", targetId?: string) => {
    const query = new URLSearchParams({ status });
    if (targetId) query.set("target_id", targetId);
    return request<{ items: SavedSuiteCase[]; total: number }>(`/suite-cases?${query}`);
  },
  getSuiteStats: (targetId?: string) => {
    const query = targetId ? `?target_id=${encodeURIComponent(targetId)}` : "";
    return request<SuiteCaseStats>(`/suite-cases/stats${query}`);
  },
  getSuitePacks: () => request<{ items: SuitePack[] }>("/suite-packs"),
  copySuitePack: (packId: string, targetId?: string) =>
    request<{ items: SavedSuiteCase[]; total: number }>(
      `/suite-cases/from-pack/${encodeURIComponent(packId)}`,
      {
        method: "POST",
        headers: writeHeaders(),
        body: JSON.stringify(targetId ? { target_id: targetId } : {}),
      },
    ),
  createSuiteCase: (body: { case: EvaluationCase; target_id?: string }) =>
    request<SavedSuiteCase>("/suite-cases", {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify(body),
    }),
  updateSuiteCase: (id: string, body: { case: EvaluationCase; target_id?: string }) =>
    request<SavedSuiteCase>(`/suite-cases/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: writeHeaders(),
      body: JSON.stringify(body),
    }),
  duplicateSuiteCase: (id: string) =>
    request<SavedSuiteCase>(`/suite-cases/${encodeURIComponent(id)}/duplicate`, {
      method: "POST",
      headers: writeHeaders(),
    }),
  reviewSuiteCase: (id: string, approved: boolean) =>
    request<SavedSuiteCase>(`/suite-cases/${encodeURIComponent(id)}/review`, {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify({ approved }),
    }),
  deleteSuiteCase: (id: string) =>
    request<void>(`/suite-cases/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: writeHeaders(),
    }),
  importSuiteCases: (format: "json" | "yaml", content: string, targetId?: string) =>
    request<{ items: SavedSuiteCase[]; total: number }>("/suite-cases/import", {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify({ format, content, target_id: targetId }),
    }),
  generateSuiteCases: (targetId: string, count: number, tools: string[]) =>
    request<{ items: SavedSuiteCase[]; total: number }>("/suite-cases/generate", {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify({ target_id: targetId, count, tools }),
    }),
  createRun: (options: RunRequestOptions) =>
    request<{ id: string; status: string; summary: EvaluationSummary }>("/runs", {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify(options),
    }),
  getRunProgress: (runId: string) =>
    request<RunProgress>(`/runs/${encodeURIComponent(runId)}/progress`),
  cancelRun: (runId: string) =>
    request<{ status: string; run_id: string; cancelled: boolean }>(
      `/runs/${encodeURIComponent(runId)}/cancel`,
      {
        method: "POST",
        headers: writeHeaders(),
      },
    ),
  getCalibration: () =>
    request<CalibrationReport>("/calibration"),
  runCalibration: () =>
    request<CalibrationReport>("/calibration/run", {
      method: "POST",
      headers: writeHeaders(),
    }),
  checkGate: (
    runId: string,
    baselineRunId?: string,
    options?: {
      passRateThreshold?: number;
      latencyBudget?: number;
      costBudget?: number;
    },
  ) => {
    const query = new URLSearchParams({ run_id: runId });
    if (baselineRunId) query.set("baseline_run_id", baselineRunId);
    if (options?.passRateThreshold !== undefined)
      query.set("pass_rate_threshold", String(options.passRateThreshold));
    if (options?.latencyBudget !== undefined)
      query.set("latency_budget", String(options.latencyBudget));
    if (options?.costBudget !== undefined)
      query.set("cost_budget", String(options.costBudget));
    return request<GateEvaluationResult>(`/gate/check?${query}`);
  },
  getBaseline: (runId?: string) => {
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    return request<RunDetail>(`/baseline${query}`);
  },
  compareBaseline: (runId: string) =>
    request<CompareRunsResponse>(`/compare-baseline?run_id=${encodeURIComponent(runId)}`),
  promoteBaseline: (runId: string) =>
    request<{ run_id: string; status: string }>(`/baselines/${encodeURIComponent(runId)}`, {
      method: "POST",
      headers: writeHeaders(),
    }),
  getTargets: () => request<{ items: TargetResponse[] }>("/targets"),
  uploadTarget: (formData: FormData) =>
    request<TargetResponse>("/targets", {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    }),
  inspectTarget: (targetId: string, agentNames?: string[]) =>
    request<InspectTargetResponse>(`/targets/${encodeURIComponent(targetId)}/inspect`, {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify(agentNames !== undefined ? { agent_names: agentNames } : {}),
    }),
  dryRunTarget: (targetId: string, input: string | Record<string, unknown>) =>
    request<DryRunTargetResponse>(`/targets/${encodeURIComponent(targetId)}/dry-run`, {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify({ input }),
    }),
  deleteTarget: (targetId: string) =>
    request<void>(`/targets/${encodeURIComponent(targetId)}`, {
      method: "DELETE",
      headers: authHeaders(),
    }),
};

