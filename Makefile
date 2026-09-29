.DEFAULT_GOAL := help
MANAGE := uv run python manage.py

.PHONY: help install lint format typecheck test check requirements migrate run worker reconcile up down bench

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-14s %s\n", $$1, $$2}'

install: ## Create .venv with runtime and dev dependencies
	uv sync --all-extras

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format: ## Apply fixes and formatting
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy, strict
	uv run mypy .

test: ## Run the test suite (needs Postgres; see README)
	uv run pytest

check: lint typecheck test ## Everything CI runs

requirements: ## Re-export the hash-pinned requirements.txt that CI and Docker install
	uv export --no-dev --all-extras --no-emit-project --format requirements-txt -o requirements.txt

migrate: ## Apply migrations
	$(MANAGE) migrate

run: ## Serve the API on :8000 with Django's dev server
	$(MANAGE) runserver 8000

worker: ## Publish outbox events until stopped
	$(MANAGE) outbox_worker

reconcile: ## Check every ledger invariant
	$(MANAGE) reconcile

up: ## Start api, worker, Postgres and Redis in Docker
	docker compose up -d --build

down: ## Stop the stack and delete its volumes
	docker compose down -v

bench: ## Load-test transfers with k6 against the compose stack (TOKEN=... SCENARIO=spread|hot)
	docker run --rm --network ledger-service_default -v "$(CURDIR)/bench:/bench" \
		-e BASE_URL=http://api:8000 -e TOKEN -e SCENARIO -e VUS -e DURATION \
		grafana/k6:latest run /bench/transfers.js
