.PHONY: help test ui-test up down demo demo-down backup docs keys
help:        ## list targets
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/\t/'
test:        ## backend test-suite (needs PostgreSQL: see docs/TESTING.md)
	cd backend && python -m pytest -q
ui-test:     ## UI smoke tests (needs a running demo server, see docs/TESTING.md)
	cd frontend/tests/ui-smoke && npm install --silent && node run.mjs
up:          ## production stack
	docker compose up -d --build
down:
	docker compose down
demo:        ## demo mode on http://localhost:8081 (separate DB, fake data)
	docker compose -f docker-compose.demo.yml up -d --build
demo-down:
	docker compose -f docker-compose.demo.yml down -v
backup:      ## verified database backup into ./backups
	./scripts/backup.sh
docs:        ## regenerate API reference + RBAC matrix from code
	python scripts/gen-docs.py
keys:        ## create .env with fresh secrets
	./scripts/gen-secrets.sh
