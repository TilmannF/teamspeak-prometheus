# Development entry points. No TeamSpeak server is required for any of these.

PYTHON ?= python3
VENV := .venv
# Extra flags for run-fake, e.g. RUN_FAKE_ARGS="--iterations 2 --interval 0.2"
RUN_FAKE_ARGS ?=
BIN := $(VENV)/bin

.PHONY: help setup lint format check test test-smoke run-fake docker-build clean

help:
	@echo "setup         create $(VENV) and install runtime + dev dependencies"
	@echo "lint          ruff check"
	@echo "format        ruff format"
	@echo "check         lint + format check + unit tests"
	@echo "test          unit tests (no sockets, no subprocesses)"
	@echo "test-smoke    end-to-end test against the fake ServerQuery server"
	@echo "run-fake      run the exporter against the fake ServerQuery server"
	@echo "docker-build  build the container image"

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

setup: $(BIN)/python
	$(BIN)/pip install -r requirements.txt -r requirements-dev.txt

lint: $(BIN)/python
	$(BIN)/ruff check .

format: $(BIN)/python
	$(BIN)/ruff format .

test: $(BIN)/python
	$(BIN)/pytest -m "not smoke"

test-smoke: $(BIN)/python
	$(BIN)/pytest -m smoke

check: lint test
	$(BIN)/ruff format --check .

run-fake: $(BIN)/python
	$(BIN)/python -m tests.exporter_harness $(RUN_FAKE_ARGS)

docker-build:
	docker build -t teamspeak-prometheus:dev .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
