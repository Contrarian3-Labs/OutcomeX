# OutcomeX Backend (First Pass)

This directory contains the first-pass Python backend skeleton for OutcomeX.
It is intentionally lightweight, with extension points ready for future
execution-engine and on-chain indexer integrations.

## Stack

- FastAPI for API surface
- SQLAlchemy 2.0 for ORM models/session
- Alembic-ready migration scaffolding
- Pytest for basic API/domain verification

## Structure

```text
code/backend
├── alembic/                   # Migration environment and version scripts
├── app/
│   ├── api/                   # REST routers and dependency wiring
│   ├── core/                  # Settings and dependency container
│   ├── db/                    # ORM base + session integration
│   ├── domain/                # Enums, models, and domain rules
│   └── integrations/          # HSP mock adapter + extension boundaries
├── tests/                     # Small verification suite
├── alembic.ini
└── pyproject.toml
```

## Product Constraints Captured in This Skeleton

- Chat-native product surface (`/chat/plans`)
- Orders expose recommended plans only, not workflow internals
- Settlement can only start after result confirmation
- Revenue split is fixed at 10% platform / 90% machine side
- Self-use revenue is not dividend-eligible
- Machine transfer is blocked by active tasks or unsettled revenue

## Local Run

```bash
cd code/backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Health endpoint:

```text
GET /api/v1/health
```

## Test

```bash
cd code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider
```

## Migrations (Alembic)

```bash
cd code/backend
alembic revision --autogenerate -m "init"
alembic upgrade head
```

