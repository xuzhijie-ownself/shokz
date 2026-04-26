# shokz — task runner recipes (`just <recipe>`)
# All recipes assume `uv` is installed (uv handles its own venv).

set shell := ["bash", "-cu"]

# Default: list recipes
default:
    @just --list

# Install deps via uv (fast, locked)
install:
    uv sync --all-extras

# Lint with ruff
lint:
    uv run ruff check src tests

# Auto-fix lint and format
fmt:
    uv run ruff check --fix src tests
    uv run ruff format src tests

# Type-check with mypy --strict
typecheck:
    uv run mypy src/shokz

# Run all unit tests with coverage gate
test:
    uv run pytest

# Run integration tests (gated by INTEGRATION=1)
integration:
    INTEGRATION=1 uv run pytest -m integration

# Run everything that CI runs
ci: lint typecheck test

# Clean caches and build artifacts
clean:
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov build dist *.egg-info
    find . -type d -name __pycache__ -exec rm -rf {} +

# Pre-commit install (one-time)
hooks-install:
    uv run pre-commit install --install-hooks
    uv run pre-commit install --hook-type commit-msg

# Run pre-commit on all files
hooks-run:
    uv run pre-commit run --all-files
