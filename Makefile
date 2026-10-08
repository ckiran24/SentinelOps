.PHONY: up down logs check test eval smoke reset-demo

up:
	docker compose up --build -d --wait

down:
	docker compose down

logs:
	docker compose logs -f api worker

check:
	docker compose config --quiet
	cd apps/web && npm run typecheck && npm run build

test:
	python -m pytest tests apps/api/tests -q

eval:
	cd apps/api && python -m sentinelops.evaluate --output ../../evals/results/latest.json

smoke:
	python infra/smoke_test.py

# This deliberately removes local demo data. Ordinary `down` keeps it.
reset-demo:
	docker compose down --volumes
	docker compose up --build -d --wait
