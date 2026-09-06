# Testing Capabilities — 1p_si2_backend

Generated: 2026-09-05 (sdd-init)

## Strict TDD

**strict_tdd: false**

No explicit workspace-level test command exists and no test runner is installed, so the no-runner fallback applies: strict TDD cannot be activated until a test command covers the project.

## Capability table

| Capability | Status | Detail |
|---|---|---|
| Test runner | ❌ none | pytest NOT installed in `.venv` |
| HTTP test client | ❌ none | httpx NOT installed (required by `fastapi.testclient.TestClient`) |
| Test files | ❌ none | no `tests/` dir, no `conftest.py`, no `test_*.py` in project code |
| Test config | ❌ none | no `pytest.ini`, `pyproject.toml`, or `setup.cfg` |
| Coverage tool | ❌ none | not installed |
| Linter | ❌ none | no ruff/flake8/pylint config or install |
| Type checker | ❌ none | no mypy/pyright config or install |
| Formatter | ❌ none | no black/ruff config |

## Test command

Not yet available. To enable:

```
.venv\Scripts\python.exe -m pip install pytest httpx
.venv\Scripts\python.exe -m pytest
```

## What a working setup would look like

- Runner: pytest, invoked as `.venv\Scripts\python.exe -m pytest` from repo root
- API layer tests: `fastapi.testclient.TestClient` (needs httpx)
- DB layer: tests currently blocked by PostgreSQL requirement in `app/core/database.py`; would need a test DB or SQLite override in `conftest.py`

## Recommendation for the first SDD change

Any change that adds features should bundle (or be preceded by) the pytest + httpx install and a minimal smoke test, so `sdd-verify` has a runnable evidence command. Until then, verification phases have no test evidence available.
