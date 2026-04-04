# Execution Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current execution skeleton with a wrapper-based orchestration boundary that can reuse AgentSkillOS-style planning while keeping OutcomeX runtime and provider policy in control.

**Architecture:** Add `AgentSkillOSWrapper` as an internal orchestration facade and `ModelRouter` as the sole provider/model selection entry point. Keep the existing `ExecutionService` public contract stable while upgrading internal plan and dispatch behavior.

**Tech Stack:** Python, FastAPI backend modules, provider adapters, pytest

---

### Task 1: Add wrapper and model-router boundaries

**Files:**
- Create: `code/backend/app/execution/agentskillos_wrapper.py`
- Create: `code/backend/app/integrations/model_router.py`
- Modify: `code/backend/app/execution/contracts.py`
- Test: `code/backend/tests/execution/test_agentskillos_wrapper.py`

- [ ] Define the wrapper input/output mapping around existing `IntentRequest`, `ExecutionRecipe`, and `ExecutionPlan` shapes.
- [ ] Define `ModelRouter` request/response shapes that can sit above current provider adapters.
- [ ] Add failing tests for wrapper planning output and model-family selection behavior.
- [ ] Implement the minimal wrapper/router to satisfy tests.
- [ ] Commit the new boundaries.

### Task 2: Integrate wrapper/router into `ExecutionService`

**Files:**
- Modify: `code/backend/app/execution/service.py`
- Modify: `code/backend/app/integrations/providers/registry.py`
- Modify: `code/backend/app/integrations/providers/alibaba_mulerouter.py`
- Test: `code/backend/tests/execution/test_execution_service_wrapper_integration.py`

- [ ] Replace direct local matching in the execution service with wrapper-driven planning and router-driven model selection where possible.
- [ ] Preserve current single-step fallback behavior for unsupported flows.
- [ ] Add tests covering compatibility with existing text/image/video dispatch rules.
- [ ] Run focused execution pytest suites.
- [ ] Commit the integration layer.

### Task 3: Expose execution metadata needed by preview/confirmation flows

**Files:**
- Modify: `code/backend/app/api/routes/orders.py`
- Modify: `code/backend/app/domain/models.py`
- Modify: `code/backend/app/schemas/order.py`
- Test: `code/backend/tests/api/test_orders_execution_metadata.py`

- [ ] Persist execution metadata needed later by artifact, preview, and confirm gates.
- [ ] Ensure order routes can return execution plan metadata without breaking existing API behavior.
- [ ] Add tests for metadata persistence and order-read response shape.
- [ ] Run focused pytest coverage.
- [ ] Commit the execution metadata pass.
