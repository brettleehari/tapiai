.PHONY: help install test lint format catalog build clean

help:
	@echo "Targets:"
	@echo "  install   Install the package with dev extras (editable)"
	@echo "  test      Run the test suite"
	@echo "  lint      Run ruff (if installed)"
	@echo "  format    Format with ruff (if installed)"
	@echo "  catalog   Regenerate the endpoint catalog from vendored OAS specs"
	@echo "  build     Build sdist + wheel"
	@echo "  clean     Remove build/test artifacts"

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest -q

lint:
	@command -v ruff >/dev/null 2>&1 && ruff check tapi_overlay tools tests || echo "ruff not installed; skipping"

format:
	@command -v ruff >/dev/null 2>&1 && ruff format tapi_overlay tools tests || echo "ruff not installed; skipping"

catalog:
	python tools/gen_catalog.py

build:
	python -m build

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
