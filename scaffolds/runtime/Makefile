SHELL := /bin/bash

.PHONY: up down pull ps logs doctor config auto-update-start auto-update-stop auto-update-status auto-update-check

AUTO_UPDATE_SCRIPT := ./scripts/auto_update.py

up:
	docker compose pull control-plane
	docker compose up -d --no-build
	@python3 $(AUTO_UPDATE_SCRIPT) start --runtime-dir . >/dev/null

down:
	@python3 $(AUTO_UPDATE_SCRIPT) stop --runtime-dir . >/dev/null
	docker compose down --remove-orphans

pull:
	docker compose pull control-plane

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=200

# WARNING: `docker compose config` will expand env vars (including secrets).
# Keep this target redacted to avoid accidental secret disclosure in terminals/logs.
config:
	@docker compose config | sed -E \
		-e 's/(POSTGRES_PASSWORD:).*/\1 ***REDACTED***/' \
		-e 's/(POSTGRES_PWD:).*/\1 ***REDACTED***/' \
		-e 's/(SQL_PASSWORD:).*/\1 ***REDACTED***/' \
		-e 's/(DATABASE_URL:).*/\1 ***REDACTED***/' \
		-e 's/(OPENAI_API_KEY:).*/\1 ***REDACTED***/' \
		-e 's/(GITHUB_TOKEN:).*/\1 ***REDACTED***/' \
		-e 's/(OH_SECRET_KEY:).*/\1 ***REDACTED***/'

doctor:
	@docker --version
	@docker compose version
	@docker compose config -q
	@echo "doctor: OK"

auto-update-start:
	python3 $(AUTO_UPDATE_SCRIPT) start --runtime-dir .

auto-update-stop:
	python3 $(AUTO_UPDATE_SCRIPT) stop --runtime-dir .

auto-update-status:
	python3 $(AUTO_UPDATE_SCRIPT) status --runtime-dir .

auto-update-check:
	python3 $(AUTO_UPDATE_SCRIPT) check --runtime-dir .
