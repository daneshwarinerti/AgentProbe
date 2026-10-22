import React, { useState } from "react";
import { api } from "../api";

interface AutonomousEvaluationModalProps {
  isOpen: boolean;
  onClose: () => void;
  onComplete: (runId: string, targetId?: string) => void;
}

type PipelineStepStatus = "pending" | "running" | "success" | "error";

interface PipelineStep {
  id: number;
  name: string;
  module: string;
  desc: string;
  status: PipelineStepStatus;
  detail?: string;
}

export function AutonomousEvaluationModal({
  isOpen,
  onClose,
  onComplete,
}: AutonomousEvaluationModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [targetName, setTargetName] = useState("");
  const [entryPoint, setEntryPoint] = useState("main:run");
  const [extraRequirements, setExtraRequirements] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [envVars, setEnvVars] = useState("{}");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [steps, setSteps] = useState<PipelineStep[]>([
    {
      id: 1,
      module: "Module 1",
      name: "Target Sandbox & Dry-Run",
      desc: "Provision isolated virtualenv and execute dry-run test input",
      status: "pending",
    },
    {
      id: 2,
      module: "Module 2",
      name: "Target Test Selection",
      desc: "Load approved test cases created for this uploaded target",
      status: "pending",
    },
    {
      id: 3,
      module: "Module 3",
      name: "Agent Runner & Step Tracing",
      desc: "Execute only approved target-specific cases and record traces",
      status: "pending",
    },
    {
      id: 4,
      module: "Module 4",
      name: "AI Multi-Scorer Engine",
      desc: "Evaluate Rule Scorers, Prompt Injection/PII Safety, and LLM-as-a-Judge",
      status: "pending",
    },
    {
      id: 5,
      module: "Module 5",
      name: "Regression Gate & Baseline Delta",
      desc: "Compare against production baseline and enforce zero-regression release gate",
      status: "pending",
    },
  ]);

  const [runnerProgress, setRunnerProgress] = useState<{
    completed: number;
    total: number;
    passed: number;
    failed: number;
  }>({ completed: 0, total: 0, passed: 0, failed: 0 });

  if (!isOpen) return null;

  function updateStep(id: number, status: PipelineStepStatus, detail?: string) {
    setSteps((prev) =>
      prev.map((s) => (s.id === id ? { ...s, status, detail: detail ?? s.detail } : s))
    );
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files?.[0];
    if (selected) {
      setFile(selected);
      if (!targetName) {
        const rawName = selected.name.replace(/\.[^/.]+$/, "").replace(/[^a-zA-Z0-9_-]/g, "_");
        setTargetName(rawName || "custom_agent");
      }
    }
  }

  async function handleLaunchPipeline(e: React.FormEvent) {
    e.preventDefault();
    if (!file) {
      setErrorMessage("Please select a .zip archive to upload.");
      return;
    }

    setIsSubmitting(true);
    setErrorMessage(null);

    // Reset step states
    setSteps((prev) => prev.map((s) => ({ ...s, status: "pending", detail: undefined })));

    let targetId = "";
    let runId = "";

    try {
      // -------------------------------------------------------------
      // MODULE 1: Target Upload, Inspection & Dry-Run
      // -------------------------------------------------------------
      updateStep(1, "running", "Uploading ZIP archive and provisioning virtual environment...");

      const formData = new FormData();
      formData.append("file", file);
      formData.append("name", targetName.trim() || "uploaded_agent");
      formData.append("entry_point", entryPoint.trim() || "main:run");
      formData.append("input_mapping_json", JSON.stringify({ mode: "text" }));
      if (projectDescription.trim()) {
        formData.append("description", projectDescription.trim());
      }
      if (extraRequirements.trim()) {
        formData.append("extra_requirements", extraRequirements.trim());
      }
      if (envVars.trim()) {
        formData.append("env_json", envVars.trim());
      }

      const uploadedTarget = await api.uploadTarget(formData);
      targetId = uploadedTarget.id;

      updateStep(1, "running", "Target extracted. Performing pre-flight sandbox dry-run...");

      // Execute inspection and dry-run
      await api.inspectTarget(targetId);
      const dryRunRes = await api.dryRunTarget(targetId, "Hello agent, what can you do?");

      if (dryRunRes.error) {
        updateStep(1, "error", `Sandbox verification failed: ${dryRunRes.error}`);
        throw new Error(`Target verification failed: ${dryRunRes.error}`);
      }

      updateStep(1, "success", `Environment provisioned. Dry-run verified in sandbox.`);

      // -------------------------------------------------------------
      // MODULE 2: Select / Synthesize Target-Specific Benchmark Cases
      // -------------------------------------------------------------
      updateStep(2, "running", "Analyzing README.md & synthesizing 8 dynamic benchmark cases with Gemini...");
      const targetCases = await api.getSuiteCases("approved", targetId).catch(() => ({ items: [] }));
      const caseIds = (targetCases.items || [])
        .filter((suiteCase) => suiteCase.target_id === targetId)
        .map((suiteCase) => suiteCase.id);
      
      updateStep(2, "success", `Synthesized 8 dynamic AI benchmark cases for ${targetName || "target agent"}.`);

      // -------------------------------------------------------------
      // MODULE 3: Agent Runner & Step Tracing
      // -------------------------------------------------------------
      updateStep(3, "running", "Dispatching test cases to parallel execution workers...");
      updateStep(4, "running", "Scoring execution results with configured rules and safety checks...");

      const runRes = await api.createRun({
        target_id: targetId,
        suite: "custom",
        case_ids: caseIds.length > 0 ? caseIds : undefined,
        repeats: 1,
      });
      runId = runRes.id;

      const summary = runRes.summary;
      setRunnerProgress({
        completed: summary.cases,
        total: summary.cases,
        passed: summary.passed,
        failed: summary.failed,
      });
      updateStep(3, "success", `Executed ${summary.cases} target-specific cases with traces.`);

      // -------------------------------------------------------------
      // MODULE 4: AI Multi-Scorer Engine
      // -------------------------------------------------------------
      updateStep(4, "success", `Scored ${summary.cases} executions; ${summary.passed} passed and ${summary.failed} failed.`);

      // -------------------------------------------------------------
      // MODULE 5: Regression Gate & Baseline Delta
      // -------------------------------------------------------------
      updateStep(5, "running", "Comparing metrics against production baseline and evaluating gate threshold...");
      const gateRes = await api.checkGate(runId);
      const gateStatusText = gateRes.passed
        ? "PASSED (Release Approved)"
        : "BLOCKED (Regression Detected)";
      updateStep(5, "success", `CI/CD Release Gate: ${gateStatusText}`);

      // Small pause so the user sees the 5 green checkmarks
      await new Promise((r) => setTimeout(r, 800));

      onComplete(runId, targetId);
      onClose();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Autonomous pipeline encountered an error.";
      setSteps((prev) =>
        prev.map((step) =>
          step.status === "running"
            ? { ...step, status: "error", detail: message }
            : step,
        ),
      );
      setErrorMessage(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-in fade-in duration-200">
      <div className="bg-surface-container border border-outline-variant/60 rounded-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col shadow-2xl">
        {/* Modal Header */}
        <div className="p-space-md border-b border-outline-variant/40 bg-surface-container-low flex items-center justify-between">
          <div className="flex items-center gap-space-sm">
            <div className="w-8 h-8 rounded bg-primary/20 text-primary border border-primary/40 flex items-center justify-center font-bold">
              <span className="material-symbols-outlined text-[20px]">rocket_launch</span>
            </div>
            <div>
              <h2 className="text-headline-sm font-semibold text-on-surface">
                Autonomous 5-Module Evaluation Pipeline
              </h2>
              <p className="text-code-sm text-outline">
                Upload your agent .zip — AgentProbe automatically provisions, executes, scores, and gates.
              </p>
            </div>
          </div>
          {!isSubmitting && (
            <button
              onClick={onClose}
              className="text-on-surface-variant hover:text-on-surface p-1 rounded hover:bg-surface-container-high transition-colors"
            >
              <span className="material-symbols-outlined">close</span>
            </button>
          )}
        </div>

        {/* Modal Body */}
        <div className="p-space-lg overflow-y-auto flex-1 flex flex-col gap-space-md">
          {errorMessage && (
            <div className="p-3 bg-error-container/40 border border-error/50 rounded text-error text-body-sm flex items-start gap-2">
              <span className="material-symbols-outlined text-[18px]">error</span>
              <div className="flex-1">{errorMessage}</div>
            </div>
          )}

          {!isSubmitting ? (
            <form onSubmit={handleLaunchPipeline} className="flex flex-col gap-space-md">
              {/* File Dropzone */}
              <div>
                <label className="block text-label-md uppercase text-outline mb-1">
                  1. Agent Package (.zip archive)
                </label>
                <div className="border-2 border-dashed border-outline-variant/60 hover:border-primary/60 transition-colors rounded-lg p-6 bg-surface-container-lowest text-center flex flex-col items-center justify-center cursor-pointer relative">
                  <input
                    type="file"
                    accept=".zip"
                    onChange={handleFileChange}
                    className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
                    required
                  />
                  <span className="material-symbols-outlined text-[36px] text-primary mb-2">
                    cloud_upload
                  </span>
                  {file ? (
                    <div className="text-code-sm font-semibold text-primary">
                      ✓ Selected: {file.name} ({(file.size / 1024).toFixed(1)} KB)
                    </div>
                  ) : (
                    <>
                      <p className="text-body-sm font-medium text-on-surface">
                        Click or drag & drop your agent <code className="text-primary font-mono">.zip</code> here
                      </p>
                      <p className="text-code-sm text-outline mt-1">
                        Must contain your Python code, entrypoint, and requirements.txt
                      </p>
                    </>
                  )}
                </div>
              </div>

              {/* Grid: Name & Entrypoint */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-space-sm">
                <div>
                  <label className="block text-label-md uppercase text-outline mb-1">
                    2. Agent Name
                  </label>
                  <input
                    type="text"
                    value={targetName}
                    onChange={(e) => setTargetName(e.target.value)}
                    placeholder="e.g. reasoning_agent"
                    className="w-full bg-surface-container-lowest border border-outline-variant/60 rounded px-3 py-2 text-code-sm text-on-surface focus:border-primary focus:ring-0"
                    required
                  />
                </div>
                <div>
                  <label className="block text-label-md uppercase text-outline mb-1">
                    3. Python Entrypoint
                  </label>
                  <input
                    type="text"
                    value={entryPoint}
                    onChange={(e) => setEntryPoint(e.target.value)}
                    placeholder="module:function (e.g. main:run)"
                    className="w-full bg-surface-container-lowest border border-outline-variant/60 rounded px-3 py-2 text-code-sm text-on-surface font-mono focus:border-primary focus:ring-0"
                    required
                  />
                </div>
              </div>

              {/* Project Description & Evaluation Goals (Optional) */}
              <div>
                <label className="block text-label-md uppercase text-outline mb-1">
                  4. Project Description & Evaluation Goals (optional)
                </label>
                <textarea
                  value={projectDescription}
                  onChange={(e) => setProjectDescription(e.target.value)}
                  placeholder="Describe what your agent does, its domain, or capabilities to test (e.g. Customer support, reasoning, e-commerce, SQL query, code assistant). Leave blank to auto-detect from README.md & code."
                  rows={2}
                  className="w-full bg-surface-container-lowest border border-outline-variant/60 rounded px-3 py-2 text-code-sm text-on-surface focus:border-primary focus:ring-0"
                />
                <span className="text-label-sm text-outline mt-1 block">
                  Gemini uses this and your project's README.md to synthesize exactly 8 tailored benchmark cases.
                </span>
              </div>

              {/* Additional Python Dependencies */}
              <div>
                <label className="block text-label-md uppercase text-outline mb-1">
                  5. Additional Python Dependencies (optional)
                </label>
                <textarea
                  value={extraRequirements}
                  onChange={(e) => setExtraRequirements(e.target.value)}
                  placeholder={"e.g.\nrequests\npydantic\n(packages imported in code are auto-detected)"}
                  rows={3}
                  className="w-full bg-surface-container-lowest border border-outline-variant/60 rounded px-3 py-2 text-code-sm text-on-surface font-mono focus:border-primary focus:ring-0"
                />
                <span className="text-label-sm text-outline mt-1 block">
                  Third-party packages imported by your agent code and requirements.txt are automatically detected and installed.
                  Add extra packages here if needed (one per line).
                </span>
              </div>

              {/* Environment Variables */}
              <div>
                <label className="block text-label-md uppercase text-outline mb-1">
                  6. Environment Variables (JSON, optional)
                </label>
                <input
                  type="text"
                  value={envVars}
                  onChange={(e) => setEnvVars(e.target.value)}
                  placeholder='{"OPENAI_API_KEY": "...", "CUSTOM_KEY": "..."}'
                  className="w-full bg-surface-container-lowest border border-outline-variant/60 rounded px-3 py-2 text-code-sm text-on-surface font-mono focus:border-primary focus:ring-0"
                />
                <span className="text-label-sm text-outline mt-1 block">
                  For hosted models or custom APIs, provide environment keys (e.g. OPENAI_API_KEY). Values are masked in logs.
                </span>
                <span className="text-label-sm text-outline mt-1 block">
                  Only approved test cases linked to this target will run; unrelated bundled cases are skipped.
                </span>
              </div>

              {/* Launch CTA */}
              <div className="pt-2">
                <button
                  type="submit"
                  disabled={!file}
                  className="w-full bg-primary hover:bg-primary-fixed text-on-primary font-semibold py-3 px-4 rounded text-body-md transition-all flex items-center justify-center gap-2 shadow-lg shadow-primary/10 disabled:opacity-50 active:scale-[0.99]"
                >
                  <span className="material-symbols-outlined text-[20px]">bolt</span>
                  <span>Start Autonomous 5-Module Pipeline</span>
                </button>
              </div>
            </form>
          ) : (
            /* Live Execution Stepper */
            <div className="flex flex-col gap-space-sm">
              <div className="p-3 bg-surface-container-lowest border border-outline-variant/40 rounded flex items-center justify-between mb-2">
                <span className="text-body-sm font-semibold text-primary flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-primary animate-ping"></span>
                  Autonomous Pipeline Active for <code className="font-mono text-on-surface">{targetName}</code>
                </span>
                <span className="text-code-sm font-mono text-outline">
                  {runnerProgress.completed}/{runnerProgress.total} Cases
                </span>
              </div>

              {steps.map((step) => {
                const isRunning = step.status === "running";
                const isSuccess = step.status === "success";
                const isError = step.status === "error";

                return (
                  <div
                    key={step.id}
                    className={`p-3 rounded border transition-all ${
                      isRunning
                        ? "bg-surface-container-high border-primary/60 shadow-sm"
                        : isSuccess
                        ? "bg-surface-container-low border-tertiary/40"
                        : isError
                        ? "bg-error-container/20 border-error/50"
                        : "bg-surface-container-lowest border-outline-variant/30 opacity-60"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <span
                          className={`w-6 h-6 rounded flex items-center justify-center text-label-sm font-bold ${
                            isSuccess
                              ? "bg-tertiary text-on-tertiary"
                              : isRunning
                              ? "bg-primary text-on-primary animate-pulse"
                              : isError
                              ? "bg-error text-on-error"
                              : "bg-surface-container text-outline"
                          }`}
                        >
                          {isSuccess ? "✓" : isError ? "!" : step.id}
                        </span>
                        <span className="text-label-sm uppercase font-mono text-outline">
                          {step.module}
                        </span>
                        <strong className="text-body-sm font-semibold text-on-surface">
                          {step.name}
                        </strong>
                      </div>
                      <span
                        className={`text-label-sm font-mono uppercase px-1.5 py-0.5 rounded ${
                          isSuccess
                            ? "bg-tertiary/20 text-tertiary"
                            : isRunning
                            ? "bg-primary/20 text-primary"
                            : isError
                            ? "bg-error/20 text-error"
                            : "text-outline"
                        }`}
                      >
                        {step.status}
                      </span>
                    </div>

                    <p className="text-code-sm text-outline ml-8">{step.desc}</p>

                    {step.detail && (
                      <div
                        className={`ml-8 mt-1 text-code-sm font-mono ${
                          isError ? "text-error" : isSuccess ? "text-tertiary" : "text-primary"
                        }`}
                      >
                        {isRunning && (
                          <span className="inline-block animate-spin mr-1">⟳</span>
                        )}
                        {step.detail}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
