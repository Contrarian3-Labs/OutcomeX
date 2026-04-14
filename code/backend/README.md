# OutcomeX Backend

This backend is the OutcomeX control plane.
It is no longer responsible for AI capability routing, model selection, or solution orchestration.

## Current boundary

OutcomeX backend owns:

- chat-native product APIs
- order, payment, settlement, and revenue state
- machine transfer guards and chain-projected ownership semantics
- the thin submission boundary into AgentSkillOS
- deterministic write-chain payload generation
- payment rail intent generation and verifier-backed onchain sync

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

- `HSP rail`: backend creates checkout intent and later syncs merchant status by webhook and/or backend polling
- `Direct onchain rail`: backend creates a wallet-signable `OrderPaymentRouter` call spec for `USDC` / `USDT` / `PWR`, and later syncs the confirmed tx back into control-plane state

Current direct onchain behavior:

- `USDC` uses `payWithUSDCByAuthorization` (`eip3009`)
- `USDT` uses `payWithUSDT` (`erc20_approve`)
- `PWR` uses `payWithPWR` (`erc20_approve`)

Current PWR anchor behavior:

- quote math is deterministic and versioned in backend `RuntimeCostService`
- `pwr_quote` and `pwr_anchor_price_cents` are returned together
- the current anchor is a minimal backend-priced anchor, not a market oracle

Direct onchain payment sync is now correlation-only:

- backend verifies the receipt / wallet / router target
- backend stores tx correlation and callback evidence
- final paid state and settlement policy are projected from indexed onchain lifecycle events

Additional hardening now in place:

- direct onchain sync uses a verifier boundary instead of trusting caller-reported success
- direct-pay success requires a real decodable `OrderCreated` event
- machine ownership is projected from chain events; transfer intent is no longer a product API boundary
- runtime admission occupancy is shared across service instances via a container-managed simulator
- when `OUTCOMEX_ENV=prod`, app startup now fails fast if the onchain runtime/indexer is unavailable or unhealthy

## Local run

```bash
cd code/backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

AgentSkillOS runtime defaults:

- backend prefers vendored `code/agentskillos` automatically when `OUTCOMEX_AGENTSKILLOS_ROOT` is unset
- optionally set `OUTCOMEX_AGENTSKILLOS_PYTHON_EXECUTABLE=/absolute/path/to/python` when the AgentSkillOS runtime should use a dedicated interpreter instead of the backend venv

Local browser demo entrypoint:

- from repo root, use `scripts/start_local_browser_demo.sh`
- the script now forces backend startup to use vendored `code/agentskillos` even if a local `.env` still points at the legacy external checkout
- if vendored `code/agentskillos/.venv` exists, the script also exports `OUTCOMEX_AGENTSKILLOS_PYTHON_EXECUTABLE` automatically
- detailed current status / non-fully-live notes: `LOCAL_BROWSER_DEMO_CN.md`
- local backend template env: `code/backend/.env.local-demo.example`
- local browser demo backend port: `127.0.0.1:8787`

Health endpoint:

```text
GET /api/v1/health
```

Production note:

- `prod` no longer silently degrades to `NullOnchainIndexer`
- deploy with a reachable RPC + valid indexer subscriptions + healthy onchain config, or startup will fail fast

HashKey testnet runtime note:

- `code/backend/.env.hashkey-testnet.example` now includes the live HashKey testnet contract addresses plus the HSP polling/env knobs needed for a real `USDT via HSP` rollout
- current recommended merchant rollout remains `USDT`-only on HashKey testnet; `OUTCOMEX_HSP_SUPPORTED_CURRENCIES=USDT`
- if webhook stays disabled, keep `OUTCOMEX_HSP_POLL_ENABLED=true` and rely on `POST /api/v1/payments/{payment_id}/sync-hsp` plus background polling

## Test

```bash
cd code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests -q
```
