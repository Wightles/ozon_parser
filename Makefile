PYTHON ?= python3
PROJECT_PYTHONPATH ?= /private/tmp/ozon-parser-stage13-deps314
RUN_PYTHON = PYTHONPATH="$(PROJECT_PYTHONPATH)" $(PYTHON)
PYTEST ?= $(RUN_PYTHON) -m pytest

.DEFAULT_GOAL := help

.PHONY: help test compile check doctor doctor-local parse parse-csv auth gmail db-up db-ps

help:
	@printf '%s\n' \
		'Available commands:' \
		'  make test          Run the test suite' \
		'  make compile       Compile Python modules' \
		'  make check         Run tests and compile modules' \
		'  make doctor        Check local files, cookies, and PostgreSQL' \
		'  make doctor-local  Check local files and cookies only' \
		'  make parse         Parse configured Ozon SKU values' \
		'  make parse-csv     Parse configured SKU values without PostgreSQL' \
		'  make auth          Refresh Ozon cookies' \
		'  make gmail         Check Gmail OAuth' \
		'  make db-up         Start local PostgreSQL' \
		'  make db-ps         Show local PostgreSQL status'

test:
	@$(PYTEST) -q

compile:
	@$(PYTHON) -m compileall -q .

check: test compile

doctor:
	@$(RUN_PYTHON) main.py doctor

doctor-local:
	@$(RUN_PYTHON) main.py doctor --skip-database

parse:
	@$(RUN_PYTHON) main.py parse

parse-csv:
	@$(RUN_PYTHON) main.py parse --csv-only

auth:
	@$(RUN_PYTHON) main.py auth

gmail:
	@$(RUN_PYTHON) main.py gmail --auth-only

db-up:
	@docker compose up -d postgres

db-ps:
	@docker compose ps
