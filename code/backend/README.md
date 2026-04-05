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
- payment rail intent generation and onchain sync

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

## Payment rails

This backend now supports two parallel payment rails:

- `HSP rail`: backend creates checkout intent and later ingests webhook confirmation
- `Direct onchain rail`: backend creates a wallet-signable `OrderPaymentRouter` call spec for `USDC` / `USDT`, and later syncs the confirmed tx back into control-plane state

Current direct onchain behavior:

- `USDC` uses `payWithUSDCByAuthorization` (`eip3009`)
- `USDT` uses `payWithUSDT` (`permit2`)
- `PWR` direct pay is intentionally disabled until anchor semantics exist

Direct onchain payment success freezes settlement policy in backend state, but does not emit a duplicate `markOrderPaid` write because escrow has already happened onchain.

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
