# Backend Payments Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the real backend payment control plane for HSP checkout, webhook processing, unified chain writes, and runtime-based quoting.

**Architecture:** Keep API routes thin. Move pricing into `RuntimeCostService`, merchant checkout into a real `HSPAdapter`, and all business write-chain actions into `order_writer.py` so callbacks, payment confirmation, preview ready, and settlement all share one path.

**Tech Stack:** FastAPI, SQLAlchemy, web3 adapter boundary, HSP merchant flow, pytest

---

### Task 1: Add `RuntimeCostService` and quote outputs

**Files:**
- Create: `code/backend/app/runtime/cost_service.py`
- Create: `code/backend/app/schemas/quote.py`
- Modify: `code/backend/app/api/routes/chat_plans.py`
- Modify: `code/backend/app/api/routes/payments.py`
- Test: `code/backend/tests/runtime/test_cost_service.py`

- [ ] Add a service that outputs runtime cost, official quote, PWR quote, platform fee, and machine share.
- [ ] Add failing tests for baseline quote math and deterministic output shape.
- [ ] Wire quote output into plan/payment routes without breaking current schemas.
- [ ] Run focused pytest coverage for the new runtime tests.
- [ ] Commit the quote service.

### Task 2: Replace mock HSP with real adapter + webhook ingestion

**Files:**
- Modify: `code/backend/app/integrations/hsp_adapter.py`
- Create: `code/backend/app/api/routes/hsp_webhooks.py`
- Modify: `code/backend/app/api/router.py`
- Modify: `code/backend/app/domain/models.py`
- Test: `code/backend/tests/api/test_hsp_webhooks.py`

- [ ] Replace mock-only adapter methods with real merchant-order request/response shapes while preserving a testable adapter boundary.
- [ ] Add webhook parsing, signature verification hook, and idempotency behavior.
- [ ] Persist HSP merchant identifiers and callback state.
- [ ] Add failing and passing tests for duplicate callbacks and successful settlement-policy freeze.
- [ ] Commit the HSP adapter + webhook layer.

### Task 3: Add `order_writer.py` and route backend business writes through it

**Files:**
- Create: `code/backend/app/onchain/order_writer.py`
- Create: `code/backend/app/onchain/contracts_registry.py`
- Modify: `code/backend/app/api/routes/payments.py`
- Modify: `code/backend/app/api/routes/orders.py`
- Modify: `code/backend/app/api/routes/settlement.py`
- Test: `code/backend/tests/onchain/test_order_writer.py`

- [ ] Add a single backend write-chain service for create-order, mark-paid, mark-preview-ready, confirm-result, and settle-order.
- [ ] Move webhook-success and payment-success flows onto this writer.
- [ ] Add route-level tests proving callback success triggers a write-chain attempt with frozen settlement classification.
- [ ] Run focused backend pytest suites.
- [ ] Commit the unified write-chain control plane.
