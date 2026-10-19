# AgentProbe implementation status

This document summarizes the local AgentProbe foundation and the additional
upload-and-suite-builder work completed to date. It records implementation
status, not a claim that every module in the larger roadmap is finished.

## At a glance

- The existing evaluation platform remains in place: sample agent, configurable
  adapters, runner and traces, rule scoring, optional LLM judging, calibration,
  model comparisons, regression gate, API, and React dashboard.
## At a glance

- The existing evaluation platform remains in place: sample agent, configurable
  adapters, runner and traces, rule scoring, optional LLM judging, calibration,
  model comparisons, regression gate, API, and React dashboard.
- Sub-phase **A — target upload and inspection** is complete.
- Sub-phase **B — test suite builder** is complete.
- Sub-phase **C — agent runner extensions** is complete.
- Sub-phase **D — AI scoring and evaluation** is complete.
- Sub-phase **E — regression gate (Module 4)** is complete.
- Sub-phase **F — dashboard redesign (Module 5)** is complete.
- The next planned sub-phase is **G — end-to-end fixtures & final polish**. Work is paused
  for user approval between sub-phases.

## Sub-phase C — agent runner

Implemented:
- Run suites against sample agent or uploaded target projects via `POST /runs`.
- Configurable runner options: repeats, temperature, per-case timeout, concurrency,
  call spacing (delays for provider rate limits), and model override.
- Execution cost tracking via configurable model pricing table (`pricing.py`).
- Latency measurement accurately isolates execution time from deliberate rate-limit
  call spacing and backoff pacing (`CallPacer`).
- Real-time run progress polling (`GET /runs/{run_id}/progress`) with ETA, completed,
  passed, failed, and error counts.
- Run cancellation support (`POST /runs/{run_id}/cancel`) with graceful shutdown.
- Frontend runner panel on Runs page with options controls and live progress bar.

## Sub-phase D — AI scoring and evaluation

Implemented:

### 1. Configurable Rule Scorers
- **Text normalization**: Configurable per case (`text_normalization: ["nfkc", "whitespace", "case"]`),
  allowing cases to tolerate or enforce NFKC character normalization, whitespace collapsing,
  and case sensitivity.
  *Justification:* Normalizes casing and unicode formatting variations while retaining strictness
  when required.
- **Subsequence tool matching**: `tool_match_mode="subsequence"` with tolerance for read-only
  informational lookups (`search_*`, `get_*`, `lookup_*`, `check_*`, `read_*`, `list_*`, `fetch_*`
  or explicit `allowed_extra_read_tools`), while strictly disallowing unauthorized side-effecting tools.
  *Justification:* Multi-agent and exploratory agents often perform benign status/search lookups
  before taking expected actions; subsequence matching accommodates this safely.
- **Tool-call-based max steps**: Evaluates tool calls against `max_steps` while separating LLM turn counts.
  *Justification:* Distinguishes agent tool proliferation from internal conversational turns.

### 2. Failure Taxonomy & Separation of Errors
- Added `execution_error` to `FailureCategory` (`wrong_tool`, `bad_args`, `hallucination`, `loop`,
  `unsafe_output`, `execution_error`).
- Separates agent crashes, process timeouts, and dependency errors from answer hallucinations.
  *Justification:* Distinguishes system execution failures from model reasoning hallucinations.

### 3. Safety Scoring & Seeded Sensitive Values
- PII and sensitive value detection checks case-seeded values (`case.seeded_sensitive_values`)
  alongside real credential patterns (OpenAI keys, GitHub tokens, Groq tokens) while avoiding
  false flags on generic mock values.
- Explicitly tracks attack block rate and false-positive rate on benign look-alikes.

### 4. Multi-Agent Per-Agent Performance & Attribution
- Aggregates per-agent statistics across all cases involved:
  - `cases_involved`, `passed`, `failed`, `pass_rate`
  - `failure_types` breakdown per agent
  - `average_latency_ms`, `total_tokens`, `total_cost_usd`
  - `first_failing_cases`: Identifies the first failing agent in failed multi-agent workflows.
- Cases table highlights agents involved and first failing agent.
- `GET /runs/{run_id}/cases` returns `agents_involved` and `first_failing_agent`.
- `EvaluationSummary` includes `per_agent: dict[str, AgentSummary]`.

### 5. Judge Reliability & Calibration Panel
- `GET /calibration`: Returns latest calibration metrics (overall agreement %, Cohen's kappa,
  correctness agreement, faithfulness agreement, meets-target flag, target agreement threshold,
  and list of case disagreements).
- `POST /calibration/run`: Triggers judge calibration against labeled ground truth fixtures.
- Frontend `JudgeReliabilityPanel` component integrated on both `RunDetailPage` and `RunsPage`:
  - Visual cards for agreement %, Cohen's kappa, and correctness/faithfulness rates.
  - Meets-target badge (≥ 80%).
  - Detailed disagreements table (human vs judge verdicts).
  - "Run Calibration" button with live status feedback.

## Validation completed for sub-phase D

- Backend tests: **148 passed**, 1 existing LangGraph pending-deprecation warning.
- Ruff lint and formatting checks: passed without issues.
- Mypy type check: passed (61 source files checked).
- Frontend tests: **6 passed** (vitest).
- Frontend production build: passed (`tsc -b && vite build`).

## Sub-phase E — Module 4: Regression Gate

Implemented:

### 1. Case Classification Buckets & Sorting
- Every case evaluated between baseline and candidate is classified into exactly one bucket:
  - `regressed`: Passed in baseline, now failing in candidate.
  - `fixed`: Failed in baseline, now passing in candidate.
  - `persistent_failure`: Failed in baseline and still failing in candidate.
  - `passing`: Passed in both baseline and candidate.
- Case list is sorted with **regressed cases first**, followed by `persistent_failure`, `fixed`, and `passing`.

### 2. Configurable Gate Verdict Logic
- Evaluates gate result against configurable thresholds:
  - `pass_rate_threshold`: Allowed drop in pass rate (default 0.0%). Any quality regression fails the gate when threshold is 0%.
  - `latency_budget`: Allowed latency increase (default +20.0%).
  - `cost_budget`: Allowed cost increase (default +20.0%).
  - Safety check: Any safety regression (unblocked attacks, PII leaks, forbidden tools/reveals) causes immediate gate failure.
  - Missing cases: Candidate missing baseline test cases causes gate failure.
- Structured `GateEvaluationResult` with `passed`, `verdict`, and plain-language `reasons`.
- `GET /gate/check` endpoint supporting custom threshold query params (`pass_rate_threshold`, `latency_budget`, `cost_budget`).
- Comparison endpoints (`GET /compare` and `GET /compare-baseline`) automatically include the gate evaluation result.

### 3. Per-Agent Regression Attribution
- Pinpoints which agent caused quality and safety regressions in multi-agent workflows.
- Extracts agents involved and first failing agent from step traces.
- Produces per-agent breakdown: count, failure types breakdown, and plain-language summary:
  `"Agent {agent_name} caused {count} regression(s) ({failure_types_breakdown})."`

### 4. Baseline Promotion Safeguards
- `POST /baselines/{run_id}`: Promotes a run as an approved baseline.
- **Strict safety check**: Prevents baseline promotion of runs with ANY safety violations (unblocked attacks, PII leaks, forbidden actions) with a 409 Conflict.
- Only completed runs can be promoted.
- Human confirmation workflow before promoting to baseline.

### 5. UI Regression Gate Panel
- `RegressionGatePanel` component integrated on `RunDetailPage` and `RunsPage` comparison views:
  - Visual PASS/FAIL status badge.
  - Delta metrics: pass rate change vs allowed drop, latency change vs budget, cost change vs budget.
  - Classification count chips (Regressed, Fixed, Failed, Passed).
  - Gate failure reasons box.
  - Per-agent regression callout banner.
  - Case comparison table with bucket filter dropdown and side-by-side Before (Baseline) vs After (Candidate) trace summaries.
  - "Promote to Baseline" button with human confirmation modal, disabled for unsafe or incomplete runs.

## Sub-phase F — Module 5: Dashboard Redesign

Implemented:

### 1. Unified 5-Module Navigation Bar
- Top-level persistent header updated in `App.tsx` using `NavLink` with visual active-state indicators and keyboard accessibility across all five product modules:
  - **Overview** (`/`): Central health hub, target switcher, multi-agent attribution, and workflow shortcuts.
  - **Targets** (`/targets`): Target upload, heuristic agent inspection, and dry-run execution wizard.
  - **Test Suites** (`/suites`): Suite builder with manual creation, pack selection, draft generation, and human review.
  - **Runner** (`/runs`): Execution runner with concurrency, timeouts, repeat counts, call spacing, and live progress.
  - **Regression Gate** (`/gate`): Release gate evaluation with threshold controls, baseline comparisons, and promotion.
- Preserved existing nested routes: `/runs/:runId` (run details) and `/runs/:runId/cases/:caseId` (trace inspector).

### 2. Target Upload & Inspection Wizard (`/targets`)
- **Safe ZIP upload**: Max 100 MB, entry point validation (`package.module:function`), local masked environment variable configuration (never exposed via API), text and document-path input mapping.
- **Subprocess Isolation Notice**: Amber callout notifying users that uploaded code runs locally with user permissions; subprocess isolation is an execution boundary, not a root sandbox.
- **Heuristic Agent & Framework Inspection**: Triggers heuristic inspection (`POST /targets/{id}/inspect`) with detection for LangGraph, Google ADK, CrewAI, AutoGen, and custom agents. Displays candidate agents with confidence indicators (high/medium/low) and editable agent names.
- **Tracing SDK Notice**: Displays clear alert when tracing SDK is not detected: *"per-agent analysis unavailable; add the SDK (agentprobe_sdk) to enable it."*
- **Isolated Dry-Run Execution**: Executes sample inputs in the target's dedicated isolated virtual environment (`POST /targets/{id}/dry-run`) with formatted output, error diagnostics, and trace status.
- **Target Deletion**: Clean target cleanup (`DELETE /targets/{id}`).

### 3. Central Overview Dashboard (`/`)
- **Target Project Switcher**: Filter dashboard metrics across "All Projects & Runs", built-in LangGraph agent, and specific uploaded targets.
- **Top-Level Health Metric Cards**: Registered Targets, Test Suite Cases, Latest Run Pass Rate, and Judge Calibration Agreement.
- **Multi-Agent Performance Breakdown**: Table attributing performance to individual agents, highlighting cases involved, pass rates, average latencies, estimated costs, and first failing agent counts.
- **Product Workflow Quick Links**: Direct actions to launch Target Upload, Suite Builder, Runner, Scored Runs, and Regression Gate.
- **Recent Evaluation Runs**: List of recent runs with status pills, pass rates, latency, cost per 100 cases, and 1-click links to run details and regression gate check.

### 4. Dedicated Regression Gate Page (`/gate`)
- Candidate and baseline run pickers with suite/target auto-filtering.
- Threshold controls: Pass rate threshold, Max latency budget (seconds), Max cost budget (USD $).
- Integrates `RegressionGatePanel` with gate status badge, delta metrics, classification chips, per-agent regression attribution banner, and baseline promotion confirmation.

## Validation completed for sub-phase F

- Backend tests: **152 passed** (`uv run pytest`), 1 existing LangGraph pending-deprecation warning.
- Ruff lint and formatting checks: 100% clean (`uv run ruff check backend`).
- Mypy type check: 100% clean across all 61 source files (`uv run mypy backend`).
- Frontend unit tests: **24 passed across 8 test suites** (`npm test -- --run` via Vitest).
- Frontend production build: passed cleanly in 1.24s (`npm run build`).

## Sub-phase G — End-to-End Fixtures & Final Polish

Implemented:

### 1. Multi-Agent Customer Support Fixture
- Packaged reference multi-agent system in `fixtures/customer_support_agent/` and pre-built `fixtures/customer_support_agent.zip`:
  - `triage_agent`: Analyzes incoming customer intents and dispatches to specialists via `trace_handoff`.
  - `billing_agent`: Evaluates balance inquiries (`check_account_balance`) and processes refunds (`process_refund`) instrumented with `@traced_agent` and `@traced_tool`.
  - `tech_support_agent`: Answers technical questions (`query_knowledge_base`), resets credentials (`reset_api_token`), and strictly refuses prompt injection attempts and sensitive key disclosure.
  - `support_bot.entrypoint:run_agent`: Exposes standard callable interface for AgentProbe isolated runner invocation.
  - `pyproject.toml` and documentation for standalone local testing.

### 2. Comprehensive End-to-End Integration Test (`backend/tests/test_e2e_target_workflow.py`)
- Programmatically exercises all 5 AgentProbe modules in a single continuous automated lifecycle:
  1. **Target Upload (Foundation)**: Safe upload of `customer_support_agent.zip`, zip-slip rejection verification, isolated extraction, and credential masking.
  2. **Heuristic Agent Inspection (Module 1)**: Automatic detection of `triage_agent`, `billing_agent`, `tech_support_agent`, and verification that `agentprobe_sdk` tracing was detected.
  3. **Subprocess Dry-Run (Module 1)**: Dry-run execution inside the target's virtual environment, confirming isolated execution and trace capture.
  4. **Suite Authoring (Module 2)**: Dynamic case creation with expected tools, argument match modes, expected terms, and forbidden behavior constraints.
  5. **Agent Runner (Module 3)**: Execution of test cases against the uploaded target in isolated subprocesses with real-time tracking.
  6. **Trace Inspection & Attribution (Module 3 / Scoring)**: Verification of multi-agent step traces containing `agent`, `handoff`, and `tool` events.
  7. **Baseline Promotion (Module 4)**: Promoting clean run to approved baseline with safety verification.
  8. **Regression Gate (Module 4)**: Verifying release gate evaluation against baseline with 100% pass rate and zero regressions.
  9. **Target Cleanup**: Verifying clean target deletion (`DELETE /targets/{id}`).

### 3. Comprehensive Multi-Agent Target Guide (`docs/MULTI_AGENT_TARGET_GUIDE.md`)
- Detailed developer and operator guide covering:
  - Multi-agent project directory layout and dependencies.
  - Using `agentprobe_sdk` (`@traced_agent`, `@traced_tool`, `trace_handoff`, context managers).
  - Safe ZIP packaging, upload validation, entry points, and input/output mappings.
  - Authoring multi-agent test cases with exact/contains/ignore tool argument matching.
  - Interpreting per-agent performance breakdowns and first failing agent attribution.
  - Setting regression gate budgets and promoting safe baselines.

## Validation completed for sub-phase G

- Backend tests: **153 passed** (`uv run pytest`), 1 existing LangGraph pending-deprecation warning.
- Ruff lint and formatting checks: 100% clean (`uv run ruff check backend`).
- Mypy type check: 100% clean across all 62 source files (`uv run mypy backend`).
- Frontend unit tests: **24 passed across 8 test suites** (`npm test -- --run` via Vitest).
- Frontend production build: passed cleanly in 1.15s (`npm run build`).

## All Sub-Phases Completed

All sub-phases (A through G) across the AgentProbe product roadmap have been fully implemented, integrated, and validated:
- **Sub-Phase A**: Target Upload and Inspection (Foundation)
- **Sub-Phase B**: Module 1 — Test Suite Builder
- **Sub-Phase C**: Module 2 — Agent Runner
- **Sub-Phase D**: Module 3 — AI Scoring and Evaluation
- **Sub-Phase E**: Module 4 — Regression Gate
- **Sub-Phase F**: Module 5 — Dashboard Redesign
- **Sub-Phase G**: End-to-End Fixtures & Final Polish

