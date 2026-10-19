"""Local API route, auth, persistence, and request-id integration tests."""

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import api
from backend.app.comparison import ModelComparisonResult, ModelMetrics
from backend.app.core.config import Settings
from backend.app.db.database import Base, create_session_factory
from backend.app.db.models import RunRecord, TargetRecord
from backend.app.runner.models import AgentResult


class FakeAdapter:
    async def run(
        self,
        case_input: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_steps: int = 5,
    ) -> AgentResult:
        return AgentResult(final_answer="A safe local test response.")


@pytest.fixture
def app_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Engine]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = create_session_factory(engine)
    monkeypatch.setattr(api, "make_adapter", lambda *_args, **_kwargs: FakeAdapter())
    app = api.create_app(
        Settings(api_key="local-test-secret"),
        engine=engine,
        session_factory=sessions,
    )
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_api_runs_persist_and_expose_summary_and_trace(
    app_client: tuple[TestClient, Engine],
) -> None:
    client, _ = app_client
    headers = {"Authorization": "Bearer local-test-secret", "X-Request-ID": "test-request"}

    health = client.get("/health")
    assert health.json() == {"status": "ok"}
    assert health.headers["X-Request-ID"]
    assert client.get("/ready").json() == {"status": "ready"}

    unauthorized = client.post("/runs", json={"case_ids": ["faq-account"]})
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "request_error"
    assert unauthorized.headers["X-Request-ID"]

    created = client.post(
        "/runs",
        headers=headers,
        json={"suite": "all", "case_ids": ["faq-account"]},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert created.json()["summary"]["cases"] == 1

    runs = client.get("/runs?limit=1&offset=0")
    assert runs.status_code == 200
    assert runs.json()["total"] == 1
    assert runs.json()["items"][0]["id"] == run_id
    assert client.get(f"/runs/{run_id}").json()["id"] == run_id
    assert client.get(f"/runs/{run_id}/summary").json()["cases"] == 1
    assert client.get(f"/runs/{run_id}/summary").json()["average_latency_ms"] >= 0.0
    assert len(client.get(f"/runs/{run_id}/cases").json()) == 1
    trace = client.get(f"/runs/{run_id}/cases/faq-account/trace")
    assert trace.status_code == 200
    assert trace.json()["repeats"][0]["execution"]["case_id"] == "faq-account"
    assert client.get(f"/compare?a={run_id}&b={run_id}").json()["pass_rate_delta"] == 0.0
    promoted = client.post(f"/baselines/{run_id}", headers=headers)
    assert promoted.status_code == 201
    assert client.get("/baseline").json()["id"] == run_id
    assert client.get(f"/compare-baseline?run_id={run_id}").status_code == 200


def test_model_comparison_is_auth_protected_and_persisted(
    app_client: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = app_client
    metrics = ModelMetrics(
        model="test-model",
        cases=1,
        passed=1,
        failed=0,
        accuracy=1.0,
        average_latency_ms=10.0,
        total_tokens=10,
        total_cost_usd=None,
        cost_per_100_cases_usd=None,
        failure_types={},
    )
    comparison = ModelComparisonResult(
        id="",
        created_at="",
        suite="all",
        repeats=1,
        primary=metrics,
        comparison=metrics.model_copy(update={"model": "gemini-test"}),
        primary_cases=[],
        comparison_cases=[],
    )

    async def fake_compare_models(**_kwargs: object) -> ModelComparisonResult:
        return comparison

    monkeypatch.setattr(api, "compare_models", fake_compare_models)
    unauthorized = client.post("/comparisons", json={"case_ids": ["faq-account"]})
    assert unauthorized.status_code == 401

    response = client.post(
        "/comparisons",
        headers={"Authorization": "Bearer local-test-secret"},
        json={"case_ids": ["faq-account"]},
    )
    assert response.status_code == 201
    comparison_id = response.json()["id"]
    assert response.json()["primary"]["model"] == "test-model"
    assert client.get("/comparisons").json()["items"][0]["id"] == comparison_id
    assert (
        client.get(f"/comparisons/{comparison_id}").json()["comparison"]["model"] == "gemini-test"
    )


def test_api_errors_are_consistent_and_baseline_requires_auth(
    app_client: tuple[TestClient, Engine],
) -> None:
    client, _ = app_client

    missing = client.get("/runs/missing-run")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "request_error"
    assert missing.json()["error"]["request_id"] == missing.headers["X-Request-ID"]

    invalid = client.post(
        "/runs",
        headers={"Authorization": "Bearer local-test-secret"},
        json={"suite": "../outside"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"

    created = client.post(
        "/runs",
        headers={"Authorization": "Bearer local-test-secret"},
        json={"case_ids": ["faq-account"]},
    )
    run_id = created.json()["id"]
    forbidden = client.post(f"/baselines/{run_id}")
    assert forbidden.status_code == 401
    assert forbidden.json()["error"]["code"] == "request_error"


def test_suite_case_builder_crud_import_review_and_custom_run(
    app_client: tuple[TestClient, Engine],
) -> None:
    client, _ = app_client
    headers = {"Authorization": "Bearer local-test-secret"}
    case_payload = {
        "case": {
            "id": "builder-case",
            "input": "say hello",
            "expected_answer_contains": ["safe local"],
            "expected_tools": [],
            "category": "greeting",
            "target_agent": "support",
            "max_steps": 2,
        }
    }

    assert client.post("/suite-cases", json=case_payload).status_code == 401
    created = client.post("/suite-cases", headers=headers, json=case_payload)
    assert created.status_code == 201
    assert created.json()["status"] == "approved"
    assert created.json()["provenance"] == "manual"
    assert created.json()["human_reviewed"] is True

    updated_payload = {
        "case": {
            **case_payload["case"],
            "input": "say hello again",
        }
    }
    updated = client.put(
        "/suite-cases/builder-case",
        headers=headers,
        json=updated_payload,
    )
    assert updated.status_code == 200
    assert updated.json()["case"]["input"] == "say hello again"

    duplicated = client.post("/suite-cases/builder-case/duplicate", headers=headers)
    assert duplicated.status_code == 201
    assert duplicated.json()["status"] == "draft"
    assert duplicated.json()["human_reviewed"] is False

    invalid = client.post(
        "/suite-cases/import",
        headers=headers,
        json={
            "format": "yaml",
            "content": "- id: invalid-case\n  input: hello\n  unsupported: true\n",
        },
    )
    assert invalid.status_code == 422
    assert "line 1" in str(invalid.json()["error"]["message"])

    imported = client.post(
        "/suite-cases/import",
        headers=headers,
        json={"format": "json", "content": '[{"id":"imported-case","input":"hello"}]'},
    )
    assert imported.status_code == 200
    assert imported.json()["items"][0]["provenance"] == "manual"

    stats = client.get("/suite-cases/stats")
    assert stats.json()["total"] == 3
    assert stats.json()["approved"] == 2
    assert stats.json()["draft"] == 1
    assert stats.json()["agents"] == {"support": 2}

    run = client.post("/runs", headers=headers, json={"suite": "custom"})
    assert run.status_code == 201
    assert run.json()["summary"]["cases"] == 2

    reviewed = client.post(
        f"/suite-cases/{duplicated.json()['id']}/review",
        headers=headers,
        json={"approved": True},
    )
    assert reviewed.json()["status"] == "approved"
    exported = client.get("/suite-cases/export?format=yaml")
    assert exported.status_code == 200
    assert "builder-case" in exported.text
    removed = client.delete(f"/suite-cases/{duplicated.json()['id']}", headers=headers)
    assert removed.status_code == 204


def test_suite_pack_discovery_includes_universal_safety_pack(
    app_client: tuple[TestClient, Engine],
) -> None:
    client, _ = app_client

    packs = client.get("/suite-packs").json()["items"]

    safety_pack = next(pack for pack in packs if pack["id"] == "safety")
    assert safety_pack["name"] == "Universal safety pack"
    assert safety_pack["cases"] >= 10
    copied = client.post(
        "/suite-cases/from-pack/safety",
        headers={"Authorization": "Bearer local-test-secret"},
        json={},
    )
    assert copied.status_code == 201
    assert copied.json()["total"] == safety_pack["cases"]
    assert all(case["status"] == "draft" for case in copied.json()["items"])
    assert all(case["provenance"] == "template" for case in copied.json()["items"])


def test_generated_cases_are_saved_as_unapproved_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from types import SimpleNamespace

    import langchain_openai

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    target_id = "3eac9aa5-6e3c-4c6a-9b5e-90ee697c392d"
    with sessions() as session:
        session.add(
            TargetRecord(
                id=target_id,
                name="generator-test",
                entry_point="agent:run",
                input_mapping_json='{"mode":"text"}',
                output_mapping_json="{}",
                env_json="{}",
                agent_names_json='["support"]',
            )
        )
        session.commit()

    generated_cases = [
        {
            "id": f"generated-{index}",
            "input": f"Test input {index}",
            "expected_tools": [{"name": "lookup", "args": {"query": f"query-{index}"}}],
            "expected_answer_contains": [],
            "category": "generated",
            "target_agent": "support",
        }
        for index in range(20)
    ]

    class FakeChatOpenAI:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def ainvoke(self, _prompt: str) -> SimpleNamespace:
            return SimpleNamespace(content=json.dumps({"cases": generated_cases}))

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)
    app = api.create_app(
        Settings(
            api_key="local-test-secret",
            llm_provider="openai",
            model_name="test-generator",
            openai_api_key="provider-test-key",
        ),
        engine=engine,
        session_factory=sessions,
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/suite-cases/generate",
                headers={"Authorization": "Bearer local-test-secret"},
                json={"target_id": target_id, "count": 20, "tools": ["lookup"]},
            )
            assert response.status_code == 200
            assert response.json()["total"] == 20
            assert all(item["status"] == "draft" for item in response.json()["items"])
            assert all(not item["human_reviewed"] for item in response.json()["items"])

            custom_run = client.post(
                "/runs",
                headers={"Authorization": "Bearer local-test-secret"},
                json={"suite": "custom"},
            )
            assert custom_run.status_code == 201
            assert custom_run.json()["summary"]["cases"] == 0
    finally:
        engine.dispose()


def test_api_applies_alembic_migrations_to_configured_local_database(tmp_path: Path) -> None:
    database_path = tmp_path / "migrated.db"
    database_url = URL.create("sqlite", database=str(database_path)).render_as_string(
        hide_password=False
    )
    app = api.create_app(Settings(database_url=database_url))

    with TestClient(app) as client:
        assert client.get("/ready").json() == {"status": "ready"}

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {"alembic_version", "runs", "baselines", "model_comparisons", "targets"} <= tables


def test_target_upload_inspection_dry_run_and_delete_are_local_and_redacted(
    app_client: tuple[TestClient, Engine],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine = app_client
    targets_root = tmp_path / "targets"
    monkeypatch.setattr(api, "_TARGETS_ROOT", targets_root)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(
            "sample-agent/agent.py",
            "from langgraph.graph import StateGraph\n"
            "workflow = StateGraph(dict)\n"
            "def run(value): return {'answer': {'text': value}}\n",
        )
        zipped.writestr("sample-agent/.env", "MUST_NOT_EXTRACT=hidden")
    archive.seek(0)
    headers = {"Authorization": "Bearer local-test-secret"}
    missing_auth = client.post(
        "/targets",
        files={"file": ("sample.zip", archive.getvalue(), "application/zip")},
        data={"entry_point": "agent:run"},
    )
    assert missing_auth.status_code == 401

    created = client.post(
        "/targets",
        headers=headers,
        files={"file": ("sample.zip", archive.getvalue(), "application/zip")},
        data={
            "entry_point": "agent:run",
            "env_json": '{"API_TOKEN":"hidden-value"}',
        },
    )
    assert created.status_code == 201
    target = created.json()
    target_id = target["id"]
    assert target["entry_point"] == "agent:run"
    assert "env_json" not in target
    assert "hidden-value" not in created.text
    project_path = targets_root / target_id
    assert (project_path / "agent.py").is_file()
    assert not (project_path / ".env").exists()

    with engine.connect() as connection:
        saved_environment: str = connection.execute(
            text("SELECT env_json FROM targets WHERE id = :target_id"),
            {"target_id": target_id},
        ).scalar_one()
    assert "hidden-value" in saved_environment

    inspection = client.post(f"/targets/{target_id}/inspect", headers=headers).json()
    assert inspection["frameworks"] == ["LangGraph"]
    assert inspection["candidates"][0]["name"] == "workflow"
    assert inspection["notice"].startswith("Static heuristics")
    manual_inspection = client.post(
        f"/targets/{target_id}/inspect",
        headers=headers,
        json={"agent_names": ["support", "billing", "hidden-value-agent"]},
    )
    expected_names = ["support", "billing", "[REDACTED]-agent"]
    assert manual_inspection.json()["agent_names"] == expected_names
    target_list = client.get("/targets")
    assert target_list.json()["items"][0]["agent_names"] == expected_names
    assert "hidden-value" not in target_list.text

    observed: dict[str, Any] = {}

    async def fake_dry_run_target(**kwargs: Any) -> AgentResult:
        observed.update(kwargs)
        mock_result = observed.get("mock_result")
        if isinstance(mock_result, AgentResult):
            return mock_result
        return AgentResult(final_answer="secret is hidden-value")

    monkeypatch.setattr(api, "dry_run_target", fake_dry_run_target)
    dry_run = client.post(
        f"/targets/{target_id}/dry-run",
        headers=headers,
        json={"input": "hello"},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["succeeded"] is True
    assert dry_run.json()["final_answer"] == "secret is [REDACTED]"
    assert dry_run.json()["trace_available"] is False
    assert dry_run.json()["notice"] == "per-agent analysis unavailable; add the SDK to enable it."
    assert observed["env"] == {"API_TOKEN": "hidden-value"}
    assert "hidden-value" not in dry_run.text

    observed["mock_result"] = AgentResult(
        final_answer="",
        error="ModuleNotFoundError: No module named 'missing_package'",
    )
    failed_dry_run = client.post(
        f"/targets/{target_id}/dry-run",
        headers=headers,
        json={"input": "hello"},
    )
    assert failed_dry_run.json()["succeeded"] is False
    assert "import failed" in failed_dry_run.json()["error"]
    assert "ModuleNotFoundError" not in failed_dry_run.text

    deleted = client.delete(f"/targets/{target_id}", headers=headers)
    assert deleted.status_code == 204
    assert not project_path.exists()
    assert client.get("/targets").json()["items"] == []


def test_target_upload_rejects_zip_slip_and_invalid_entry_point(
    app_client: tuple[TestClient, Engine],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = app_client
    targets_root = tmp_path / "targets"
    monkeypatch.setattr(api, "_TARGETS_ROOT", targets_root)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escape.py", "unsafe")
    archive.seek(0)
    headers = {"Authorization": "Bearer local-test-secret"}
    response = client.post(
        "/targets",
        headers=headers,
        files={"file": ("unsafe.zip", archive.getvalue(), "application/zip")},
        data={"entry_point": "agent:run"},
    )
    assert response.status_code == 422
    assert not (tmp_path / "escape.py").exists()
    invalid_entry = client.post(
        "/targets",
        headers=headers,
        files={"file": ("safe.zip", io.BytesIO().getvalue(), "application/zip")},
        data={"entry_point": "../agent:run"},
    )
    assert invalid_entry.status_code == 422


def test_uploaded_target_dry_run_executes_in_its_own_venv_with_sdk_trace(
    app_client: tuple[TestClient, Engine],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = app_client
    targets_root = tmp_path / "targets"
    monkeypatch.setattr(api, "_TARGETS_ROOT", targets_root)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(
            "agent.py",
            "import os\n"
            "from agentprobe_sdk import traced_agent, traced_tool\n"
            "@traced_tool\n"
            "def echo(text): return text\n"
            "@traced_agent(name='support')\n"
            "def run(text):\n"
            "    return {'result': echo(text) + ':' + os.environ['AGENTPROBE_API_KEY']}\n",
        )
    archive.seek(0)
    created = client.post(
        "/targets",
        headers={"Authorization": "Bearer local-test-secret"},
        files={"file": ("sample.zip", archive.getvalue(), "application/zip")},
        data={
            "entry_point": "agent:run",
            "env_json": '{"AGENTPROBE_API_KEY":"local-project-secret"}',
            "input_mapping_json": '{"mode":"text","parameter":"text"}',
            "output_mapping_json": '{"final_answer":"result"}',
        },
    )
    assert created.status_code == 201
    target_id = created.json()["id"]

    response = client.post(
        f"/targets/{target_id}/dry-run",
        headers={"Authorization": "Bearer local-test-secret"},
        json={"input": "hello"},
    )

    assert response.status_code == 200
    assert response.json()["succeeded"] is True
    assert response.json()["final_answer"] == "hello:[REDACTED]"
    assert response.json()["trace_available"] is True
    assert response.json()["notice"] is None
    assert "local-project-secret" not in response.text
    assert (targets_root / ".venvs" / target_id / "Scripts" / "python.exe").is_file() or (
        targets_root / ".venvs" / target_id / "bin" / "python"
    ).is_file()

    assert (
        client.delete(
            f"/targets/{target_id}",
            headers={"Authorization": "Bearer local-test-secret"},
        ).status_code
        == 204
    )


def test_api_stamps_existing_pre_migration_local_database(tmp_path: Path) -> None:
    database_path = tmp_path / "existing.db"
    database_url = URL.create("sqlite", database=str(database_path)).render_as_string(
        hide_password=False
    )
    existing_engine = create_engine(database_url)
    Base.metadata.create_all(existing_engine)
    with existing_engine.begin() as connection:
        connection.execute(text("DROP TABLE targets"))
        connection.execute(text("DROP TABLE model_comparisons"))
        connection.execute(text("DROP TABLE suite_cases"))
        connection.execute(
            text(
                "INSERT INTO runs "
                "(id, created_at, suite, status, summary_json, results_json) "
                "VALUES (:id, :created_at, :suite, :status, :summary_json, :results_json)"
            ),
            {
                "id": "legacy-run",
                "created_at": "2026-01-01T00:00:00",
                "suite": "all",
                "status": "completed",
                "summary_json": "{}",
                "results_json": "[]",
            },
        )
    existing_engine.dispose()
    app = api.create_app(Settings(database_url=database_url))

    with TestClient(app) as client:
        assert client.get("/ready").json() == {"status": "ready"}

    migrated_engine = create_engine(database_url)
    try:
        with migrated_engine.connect() as connection:
            legacy_run = connection.scalar(text("SELECT id FROM runs WHERE id = 'legacy-run'"))
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        migrated_tables = set(inspect(migrated_engine).get_table_names())
    finally:
        migrated_engine.dispose()
    assert legacy_run == "legacy-run"
    assert revision == "20261017_0004"
    assert {"model_comparisons", "targets", "suite_cases"} <= migrated_tables


def test_api_rate_limits_write_requests() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = api.create_app(
        Settings(api_key="local-test-secret", api_rate_limit_per_minute=1),
        engine=engine,
        session_factory=create_session_factory(engine),
    )

    try:
        with TestClient(app) as client:
            first = client.post("/baselines/unknown-run")
            second = client.post("/baselines/unknown-run")
    finally:
        engine.dispose()
    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limit_exceeded"


def test_calibration_endpoints(
    app_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = app_client
    sample_report = {
        "cases": 10,
        "overall_pass_agreement_pct": 80.0,
        "cohen_kappa": 0.6,
        "correctness_agreement_pct": 90.0,
        "faithfulness_agreement_pct": 85.0,
        "meets_target": True,
        "target_agreement_pct": 80.0,
        "disagreements": [
            {
                "case_id": "c1",
                "human_correct": True,
                "human_faithful": True,
                "judge_correct": False,
                "judge_faithful": True,
                "human_passed": True,
                "judge_passed": False,
            }
        ],
    }

    # Test GET when calibration exists
    monkeypatch.setattr(api, "get_latest_calibration", lambda: sample_report)
    res = client.get("/calibration")
    assert res.status_code == 200
    data = res.json()
    assert data["cases"] == 10
    assert data["overall_pass_agreement_pct"] == 80.0
    assert len(data["disagreements"]) == 1
    assert data["disagreements"][0]["case_id"] == "c1"

    # Test GET when no calibration exists
    monkeypatch.setattr(api, "get_latest_calibration", lambda: None)
    res_none = client.get("/calibration")
    assert res_none.status_code == 404

    # Test POST /calibration/run
    async def _fake_perform() -> dict[str, Any]:
        return sample_report

    monkeypatch.setattr(api, "perform_calibration", _fake_perform)
    run_res = client.post(
        "/calibration/run",
        headers={"Authorization": "Bearer local-test-secret"},
    )
    assert run_res.status_code == 200
    assert run_res.json()["cases"] == 10


def test_run_cases_and_summary_contain_per_agent_and_attribution(
    app_client: tuple[TestClient, Engine],
) -> None:
    client, engine = app_client
    persisted_results = [
        {
            "case_id": "order-1",
            "target_agent": "support_agent",
            "execution": {
                "case_id": "order-1",
                "repeat_index": 0,
                "output": "Processed",
                "duration_ms": 150.0,
                "attempts": 1,
                "success": True,
                "steps": [
                    {
                        "kind": "tool",
                        "agent_name": "support_agent",
                        "tool_name": "lookup",
                        "latency_ms": 50.0,
                        "cost_usd": 0.001,
                        "input_tokens": 10,
                        "output_tokens": 20,
                    },
                    {
                        "kind": "handoff",
                        "agent_name": "support_agent",
                        "handoff_to": "billing_agent",
                        "latency_ms": 10.0,
                    },
                    {
                        "kind": "tool",
                        "agent_name": "billing_agent",
                        "tool_name": "charge",
                        "latency_ms": 90.0,
                        "cost_usd": 0.002,
                        "input_tokens": 15,
                        "output_tokens": 25,
                    },
                ],
            },
            "score": {"passed": True, "failure_categories": []},
            "safety": {"passed": True},
        },
        {
            "case_id": "order-2",
            "target_agent": "billing_agent",
            "execution": {
                "case_id": "order-2",
                "repeat_index": 0,
                "output": None,
                "error": "Payment gateway error",
                "duration_ms": 200.0,
                "attempts": 1,
                "success": False,
                "steps": [
                    {
                        "kind": "tool",
                        "agent_name": "billing_agent",
                        "tool_name": "charge",
                        "latency_ms": 100.0,
                        "error": "Card declined",
                    }
                ],
            },
            "score": {"passed": False, "failure_categories": ["execution_error"]},
            "safety": {"passed": True},
        },
    ]

    summary = {
        "cases": 2,
        "passed": 1,
        "failed": 1,
        "pass_rate": 0.5,
        "safety": {
            "cases": 0,
            "attack_cases": 0,
            "blocked_attacks": 0,
            "attack_block_rate": None,
            "benign_cases": 0,
            "benign_false_positives": 0,
            "false_positive_rate": None,
            "pii_leak_cases": 0,
        },
        "average_latency_ms": 175.0,
        "total_tokens": 70,
        "total_cost_usd": 0.003,
        "cost_per_100_cases_usd": 0.15,
        "failure_types": {"execution_error": 1},
        "per_agent": {
            k: v.model_dump(mode="json")
            for k, v in api._per_agent_summary(persisted_results).items()
        },
    }

    with Session(engine) as session:
        session.add(
            RunRecord(
                id="multi-agent-run",
                suite="all",
                status="completed",
                summary_json=json.dumps(summary),
                results_json=json.dumps(persisted_results),
            )
        )
        session.commit()

    # Verify /runs/{id}/cases
    cases_res = client.get("/runs/multi-agent-run/cases")
    assert cases_res.status_code == 200
    cases_data = cases_res.json()
    assert len(cases_data) == 2

    case_1 = next(c for c in cases_data if c["case_id"] == "order-1")
    assert case_1["passed"] is True
    assert set(case_1["agents_involved"]) == {"support_agent", "billing_agent"}
    assert case_1["first_failing_agent"] is None

    case_2 = next(c for c in cases_data if c["case_id"] == "order-2")
    assert case_2["passed"] is False
    assert case_2["agents_involved"] == ["billing_agent"]
    assert case_2["first_failing_agent"] == "billing_agent"

    # Verify /runs/{id}/summary
    summary_res = client.get("/runs/multi-agent-run/summary")
    assert summary_res.status_code == 200
    summary_data = summary_res.json()
    per_agent = summary_data["per_agent"]
    assert "support_agent" in per_agent
    assert "billing_agent" in per_agent

    assert per_agent["support_agent"]["cases_involved"] == 1
    assert per_agent["support_agent"]["passed"] == 1
    assert per_agent["support_agent"]["first_failing_cases"] == 0

    assert per_agent["billing_agent"]["cases_involved"] == 2
    assert per_agent["billing_agent"]["passed"] == 1
    assert per_agent["billing_agent"]["failed"] == 1
    assert per_agent["billing_agent"]["first_failing_cases"] == 1
    assert per_agent["billing_agent"]["failure_types"] == {"execution_error": 1}


def test_gate_check_endpoint_and_baseline_safety_rejection(
    app_client: tuple[TestClient, Engine],
) -> None:
    client, engine = app_client
    session = Session(engine)
    headers = {"Authorization": "Bearer local-test-secret"}

    # 1. Run with safety failure cannot be promoted
    unsafe_run = RunRecord(
        id="unsafe-run-1",
        suite="default",
        status="completed",
        summary_json=json.dumps(
            {
                "cases": 1,
                "passed": 1,
                "failed": 0,
                "pass_rate": 1.0,
                "average_duration_ms": 100.0,
                "total_cost": 0.01,
                "safety": {"attack_block_rate": 0.0, "pii_leak_cases": 1},
            }
        ),
        results_json=json.dumps(
            [
                {
                    "execution": {
                        "case_id": "c1",
                        "success": True,
                        "duration_ms": 100,
                        "steps": [],
                    },
                    "score": {"passed": True, "failure_categories": []},
                    "safety": {
                        "passed": False,
                        "attack_blocked": False,
                        "is_attack": True,
                        "pii_leaks": ["api_key"],
                    },
                }
            ]
        ),
    )
    session.add(unsafe_run)
    session.commit()

    rej = client.post("/baselines/unsafe-run-1", headers=headers)
    assert rej.status_code == 409
    assert "safety failures" in rej.json()["error"]["message"]

    # 2. Safe baseline run can be promoted
    safe_baseline = RunRecord(
        id="safe-baseline-1",
        suite="default",
        status="completed",
        summary_json=json.dumps(
            {
                "cases": 2,
                "passed": 2,
                "failed": 0,
                "pass_rate": 1.0,
                "average_duration_ms": 100.0,
                "total_cost": 0.01,
                "safety": {
                    "cases": 2,
                    "attack_cases": 2,
                    "blocked_attacks": 2,
                    "attack_block_rate": 1.0,
                    "benign_cases": 0,
                    "benign_false_positives": 0,
                    "false_positive_rate": 0.0,
                    "pii_leak_cases": 0,
                },
            }
        ),
        results_json=json.dumps(
            [
                {
                    "execution": {
                        "case_id": "c1",
                        "success": True,
                        "duration_ms": 100,
                        "steps": [{"agent_name": "bot"}],
                    },
                    "score": {"passed": True, "failure_categories": []},
                    "safety": {
                        "passed": True,
                        "attack_blocked": True,
                        "is_attack": True,
                        "pii_leaks": [],
                    },
                },
                {
                    "execution": {
                        "case_id": "c2",
                        "success": True,
                        "duration_ms": 100,
                        "steps": [{"agent_name": "bot"}],
                    },
                    "score": {"passed": True, "failure_categories": []},
                    "safety": {
                        "passed": True,
                        "attack_blocked": True,
                        "is_attack": True,
                        "pii_leaks": [],
                    },
                },
            ]
        ),
    )
    session.add(safe_baseline)
    session.commit()

    prom = client.post("/baselines/safe-baseline-1", headers=headers)
    assert prom.status_code == 201

    # 3. Candidate run with 1 regression
    cand_run = RunRecord(
        id="cand-run-1",
        suite="default",
        status="completed",
        summary_json=json.dumps(
            {
                "cases": 2,
                "passed": 1,
                "failed": 1,
                "pass_rate": 0.5,
                "average_duration_ms": 100.0,
                "total_cost": 0.01,
                "safety": {
                    "cases": 2,
                    "attack_cases": 2,
                    "blocked_attacks": 2,
                    "attack_block_rate": 1.0,
                    "benign_cases": 0,
                    "benign_false_positives": 0,
                    "false_positive_rate": 0.0,
                    "pii_leak_cases": 0,
                },
            }
        ),
        results_json=json.dumps(
            [
                {
                    "execution": {
                        "case_id": "c1",
                        "success": True,
                        "duration_ms": 100,
                        "steps": [{"agent_name": "bot"}],
                    },
                    "score": {"passed": True, "failure_categories": []},
                    "safety": {
                        "passed": True,
                        "attack_blocked": True,
                        "is_attack": True,
                        "pii_leaks": [],
                    },
                },
                {
                    "execution": {
                        "case_id": "c2",
                        "success": False,
                        "duration_ms": 100,
                        "steps": [{"agent_name": "bot"}],
                    },
                    "score": {"passed": False, "failure_categories": ["wrong_tool"]},
                    "safety": {
                        "passed": True,
                        "attack_blocked": True,
                        "is_attack": True,
                        "pii_leaks": [],
                    },
                },
            ]
        ),
    )
    session.add(cand_run)
    session.commit()

    # GET /gate/check
    gate_res = client.get("/gate/check?run_id=cand-run-1")
    assert gate_res.status_code == 200
    gate_data = gate_res.json()
    assert gate_data["passed"] is False
    assert gate_data["summary_counts"]["regressed"] == 1
    assert gate_data["summary_counts"]["passing"] == 1
    assert gate_data["cases"][0]["case_id"] == "c2"
    assert gate_data["cases"][0]["bucket"] == "regressed"
    assert "bot" in gate_data["per_agent_regressions"]
    assert gate_data["per_agent_regressions"]["bot"]["agent_name"] == "bot"

    # GET /compare also embeds gate evaluation
    comp_res = client.get("/compare?a=safe-baseline-1&b=cand-run-1")
    assert comp_res.status_code == 200
    comp_data = comp_res.json()
    assert comp_data["gate"] is not None
    assert comp_data["gate"]["passed"] is False
