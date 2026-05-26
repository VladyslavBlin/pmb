.PHONY: help install dev test test-core test-smoke test-all-WARN lint format clean bench bench-quick tui dashboard

help:
	@echo "PMB development targets:"
	@echo "  make install        - pip install -e ."
	@echo "  make dev            - install + dev tools (pytest, ruff, textual)"
	@echo ""
	@echo "  make test           - alias for test-core (CI baseline, 88 tests, ~80s)"
	@echo "  make test-core      - the 8 deterministic core test files from .github/workflows/ci.yml"
	@echo "  make test-smoke     - lightweight import smoke tests only (~5s)"
	@echo "  make test-all-WARN  - full pytest tests/  (KNOWN ISSUE: hangs on huggingface_hub parallel"
	@echo "                        downloads when the embedding model isn't cached; only run if you"
	@echo "                        have the HF cache populated)"
	@echo ""
	@echo "  make lint           - ruff check"
	@echo "  make format         - ruff format"
	@echo "  make clean          - remove build artefacts and __pycache__"
	@echo "  make bench          - full LoCoMo benchmark (10 conversations, ~30 min)"
	@echo "  make bench-quick    - quick smoke benchmark (3 conversations, ~3 min)"
	@echo "  make tui            - launch terminal UI"
	@echo "  make dashboard      - launch web dashboard on :8765"

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

# --------------------------------------------------------------------------
# Tests. The default `test` target intentionally runs ONLY the 8 core files
# used by CI. Running `pytest tests/` directly is known to deadlock on first
# run because several test modules trigger parallel huggingface_hub model
# downloads against the same cache directory. Until that is fixed, use:
#   - `make test`        for the deterministic CI baseline
#   - `make test-smoke`  for the import-weight smoke tests
#   - `make test-all-WARN` only if your HF cache is already populated
# --------------------------------------------------------------------------

test: test-core

test-core:
	pytest tests/test_graph.py tests/test_persons.py tests/test_goals_chains.py \
	       tests/test_fact_tree.py tests/test_recall_cache.py tests/test_config.py \
	       tests/test_redact.py tests/test_causation.py -q

test-smoke:
	pytest tests/test_lightweight_imports.py -v

test-all-WARN:
	@echo "WARNING: full pytest is known to deadlock on parallel HF downloads."
	@echo "         If this hangs >60s, Ctrl-C and run 'make test-core' instead."
	pytest tests/ -q

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
