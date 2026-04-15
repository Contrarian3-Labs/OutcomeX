# OutcomeX Backend

`code/backend` is the OutcomeX control plane. It is responsible for turning product intent into orders, payment flows, execution dispatch, and read models while keeping economic truth anchored to contracts.

## What this service owns

- chat-facing plan and quote APIs
- order creation and lifecycle coordination
- payment intent generation for direct pay and HSP checkout
- HSP webhook ingestion, polling, and receipt verification
- execution dispatch into AgentSkillOS
- onchain event indexing and SQL projections
- machine, revenue, settlement, and claim read APIs
- owner-only self-use flows that do not enter buyer settlement

## What this service does not own

The backend is not the final source of truth for:

- payment settlement
- machine ownership
- refunds and claims
- transfer eligibility
- AI orchestration internals

Those live in `code/contracts` and `code/agentskillos`. The backend exists to coordinate them and project their state into a product-facing API.

## Architecture boundary

```text
client -> backend API -> contracts / AgentSkillOS
                     -> SQL projections
```

The key design idea is thin execution plus event-driven finance:

- contracts own economic state
- AgentSkillOS owns delivery execution
- backend owns composition, policy checks, and read models

## Main API areas

- `chat_plans.py` - product-facing plan and quote responses
- `orders.py` - order creation, available actions, confirmation, rejection, refund-trigger flows
- `payments.py` - HSP intents/webhooks/polling plus direct-payment payload generation and sync
- `execution_runs.py` - run status, logs, previews, and artifact access
- `machines.py`, `marketplace.py`, `primary_issuance.py` - machine asset and listing views
- `revenue.py`, `settlement.py` - machine claims, refunds, and treasury-facing revenue views
- `self_use.py` - owner-only execution path outside buyer order settlement

## Payment rails in the current repo

### Official product-facing story

The current product docs treat these as the primary rails:

- `PWR` direct from the user wallet
- `USDC/USDT via HSP`

That is the right README framing for hackathon presentation.

### Compatibility still present in code

The backend also still exposes direct-intent payload generation for `USDC`, `USDT`, and `PWR` through `/payments/orders/{order_id}/direct-intent`.

Treat direct stablecoin support as compatibility and test coverage rather than the main user experience the project should be pitched around.

## Planning and execution boundary

The backend references AgentSkillOS in two distinct ways:

- a fast product-facing plan layer used by the public chat-plan route today
- a deeper bridge and execution service used to call the vendored AgentSkillOS runtime through subprocess isolation

That means the repo already contains the architecture for true downstream planning and execution, while still preserving a simpler product-facing fast path where needed.

## Runtime services

The app starts a small set of background workers around the API server:

- projection repair at startup
- execution sync worker
- HSP payment sync worker
- onchain indexer worker

In `prod`, startup fails fast if the onchain runtime and health checks are unavailable. That is important because the backend is only honest if projections can converge.

## Self-use split

One of the strongest product decisions in this service is the self-use path:

- owner-only
- no buyer order
- no payment
- no settlement or dividend accounting
- execution is still real, but isolated from the market-facing financial model

This prevents internal machine usage from contaminating buyer economics.

## Local development

```bash
cd code/backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Useful endpoint:

```text
GET /api/v1/health
```

## Test commands

Backend tests:

```bash
cd code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests -q
```

Representative smoke path:

```bash
cd code/backend
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 tests/smoke/run_real_business_logic_e2e.py
```

## Related docs

- `../contracts/README.md`
- `../agentskillos/README.md`
- `../../docs/backend-convergence-status-cn.md`
- `../../docs/business-logic-target-decisions-2026-04-07-cn.md`
- `../../docs/e2e-validation-2026-04-09-cn.md`
