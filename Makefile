.PHONY: db-up db-migrate db-reset

db-up:
	docker compose up -d db

db-migrate:
	uv run python apps/api/scripts/wait_for_db.py
	uv run alembic -c apps/api/alembic.ini upgrade head

db-reset:
	uv run python apps/api/scripts/wait_for_db.py
	uv run alembic -c apps/api/alembic.ini downgrade base
	uv run alembic -c apps/api/alembic.ini upgrade head
