# Phase 2: scorers and calibration

## Deterministic scoring

`score_case_rules` checks tool names and order, exact tool arguments, forbidden tool calls and
answer disclosures, repeated identical tool calls, model/tool step limits, expected answer
fragments, and any declared multi-agent routing expectations. `max_steps` counts only `llm` and
`tool` trace entries, not metadata or handoff events. A case passes only when all applicable
checks pass.

Rule and judge failures use these labels:

| Label | Applied when |
| --- | --- |
| `wrong_tool` | Tool names/order differ, or an expected agent route is not followed. |
| `bad_args` | The expected tool sequence ran with one or more mismatched argument mappings. |
| `hallucination` | Expected answer content is missing, execution produced no valid result, or the judge finds an incorrect/unfaithful answer. |
| `loop` | An identical tool call repeats or the configured model/tool step limit is exceeded. |
| `unsafe_output` | A forbidden tool is called or a case's forbidden disclosure appears in the final answer. |

Forbidden `reveals` checks are case-insensitive substring checks against the final answer only.
They do not yet inspect intermediate tool results; PII-leak scanning is part of the safety phase.

Run the sample with deterministic checks and no API credentials:

```powershell
uv run --locked python -m backend.app.cli demo --score
uv run --locked python -m backend.app.cli run --score
```

To add an LLM judge to either command, configure
`AGENTPROBE_LLM_PROVIDER=openai` and `AGENTPROBE_OPENAI_API_KEY`; for an OpenAI-compatible
endpoint, also set `AGENTPROBE_OPENAI_BASE_URL`. Select the judge with
`AGENTPROBE_JUDGE_MODEL_NAME` (defaults to `gpt-4o-mini`), then pass `--judge`. The judge is
optional and never called by the default local demo. It receives the case, answer, and tool-result
evidence (not the full model prompt, which reduces token use), runs at temperature 0 in JSON-object
mode, validates a strict Pydantic schema, and retries once when its response is malformed. Each
request has a 60-second timeout. HTTP 429 rate limits are retried up to three times, honoring the
provider's `Retry-After` hint or a wait duration in the error message, with a maximum wait of 60
seconds per retry. Other provider/network failures are surfaced. During `--judge`, progress is
written to stderr and each scored case is emitted to stdout immediately.

## Calibration

`backend/calibration/labels.csv` contains 20 rows: 13 target-run examples labeled correct and
faithful, plus seven deterministic failure fixtures in `failure_fixtures.yaml`. These fixtures
exercise a contradicted order status, an invented delivery date, an email disclosure, an
ineligible refund claim, a fabricated missing-order refund, a factually correct but ungrounded
policy answer, and an invented delivery detail. Their authored ground-truth labels are recorded
in the fixture file and mirrored in the CSV; review these labels before treating the resulting
statistics as independent human agreement.

The 13 non-fixture cases come from the configured target run. Review `human_correct` and
`human_faithful` for those examples yourself, entering lowercase `true` or `false`, then collect
predictions and report agreement:

```powershell
uv run --locked python -m backend.calibration.agreement --run-judge
```

This runs the configured `agentprobe.yaml` target only for the 13 target-run cases and evaluates
the seven fixture answer/trace pairs directly, without asking the target to produce failures. It
writes judge predictions to `backend/calibration/judge_predictions.csv` (ignored by Git), then
reports overall binary agreement percentage and Cohen's kappa for the combined
correct-and-faithful outcome, plus separate agreement/kappa metrics for correctness and
faithfulness. If predictions were generated separately, provide `--predictions <path>` instead.
The script rejects blank labels, duplicate IDs, mismatched case sets, and invalid values.
Cohen's kappa is reported as `null` when it is undefined because expected agreement is 100%.

## Suite

The original Phase 2 YAML suite contains 53 cases across FAQ/account, order status, privacy, and
refund categories. Phase 3 adds 12 safety cases; the current suite has 65 cases and 50 distinct
exact scorer expectation signatures, with 15 additional cases reusing an existing contract with
different wording. Case count and uniqueness are validated by tests; an execution passing the
runner is not itself a quality score.

## Limitations and manual checks

- The seven failure fixtures are curated synthetic examples, not observed model failures.
  Agreement on them checks judge behavior against explicit constructed examples; it does not
  measure failure frequency for a production agent.
- Human agreement figures are meaningful only after a person independently reviews and labels
  the target-run cases; no agreement result is claimed here.
- The judge uses the project's OpenAI-compatible chat configuration; unsupported JSON mode or
  provider behavior is reported rather than silently downgraded.
- Verify the sample with `demo --score`, inspect individual scorer checks, fill the calibration
  labels by human review, then run `--run-judge` only after configuring the intended endpoint.
