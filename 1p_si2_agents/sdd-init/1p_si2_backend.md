# SDD Init — 1p_si2_backend

Generated: 2026-09-05
Source: automated stack/convention/testing detection (sdd-init)

## Stack

- Language: Python 3.14.7 (venv at `.venv/`)
- Framework: FastAPI 0.141.1 (ASGI, served by uvicorn 0.52.4)
- ORM: SQLAlchemy 2.0.52 (declarative, `Base` in `app/models/base.py`)
- Migrations: alembic 1.19.1 installed (no `alembic/` directory exists yet)
- Database: PostgreSQL 15 via psycopg2-binary 2.9.12
- Validation: Pydantic 2.13.5
- Auth: JWT via PyJWT (`import jwt` in `app/core/security.py`), password hashing via passlib + bcrypt
- Config: python-dotenv (`.env` at repo root)
- Dependency management: pip + `requirements.txt` (note: file is UTF-16 encoded on disk)

## Architecture

Layered architecture, single FastAPI app (`app/main.py`). Actual structure (authoritative over docs):

```
app/
├── api/
│   ├── dependencies.py      # injectable deps (db session, auth)
│   └── v1/
│       ├── api_router.py
│       ├── auth.py           # /api/v1/auth
│       ├── sucursales.py     # /api/v1/sucursales
│       └── usuarios.py       # /api/v1/usuarios
├── core/
│   ├── config.py
│   ├── database.py
│   └── security.py           # JWT create/verify, password hashing
├── models/                   # SQLAlchemy models (base, rol, sucursal, usuario)
├── schemas/                  # Pydantic schemas (auth, sucursal, usuario)
├── services/                 # business logic (auth_service, sucursal_service, usuario_service)
└── main.py                   # app factory + router registration
```

Pattern: router → service → model, with schemas for request/response DTOs. Services hold business logic; routers are thin.

## Doc-vs-code drift (verified discrepancies)

`backend.md` (the project's agent guide) has drifted from the actual code. Later SDD phases must treat CODE as authoritative:

1. **JWT library**: `backend.md` says python-jose; actual code uses PyJWT (`app/core/security.py`). python-jose is NOT installed.
2. **Folder layout**: `backend.md` says `app/routers/`; actual code uses `app/api/v1/`. Routers live in `app/api/v1/`.
3. **Python version**: `backend.md` says 3.11+; actual venv runs 3.14.7.
4. **Table creation**: `app/main.py` calls `Base.metadata.create_all()` — no Alembic migrations directory exists despite alembic being installed.

## Conventions

- Tables: snake_case plural (`usuarios`, `roles`, `sucursales`)
- Models: PascalCase singular (`Usuario`, `Rol`, `Sucursal`)
- Schemas: `{Entidad}Create` / `{Entidad}Update` / `{Entidad}Response`
- Routes: `/api/v1/{modulo}` prefix
- Services: `{entidad}_service.py`
- Comments: Spanish (existing codebase convention)
- Response format: `{"status": "success", "data": ..., "message": ...}` / `{"status": "error", "detail": ..., "code": ...}`
- Passwords: bcrypt hash before persist; never expose `password` in response schemas

## Testing capability

- **Status: NONE.** No pytest, no test files, no `conftest.py`, no test config (`pytest.ini` / `pyproject.toml` / `setup.cfg` absent).
- httpx (needed for FastAPI `TestClient`) is NOT installed.
- No test directory exists. Only `.venv` site-packages contain third-party test code.
- **Test command: not yet available.** To enable: `pip install pytest httpx` then run `.venv\Scripts\python.exe -m pytest`.

## Strict TDD support

**strict_tdd: false**

Reason: no testing capability is installed. There is no runnable test command, so red-green-refactor cycles cannot be executed. Strict TDD can be activated in a future change once pytest + httpx are installed and a smoke test runs green.

## SDD Session Preflight (this session)

- execution_mode: `auto`
- artifact_store: custom file-based store at `1p_si2_agents/` (user choice: "Guarda todos los artefactos en la carpeta 1p_si2_agents/")
- delivery_strategy: `single-pr`
- review_budget_lines: 800

## Next steps

1. `/sdd-explore` or `/sdd-new` for the first change (Ciclo 1 use cases from `backend.md` are the roadmap)
2. First change should consider installing pytest + httpx to enable verification
3. Reconcile `backend.md` doc drift (routers layout, JWT library) or adopt code as truth
