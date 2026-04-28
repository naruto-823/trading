.PHONY: setup dev dev-backend dev-frontend test lint sync

setup:
	@echo "==> Setting up backend..."
	cd backend && uv sync
	@echo "==> Setting up frontend..."
	cd frontend && pnpm install
	@if [ ! -f .env ]; then cp .env.example .env && echo "==> Created .env from .env.example — please fill in your keys"; fi
	@mkdir -p backend/data
	@echo "==> Done! Edit .env then run: make dev"

dev:
	@make dev-backend & make dev-frontend & wait

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:
	cd frontend && pnpm dev

test:
	cd backend && uv run pytest -v
	cd frontend && pnpm test

lint:
	cd backend && uv run ruff check . && uv run mypy .
	cd frontend && pnpm lint

sync:
	cd backend && uv run python -m app.cli sync --all
