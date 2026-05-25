.PHONY: help install dev test lint format clean bench bench-quick tui dashboard

help:
	@echo "PMB development targets:"
	@echo "  make install      - pip install -e ."
	@echo "  make dev          - install + dev tools (pytest, ruff, textual)"
	@echo "  make test         - run unit tests"
	@echo "  make lint         - ruff check"
	@echo "  make format       - ruff format"
	@echo "  make clean        - remove build artefacts and __pycache__"
	@echo "  make bench        - full LoCoMo benchmark (10 conversations, ~30 min)"
	@echo "  make bench-quick  - quick smoke benchmark (3 conversations, ~3 min)"
	@echo "  make tui          - launch terminal UI"
	@echo "  make dashboard    - launch web dashboard on :8765"

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest tests/test_graph.py tests/test_persons.py tests/test_goals_chains.py \
	       tests/test_fact_tree.py tests/test_recall_cache.py tests/test_config.py \
	       tests/test_redact.py tests/test_causation.py -q

lint:
	ruff check src/ tests/ scripts/

format:
	ruff format src/ tests/ scripts/

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +

bench:
	python scripts/benchmarks/benchmark_locomo.py --n-conversations 10 --top-k 10

bench-quick:
	python scripts/benchmarks/benchmark_locomo.py --n-conversations 3 --top-k 10

tui:
	pmb tui

dashboard:
	pmb dashboard
