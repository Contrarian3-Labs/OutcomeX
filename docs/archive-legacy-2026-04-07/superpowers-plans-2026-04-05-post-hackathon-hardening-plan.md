# Post-Hackathon Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the current OutcomeX MVP by binding backend orders to real on-chain order identifiers, validating direct-pay sync from chain evidence, projecting machine ownership from chain semantics, and making hardware admission state shared across requests.

**Architecture:** Keep the existing product API surface, but replace mock-style trust boundaries with verifiable state transitions. Backend remains the control plane, contracts remain the settlement truth, and AgentSkillOS remains the execution kernel. New hardening code should slot into current routes, models, and integrations rather than introducing a parallel stack.

**Tech Stack:** FastAPI, SQLAlchemy, Pytest, Foundry, web3-style adapter boundaries already present in repo

---

## File Structure

### Backend payment / chain verification slice
- Modify: `code/backend/app/domain/models.py`
- Modify: `code/backend/app/api/routes/orders.py`
- Modify: `code/backend/app/api/routes/payments.py`
- Modify: `code/backend/app/schemas/order.py`
- Modify: `code/backend/app/schemas/payment.py`
- Modify: `code/backend/app/onchain/order_writer.py`
- Create or modify: `code/backend/app/integrations/onchain_payment_verifier.py`
- Test: `code/backend/tests/api/test_direct_payments_api.py`
- Test: `code/backend/tests/onchain/test_order_writer.py`
- Test: `code/backend/tests/api/test_orders_api.py` or new targeted file

### Backend ownership projection slice
- Modify: `code/backend/app/api/routes/machines.py`
- Modify: `code/backend/app/domain/models.py`
- Modify: `code/backend/app/schemas/machine.py`
- Modify: `code/backend/app/indexer/projections.py`
- Create or modify: `code/backend/app/integrations/machine_ownership_projection.py`
- Test: `code/backend/tests/api/test_machines_api.py`
- Test: `code/backend/tests/indexer/*`

### Execution capacity slice
- Modify: `code/backend/app/execution/service.py`
- Modify: `code/backend/app/api/deps.py`
- Modify: `code/backend/app/core/container.py`
- Modify: `code/backend/app/runtime/hardware_simulator.py`
- Test: `code/backend/tests/execution/test_execution_boundary.py`
- Test: `code/backend/tests/execution/test_execution_service_wrapper_integration.py`

### Documentation slice
- Modify: `docs/code-audit-callchain-cn.md`
- Modify: `docs/backend-contract-interface-map-cn.md`
- Modify: `code/backend/README.md`

---

### Task 1: Bind backend orders to on-chain order ids and make direct intent chain-addressable

**Files:**
- Modify: `code/backend/app/domain/models.py`
- Modify: `code/backend/app/api/routes/orders.py`
- Modify: `code/backend/app/api/routes/payments.py`
- Modify: `code/backend/app/schemas/order.py`
- Modify: `code/backend/app/schemas/payment.py`
- Modify: `code/backend/app/onchain/order_writer.py`
- Test: `code/backend/tests/api/test_direct_payments_api.py`
- Test: `code/backend/tests/onchain/test_order_writer.py`

- [ ] **Step 1: Write the failing tests**
- [ ] **Step 2: Run focused backend tests and verify they fail**
- [ ] **Step 3: Add `onchain_order_id` and related metadata fields to backend models / schemas**
- [ ] **Step 4: Update order creation + direct intent payloads so direct-pay routes use the chain-facing order identifier**
- [ ] **Step 5: Re-run focused backend tests and verify they pass**
- [ ] **Step 6: Commit**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_direct_payments_api.py tests/onchain/test_order_writer.py -q`

Commit message: `feat: bind backend orders to onchain order ids`

### Task 2: Replace trusted `/sync-onchain` state writes with verifier-backed chain evidence checks

**Files:**
- Create or modify: `code/backend/app/integrations/onchain_payment_verifier.py`
- Modify: `code/backend/app/api/routes/payments.py`
- Modify: `code/backend/app/core/container.py`
- Modify: `code/backend/app/api/deps.py`
- Test: `code/backend/tests/api/test_direct_payments_api.py`
- Test: `code/backend/tests/integrations/test_onchain_payment_verifier.py`

- [ ] **Step 1: Write failing tests for tx verification, buyer/token/amount/order matching, and reject-on-mismatch behavior**
- [ ] **Step 2: Run focused verifier/payment tests and verify they fail**
- [ ] **Step 3: Implement verifier boundary and wire `/sync-onchain` to verified event evidence instead of trusting caller-provided success**
- [ ] **Step 4: Re-run focused tests and verify they pass**
- [ ] **Step 5: Commit**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_direct_payments_api.py tests/integrations/test_onchain_payment_verifier.py -q`

Commit message: `feat: verify direct payments from chain evidence`

### Task 3: Make machine ownership a chain-projected truth instead of a backend-only write

**Files:**
- Modify: `code/backend/app/api/routes/machines.py`
- Modify: `code/backend/app/domain/models.py`
- Modify: `code/backend/app/schemas/machine.py`
- Modify: `code/backend/app/indexer/projections.py`
- Create or modify: `code/backend/app/integrations/machine_ownership_projection.py`
- Test: `code/backend/tests/api/test_machines_api.py`
- Test: `code/backend/tests/indexer/test_machine_projection.py`

- [ ] **Step 1: Write failing tests that prove transfer API no longer silently mutates canonical owner without chain-backed projection state**
- [ ] **Step 2: Run focused machine/indexer tests and verify they fail**
- [ ] **Step 3: Implement projected ownership semantics and downgrade direct transfer route to intent/mock-only semantics where needed**
- [ ] **Step 4: Re-run focused tests and verify they pass**
- [ ] **Step 5: Commit**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_machines_api.py tests/indexer -q`

Commit message: `feat: project machine ownership from chain state`

### Task 4: Make hardware admission state shared across requests

**Files:**
- Modify: `code/backend/app/execution/service.py`
- Modify: `code/backend/app/api/deps.py`
- Modify: `code/backend/app/core/container.py`
- Modify: `code/backend/app/runtime/hardware_simulator.py`
- Test: `code/backend/tests/execution/test_execution_boundary.py`
- Test: `code/backend/tests/execution/test_execution_service_wrapper_integration.py`

- [ ] **Step 1: Write failing tests proving occupancy survives across service instances / requests**
- [ ] **Step 2: Run focused execution tests and verify they fail**
- [ ] **Step 3: Introduce shared simulator dependency / container-managed state and minimal reset hooks for tests**
- [ ] **Step 4: Re-run focused tests and verify they pass**
- [ ] **Step 5: Commit**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/execution/test_execution_boundary.py tests/execution/test_execution_service_wrapper_integration.py -q`

Commit message: `feat: share hardware admission state across requests`

### Task 5: Refresh call-chain and hardening docs, then run full verification

**Files:**
- Modify: `docs/code-audit-callchain-cn.md`
- Modify: `docs/backend-contract-interface-map-cn.md`
- Modify: `code/backend/README.md`

- [ ] **Step 1: Update docs to reflect new trust boundaries and remaining gaps**
- [ ] **Step 2: Run backend full suite**
- [ ] **Step 3: Run contracts full suite**
- [ ] **Step 4: Commit**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests -q`
Run: `cd code/contracts && forge test -vv`

Commit message: `docs: refresh hardening call chain and trust boundaries`
