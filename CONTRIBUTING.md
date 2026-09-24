# Contributing to ClawMux

Thank you for your interest in contributing to ClawMux.

## How to contribute

- Open an issue for bugs, feature requests, or improvements.
- Fork the repository and create a branch for your work.
- Keep changes focused and submit a pull request with a clear description.

## Development setup

```bash
git clone <repo-url>
cd ClawMux
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Quality gates

CI enforces the following — run them locally before pushing:

```bash
python -m ruff check src tests scripts   # lint
python -m ruff format --check src tests scripts
python -m mypy                           # type check (src only; tests are exempt)
python -m pytest -q --cov                # tests + coverage gate (fail_under in pyproject.toml)
```

Tooling config lives in `pyproject.toml` (ruff rules, mypy, pytest, coverage).

## Testing

Run the test suite with:

```bash
python -m pytest -q
```

## Code style

- Formatting and imports are enforced by `ruff format` / `ruff check` (line length 120, rule sets E4/E7/E9/F/I/UP/B).
- Prefer descriptive variable names and small functions.
- Add or update tests for new behavior.
