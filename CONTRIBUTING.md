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
pip install -r requirements.lock
```

## Testing

Run the test suite with:

```bash
python -m pytest -q
```

## Code style

- Keep code readable and maintain consistency with the existing style.
- Prefer descriptive variable names and small functions.
- Add or update tests for new behavior.
