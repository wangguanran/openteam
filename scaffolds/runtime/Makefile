SHELL := /bin/bash

.PHONY: up down pull ps logs doctor config up-image down-image pull-image ps-image logs-image config-image

up:
	mkdir -p ../team-os/.team-os state/teamos state hub workspace tmp cache
	docker compose up -d

down:
	docker compose down

pull:
	docker compose pull

pull-image:
	docker compose -f docker-compose.image.yml pull

ps:
	docker compose ps

ps-image:
	docker compose -f docker-compose.image.yml ps

logs:
	docker compose logs -f --tail=200

logs-image:
	docker compose -f docker-compose.image.yml logs -f --tail=200

up-image:
	docker compose -f docker-compose.image.yml up -d

down-image:
	docker compose -f docker-compose.image.yml down

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

config-image:
	@docker compose -f docker-compose.image.yml config | sed -E \
		-e 's/(TEAMOS_DB_URL:).*/\1 ***REDACTED***/' \
		-e 's/(OPENAI_API_KEY:).*/\1 ***REDACTED***/' \
		-e 's/(GITHUB_TOKEN:).*/\1 ***REDACTED***/'

doctor:
	@docker --version
	@docker compose version
	@docker compose config -q
	@echo "doctor: OK"
