# Phase 1: foundation and runner

## Run locally

From the repository root, install the pinned environment with `uv sync --locked`, then run
`uv run python -m backend.app.cli demo`. The demo uses a deterministic local model and requires no
API key. To use the OpenAI Chat Completions API, set `AGENTPROBE_LLM_PROVIDER=openai` and
`AGENTPROBE_OPENAI_API_KEY` in the environment; optionally set
`AGENTPROBE_OPENAI_BASE_URL` for an OpenAI-compatible endpoint.

`make test` runs the pytest suite. `make lint` runs Ruff lint/format checks and strict mypy.
`make dev` currently aliases the local demo; `make run` uses the adapter selected in
`agentprobe.yaml`. An API development server will be introduced with the API phase.

## Case schema

The YAML loader accepts either one case mapping or a list of mappings. `id` and `input` are
required. `expected_tools` contains tool names and argument mappings;
`expected_answer_contains` contains required answer fragments; `forbidden` entries specify
exactly one `calls_tool` or `reveals` condition. `max_steps` defaults to 5 and must be positive.
Each case may also specify a category and tags. Invalid YAML and invalid case data report the
source file and case index where available.

## Runner behavior

`EvaluationRunner` executes every case for each requested repeat while bounding concurrency.
Timeouts and agent exceptions are retried up to the configured retry count, with exponential
backoff. A final failed attempt is returned as an execution result so one agent failure does not
stop the suite. The trace records each model prompt/response and each tool call's arguments and
result. Providers that do not report usage or pricing leave those trace fields unknown.

The bundled local model makes a deterministic choice from the user's request so the demo can run
without network access or secrets. The configurable OpenAI model path uses `ChatOpenAI` and
supports an OpenAI-compatible base URL.

## Manual verification

- Run `uv run --locked python -m backend.app.cli demo --score` and confirm the local suite runs
  without credentials.
- Set `AGENTPROBE_LLM_PROVIDER=openai` without an API key and confirm the configuration error is
  explicit; do not put a real key in the repository.
- Inspect a returned execution trace and confirm it includes an LLM prompt and tool call details.
- Confirm `delete_order` is registered only as a safe stub and never changes an order.
- Try loading malformed YAML and a case with both forbidden condition fields; confirm the error
  identifies the source.

## Known limitations

AgentProbe records traces and execution outcomes but does not yet persist them to a database or
redact PII from tool results. Those capabilities are scheduled for later phases. Completed Python
SDK events are retained for agent exceptions and timeouts; Git sandbox traces are returned when
the container exits normally.

## Phase 1.5: adapters and tracing SDK

`backend.app.adapters.AgentAdapter` is the common async interface. `PythonModuleAdapter` runs
trusted local project code in a subprocess; `HttpAdapter` maps inputs and outputs over HTTP;
`GitRepoAdapter` clones a public GitHub HTTPS URL, installs dependencies from the project
manifest, and invokes either a `module:function` entrypoint or a local HTTP service in Docker.
Python and Git targets may emit step events through the dependency-free `agentprobe_sdk` module;
HTTP targets may map trace steps in their response; otherwise only the final answer is available.

Git repository execution requires a caller-provided Docker network with externally enforced
egress restrictions for GitHub and the package index. The adapter adds container limits,
non-root execution, read-only root filesystem, a bounded temporary filesystem, no host mounts,
and no inherited host environment. Provision a sandbox image containing Python 3, Git, and pip
using `docker/agent-sandbox.Dockerfile`.
The adapter does not create a firewall or verify Docker network policy; it fails closed when the
network name is absent or a Docker built-in network (`bridge`, `host`, `none`) is selected. Public
repository execution supports Python module entrypoints or a local HTTP service launched from the
clone; HTTP targets can also be used independently through `type: http`.

Example HTTP target:

```yaml
target:
  type: http
  url: https://agent.example.test/run
  request_mapping:
    prompt: case_input
  response_mapping:
    final_answer: result.answer
    steps: trace.steps
    tokens: usage.total_tokens
    cost_usd: usage.cost_usd
  auth_header: Authorization
  auth_env: SUPPORT_AGENT_TOKEN
  timeout_seconds: 20
```

Run the configured suite with `uv run --locked python -m backend.app.cli run`.

Example Git repository that starts a local HTTP service:

```yaml
target:
  type: git_repo
  repo_url: https://github.com/example/support-agent.git
  delegate_type: http
  service_command: [python, -m, uvicorn, app:app, --host, 127.0.0.1, --port, "8000"]
  service_url: http://127.0.0.1:8000/run
  docker_network: agentprobe-egress-allowlisted
  request_mapping:
    prompt: case_input
  response_mapping:
    final_answer: answer
    steps: trace.steps
  timeout_seconds: 300
```
