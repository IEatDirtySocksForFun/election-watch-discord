# Common tasks. Run `make` on its own to see this list.
#
#   make install     create .venv and install the package with its dev dependencies
#   make test        fast unit tests: no network, no MongoDB
#   make test-mongo  the same plus the MongoDB store suite (needs a MongoDB server)
#   make sim         quick election-night command simulation, bundled data, ~15 s
#   make sim-full    the full simulation on downloaded history data (slow)
#   make health      ask every results source what it can see right now (uses the network)
#   make lint        what CI's lint job checks: ruff (Python) and Prettier (Markdown)
#   make format      fix what `make lint` complains about
#
# Recipes use .venv/bin/python when it exists, otherwise python3. No make available
# (Windows)? tests/README.md lists the same commands as plain python/pytest lines.
#
# sim-full needs the timelines that tools/fetch_history.py writes to
# ~/Downloads/election-test-data, plus a MongoDB server for three of the scenarios (and a mongod
# binary for the restart test). For an end-to-end check of the bot's commands without that
# dataset, use `make sim`.

PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYTEST = $(PY) -m pytest
YEARS ?= 2022,2024
MONGO_URI ?= mongodb://127.0.0.1:27017
MONGOD_BIN ?= $(shell command -v mongod 2>/dev/null)

.PHONY: help install test test-mongo sim sim-full health lint format

help:
	@echo "make install     create .venv, then pip install -e .[dev]"
	@echo "make test        unit tests: no network, no MongoDB (~20 s)"
	@echo "make test-mongo  unit tests plus the MongoDB store suite"
	@echo "                   no server? docker run -d -p 27017:27017 mongo:8.0"
	@echo "                   another server? EW_TEST_MONGO_URI=mongodb://host:27017 make test-mongo"
	@echo "make sim         quick command simulation, no history data needed (~15 s)"
	@echo "make sim-full    full simulation on fetched timelines (years: YEARS=2022,2024)"
	@echo "make health      probe every results source (network)"
	@echo "make lint        ruff check + ruff format --check + Prettier on the .md files"
	@echo "make format      ruff --fix, ruff format, Prettier --write (needs npm for Prettier)"

install:
	python3 -m venv .venv
	.venv/bin/python -m pip install -e ".[dev]"

test:
	$(PYTEST) -q

test-mongo:
	@uri="$${EW_TEST_MONGO_URI:-mongodb://127.0.0.1:27017}"; \
	echo "MongoDB store suite against $$uri"; \
	echo "No server yet? Start one in the background with: docker run -d -p 27017:27017 mongo:8.0"; \
	EW_TEST_MONGO_URI="$$uri" $(PY) -c 'import os; from pymongo import MongoClient; MongoClient(os.environ["EW_TEST_MONGO_URI"], serverSelectionTimeoutMS=2000).admin.command("ping")' 2>/dev/null \
	  || { echo "Nothing answered at $$uri - start MongoDB first (see the line above)."; exit 1; }; \
	EW_TEST_MONGO_URI="$$uri" EW_TEST_ADMIN_MONGO_URI="$$uri" $(PYTEST) -q

sim:
	$(PY) -m replay.command_sim --quick

sim-full:
	@test -d "$$HOME/Downloads/election-test-data/timelines" \
	  || { echo "No timelines in ~/Downloads/election-test-data yet."; \
	       echo "Run tools/fetch_history.py first, or use 'make sim' for a data-free check."; \
	       exit 1; }
	@echo "All 17 scenarios for $(YEARS). The three MongoDB ones need a server at $(MONGO_URI)"
	@echo "(docker run -d -p 27017:27017 mongo:8.0) and the restart test needs a mongod binary."
	$(PY) -m replay.command_sim --years $(YEARS) --races top \
	  $(if $(MONGO_URI),--mongo-uri $(MONGO_URI)) \
	  $(if $(MONGOD_BIN),--mongod-bin "$(MONGOD_BIN)")

health:
	$(PY) tools/health_check.py

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	npm run --silent format:check

format:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .
	npm run --silent format
