# OutcomeX Backend

This backend is the OutcomeX control plane.
It is no longer responsible for AI capability routing, model selection, or solution orchestration.

## Current boundary

OutcomeX backend owns:

- chat-native product APIs
- order, payment, settlement, and revenue state
- machine transfer guards and control-plane projections
- the thin submission boundary into AgentSkillOS
- deterministic write-chain payload generation

AgentSkillOS owns:

- capability understanding
- skill retrieval
- orchestration and planning
- model/script/tool invocation
- delivery artifacts

The thin execution contract persisted by OutcomeX is:

```json
{
  "intent": "user outcome request",
  "files": ["input files"],
  "execution_strategy": "quality | efficiency | simplicity"
}
```

## Stack

- FastAPI for API surface
- SQLAlchemy 2.0 for ORM models/session
- Alembic-ready migration scaffolding
- Pytest for backend verification

## Structure

```text
code/backend
├── alembic/
├── app/
│   ├── api/
│   ├── core/
│   ├── db/
│   ├── domain/
│   ├── execution/      # Thin execution boundary types/services
│   ├── integrations/   # AgentSkillOS bridge + HSP adapter boundary
│   ├── onchain/        # Deterministic write-chain payload layer
│   └── runtime/
├── tests/
├── alembic.ini
└── pyproject.toml
```

## Product truths captured here

- Users buy outcomes, not workflow internals
- Settlement starts only after result confirmation
- Revenue split is fixed at 10% platform / 90% machine side
- Owner self-use is not dividend-eligible
- Machine transfer is blocked by active tasks or unsettled revenue

## Local run

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
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests -q
```
