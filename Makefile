.PHONY: dev test lint demo eval-demo run api gate promote db-upgrade dashboard dashboard-install dashboard-test dashboard-lint dashboard-build

dev: demo

test:
	uv run --locked pytest

lint:
	uv run --locked ruff check backend agentprobe_sdk.py
	uv run --locked ruff format --check backend agentprobe_sdk.py
	uv run --locked mypy backend agentprobe_sdk.py
	npm --prefix frontend run lint

dashboard:
	npm --prefix frontend run dev

dashboard-install:
	npm --prefix frontend ci

dashboard-test:
	npm --prefix frontend test

dashboard-lint:
	npm --prefix frontend run lint

dashboard-build:
	npm --prefix frontend run build

demo:
	powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/demo.ps1

eval-demo:
	uv run --locked python -m backend.app.cli demo --score

run:
	uv run --locked python -m backend.app.cli run

api:
	uv run --env-file .env --locked uvicorn backend.app.api:app --reload

gate:
	uv run --locked python -m backend.app.cli gate

promote:
	uv run --locked python -m backend.app.cli promote

db-upgrade:
	uv run --env-file .env --locked alembic upgrade head
