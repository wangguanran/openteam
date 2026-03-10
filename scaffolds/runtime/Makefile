SHELL := /bin/bash

.PHONY: build up up-build down pull ps logs doctor config

up:
	docker compose up -d --no-build

build:
	docker compose build control-plane

up-build:
	mkdir -p ../team-os/.team-os state/teamos state hub workspace tmp cache
	docker compose up -d --build

down:
	docker compose down

pull:
	docker compose pull

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
