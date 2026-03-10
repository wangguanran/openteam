SHELL := /bin/bash
COMPOSE := bash ./scripts/compose.sh

.PHONY: build up up-build down pull ps logs doctor config

up:
	$(COMPOSE) up -d --no-build

build:
	docker compose build control-plane

up-build:
	mkdir -p ../team-os/.team-os state/teamos state hub workspace tmp cache
	$(COMPOSE) up -d --build

down:
	COMPOSE_PROFILES=localdb docker compose down --remove-orphans

pull:
	$(COMPOSE) pull

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200

# WARNING: `docker compose config` will expand env vars (including secrets).
# Keep this target redacted to avoid accidental secret disclosure in terminals/logs.
config:
	@$(COMPOSE) config | sed -E \
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
	@$(COMPOSE) config -q
	@echo "doctor: OK"
