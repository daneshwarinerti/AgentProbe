"""Integration tests for runner options, progress reporting, and cancellation via API."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import api
from backend.app.core.config import Settings
from backend.app.db.database import Base, create_session_factory
from backend.app.db.models import TargetRecord
from backend.app.runner.models import AgentResult, TraceStep


class FakeAgentWithTrace:
    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        case_input: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_steps: int = 5,
    ) -> AgentResult:
        self.calls += 1
        return AgentResult(
            final_answer=f"Processed: {case_input}",
            steps=[
                TraceStep(
                    kind="llm",
                    latency_ms=12.5,
                    input_tokens=100,
                    output_tokens=50,
                )
            ],
        )


@pytest.fixture
def app_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Engine]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = create_session_factory(engine)
    monkeypatch.setattr(api, "make_adapter", lambda *_args, **_kwargs: FakeAgentWithTrace())
    app = api.create_app(
        Settings(api_key="local-test-secret"),
        engine=engine,
        session_factory=sessions,
    )
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_run_with_custom_runner_options_and_cost_enrichment(
    app_client: tuple[TestClient, Engine],
) -> None:
    client, _ = app_client
    headers = {"Authorization": "Bearer local-test-secret"}

    response = client.post(
        "/runs",
        headers=headers,
        json={
            "suite": "all",
            "model": "gpt-4o-mini",
            "temperature": 0.5,
            "repeats": 1,
            "concurrency": 2,
            "timeout_seconds": 15.0,
            "call_spacing_seconds": 0.01,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "completed"
    assert data["summary"]["cases"] > 0
    run_id = data["id"]

    # Verify run cases and trace steps have estimated cost
    cases_res = client.get(f"/runs/{run_id}/cases", headers=headers)
    assert cases_res.status_code == 200
    cases_data = cases_res.json()
    assert len(cases_data) > 0
    first_case = cases_data[0]
    assert first_case["total_cost_usd"] is not None
    assert first_case["total_cost_usd"] > 0

    trace_res = client.get(f"/runs/{run_id}/cases/{first_case['case_id']}/trace", headers=headers)
    assert trace_res.status_code == 200
    trace_data = trace_res.json()
    trace_steps = trace_data["repeats"][0]["execution"]["steps"]
    assert len(trace_steps) > 0
    llm_step = next(s for s in trace_steps if s["kind"] == "llm")
    assert llm_step["cost_usd"] is not None
    assert llm_step["cost_usd"] > 0


def test_run_progress_and_cancel_endpoints(
    app_client: tuple[TestClient, Engine],
) -> None:
    client, _ = app_client
    headers = {"Authorization": "Bearer local-test-secret"}

    # First run a suite
    run_res = client.post(
        "/runs",
        headers=headers,
        json={"suite": "all", "repeats": 1},
    )
    assert run_res.status_code == 201
    run_id = run_res.json()["id"]

    # Check progress endpoint
    prog_res = client.get(f"/runs/{run_id}/progress", headers=headers)
    assert prog_res.status_code == 200
    prog = prog_res.json()
    assert prog["run_id"] == run_id
    assert prog["total_cases"] > 0
    assert prog["completed_cases"] == prog["total_cases"]
    assert prog["is_running"] is False

    # Cancel endpoint
    cancel_res = client.post(f"/runs/{run_id}/cancel", headers=headers)
    assert cancel_res.status_code == 200
    assert cancel_res.json()["run_id"] == run_id
    assert cancel_res.json()["cancelled"] is True


def test_run_progress_and_cancel_not_found(
    app_client: tuple[TestClient, Engine],
) -> None:
    client, _ = app_client
    headers = {"Authorization": "Bearer local-test-secret"}

    prog_res = client.get("/runs/non-existent-run-id/progress", headers=headers)
    assert prog_res.status_code == 404

    cancel_res = client.post("/runs/non-existent-run-id/cancel", headers=headers)
    assert cancel_res.status_code == 404


def test_run_with_target_id(
    app_client: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    import shutil
    import uuid

    client, engine = app_client
    headers = {"Authorization": "Bearer local-test-secret"}
    target_id = str(uuid.uuid4())
    project_dir = api._TARGETS_ROOT / target_id
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Insert a target record in DB
        sessions: sessionmaker[Session] = create_session_factory(engine)
        with sessions() as session:
            target = TargetRecord(
                id=target_id,
                name="test-target",
                entry_point="agent:run",
                env_json="{}",
                input_mapping_json='{"mode":"text","parameter":"input"}',
                output_mapping_json='{"final_answer":"output"}',
                setup_status="ready",
            )
            session.add(target)
            session.commit()

        # Mock prepare_target_environment and PythonModuleAdapter
        monkeypatch.setattr(
            api,
            "prepare_target_environment",
            AsyncMock(return_value=("fake_python", None)),
        )
        monkeypatch.setattr(
            api,
            "PythonModuleAdapter",
            lambda *args, **kwargs: FakeAgentWithTrace(),
        )

        run_res = client.post(
            "/runs",
            headers=headers,
            json={
                "target_id": target_id,
                "repeats": 1,
                "concurrency": 1,
            },
        )
        assert run_res.status_code == 201
        data = run_res.json()
        assert data["status"] == "completed"
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
