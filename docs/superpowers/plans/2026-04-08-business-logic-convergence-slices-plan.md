# Business Logic Convergence Slices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six remaining business-truth slices across contracts, backend, AgentSkillOS integration, frontend, and the single canonical backlog document, then merge each slice to `main` with its own commit.

**Architecture:** Implement six vertical slices in fixed order. For each slice, tighten contract truth first where needed, project that truth into backend read models, then consume only projected truth in frontend, and finally update the canonical convergence backlog document with status, commit hash, and verification notes.

**Tech Stack:** Solidity/Foundry, FastAPI, SQLAlchemy, onchain indexer/projection, AgentSkillOS subprocess bridge, React/Vite frontend, local Anvil, pytest, npm tests

---

## File Map

### Contracts
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/contracts/src/OrderBook.sol`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/contracts/src/OrderPaymentRouter.sol`
- Modify if needed: `/mnt/c/users/72988/desktop/OutcomeX/code/contracts/src/SettlementController.sol`
- Modify if needed: `/mnt/c/users/72988/desktop/OutcomeX/code/contracts/src/RevenueVault.sol`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/contracts/test/*.t.sol`

### Backend
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/orders.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/payments.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/hsp_webhooks.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/chat_plans.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/revenue.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/indexer/events.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/indexer/sql_projection.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/integrations/agentskillos_bridge.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/integrations/agentskillos_execution_service.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/execution/service.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/schemas/chat_plan.py`
- Modify if needed: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/schemas/order.py`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/**/*.py`

### Frontend
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/OrderDetail.tsx`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/ChatWorkspace.tsx`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/AssetYield.tsx`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/MyMachines.tsx`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/lib/api/outcomex-client.ts`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/lib/api/outcomex-types.ts`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/lib/order-presentation.ts`
- Modify if needed: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/lib/machines-api.ts`
- Test: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/test/*.test.tsx`

### Canonical backlog doc
- Modify after every slice: `/mnt/c/users/72988/desktop/OutcomeX/docs/business-logic-implementation-gap-checklist-2026-04-07-cn.md`

---

### Task 1: Slice A - Enforce unpaid order expiry across contract, backend, and frontend

**Files:**
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/contracts/src/OrderBook.sol`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/indexer/events.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/indexer/sql_projection.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/orders.py`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/OrderDetail.tsx`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/contracts/test/OrderExpiry.t.sol`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/test_order_execution_gate.py`
- Test: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/test/order-detail-wallet-actions.test.tsx`

- [ ] **Step 1: Write failing contract tests for unpaid expiry / cancellation truth**

```solidity
function testExpiredOrderCannotBePaid() public {}
function testCleanupCancelsExpiredOrder() public {}
function testCancelledOrderCannotExecuteOrSettle() public {}
```

- [ ] **Step 2: Run the targeted contract tests to verify failure**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/contracts && forge test --match-path test/OrderExpiry.t.sol -vv`
Expected: FAIL on missing deadline / cancel semantics.

- [ ] **Step 3: Add minimal `deadline` / expiry / cleanup truth to `OrderBook.sol`**

```solidity
uint64 public constant UNPAID_TTL = 10 minutes;
mapping(uint256 => uint64) public unpaidDeadlineByOrder;
event OrderCancelled(uint256 indexed orderId, uint256 indexed machineId, bytes32 reason);

function cancelExpiredOrder(uint256 orderId) external {
    OrderRecord storage order = _orders[orderId];
    require(order.status == OrderStatus.Created, "INVALID_STATUS");
    require(block.timestamp > unpaidDeadlineByOrder[orderId], "NOT_EXPIRED");
    order.status = OrderStatus.Cancelled;
    emit OrderCancelled(orderId, order.machineId, keccak256("UNPAID_EXPIRED"));
}
```

- [ ] **Step 4: Add backend event normalization + SQL projection for expired/cancelled orders**

```python
if event_name == "OrderCancelled":
    return OrderLifecycleEvent(
        order_id=str(decoded["order_id"]),
        machine_id=str(decoded["machine_id"]),
        status="CANCELLED",
        cancelled_reason=str(decoded.get("reason", "")),
    )
```

```python
elif order_status == "CANCELLED":
    order.state = OrderState.CANCELLED
    order.cancelled_at = event.block_timestamp or datetime.now(timezone.utc)
    if machine is not None:
        machine.has_active_tasks = False
```

- [ ] **Step 5: Tighten backend execution gate and frontend rendering to respect projected expiry/cancel**

```python
if order.is_expired or order.is_cancelled:
    raise HTTPException(status_code=409, detail="Order is expired or cancelled")
```

```tsx
const startExecutionDisabledReason = order.is_cancelled
  ? "Order is cancelled"
  : order.is_expired
    ? "Order is expired"
    : !hasAuthoritativePaidProjection(order)
      ? "Order execution requires paid projection"
      : null;
```

- [ ] **Step 6: Run focused tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/contracts && forge test --match-path test/OrderExpiry.t.sol -vv`
Expected: PASS

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/test_order_execution_gate.py -q`
Expected: PASS

Run: `cd /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai && npm test -- --run src/test/order-detail-wallet-actions.test.tsx`
Expected: PASS

- [ ] **Step 7: Update the canonical backlog doc and commit**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX add code/contracts/src/OrderBook.sol code/contracts/test/OrderExpiry.t.sol code/backend/app/indexer/events.py code/backend/app/indexer/sql_projection.py code/backend/app/api/routes/orders.py docs/business-logic-implementation-gap-checklist-2026-04-07-cn.md
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "fix: enforce unpaid order expiry across contract backend frontend"
git -C /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai add src/pages/OrderDetail.tsx src/test/order-detail-wallet-actions.test.tsx
git -C /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai commit -m "fix: enforce unpaid order expiry across contract backend frontend"
```

### Task 2: Slice B - Ship HSP as the primary stablecoin payment flow

**Files:**
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/contracts/src/OrderPaymentRouter.sol`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/payments.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/hsp_webhooks.py`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/OrderDetail.tsx`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/lib/api/outcomex-client.ts`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/hooks/use-outcomex-api.ts`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/api/test_hsp_webhooks.py`
- Test: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/test/product-closure.test.tsx`

- [ ] **Step 1: Write failing backend/frontend tests for HSP-first stablecoin UX**

```python
def test_hsp_payment_flow_requires_checkout_then_projection_paid(client):
    ...
```

```tsx
it("shows HSP checkout as the stablecoin primary path", async () => {
  expect(screen.getByText(/HSP/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run focused tests to verify failure**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_hsp_webhooks.py -q`
Expected: FAIL on missing end-to-end status expectations.

Run: `cd /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai && npm test -- --run src/test/product-closure.test.tsx`
Expected: FAIL on HSP-first UI expectations.

- [ ] **Step 3: Remove stablecoin direct-pay from formal UI path and make HSP states explicit**

```tsx
const stablecoinRail = "HSP";
const stablecoinStatus = payment?.provider === "hsp" ? payment.state : "pending_checkout";
```

- [ ] **Step 4: Tighten backend payment routes so formal stablecoin path is HSP-only**

```python
if payload.currency in {"USDC", "USDT"}:
    return create_hsp_checkout(...)
```

- [ ] **Step 5: Keep paid truth projection-first in webhook/application flow**

```python
if mapped_state == PaymentState.SUCCEEDED:
    # write chain / backfill anchor, but frontend still waits for projected paid order state
```

- [ ] **Step 6: Run focused tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_hsp_webhooks.py -q`
Expected: PASS

Run: `cd /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai && npm test -- --run src/test/product-closure.test.tsx`
Expected: PASS

- [ ] **Step 7: Update canonical backlog doc and commit**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "feat: ship hsp as the primary stablecoin payment flow"
git -C /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai commit -m "feat: ship hsp as the primary stablecoin payment flow"
```

### Task 3: Slice C - Align settlement, refund, and claim projections with onchain truth

**Files:**
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/indexer/events.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/indexer/sql_projection.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/settlement.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/revenue.py`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/OrderDetail.tsx`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/AssetYield.tsx`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/api/test_settlement_convergence_api.py`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/indexer/test_sql_projection_store.py`
- Test: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/test/order-detail-wallet-actions.test.tsx`

- [ ] **Step 1: Add failing tests for reject/refund/claim projection completeness**
- [ ] **Step 2: Run targeted backend/frontend tests and verify failure**
- [ ] **Step 3: Normalize `RefundClaimed`, `PlatformRevenueClaimed`, and machine claim events into complete read-model updates**
- [ ] **Step 4: Update frontend settlement/claim surfaces to consume only projected truth**
- [ ] **Step 5: Run focused tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_settlement_convergence_api.py tests/indexer/test_sql_projection_store.py -q`
Expected: PASS

Run: `cd /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai && npm test -- --run src/test/order-detail-wallet-actions.test.tsx`
Expected: PASS

- [ ] **Step 6: Update canonical backlog doc and commit**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "fix: align settlement projections with onchain truth"
git -C /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai commit -m "fix: align settlement projections with onchain truth"
```

### Task 4: Slice D - Make revenue overview beneficiary-based and amount-accurate

**Files:**
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/domain/settlement_projection.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/indexer/sql_projection.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/revenue.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/machines.py`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/AssetYield.tsx`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/MyMachines.tsx`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/api/test_revenue_overview_api.py`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/api/test_machines_api.py`

- [ ] **Step 1: Add failing backend tests for beneficiary aggregation and partial-claim amount updates**
- [ ] **Step 2: Run focused backend tests to verify failure**
- [ ] **Step 3: Replace current-owner aggregation with beneficiary-based aggregation and amount-accurate claim math**
- [ ] **Step 4: Update frontend yield / machine readiness views to consume the new fields**
- [ ] **Step 5: Run focused tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_revenue_overview_api.py tests/api/test_machines_api.py -q`
Expected: PASS

- [ ] **Step 6: Update canonical backlog doc and commit**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "fix: make revenue overview beneficiary based and amount accurate"
git -C /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai commit -m "fix: make revenue overview beneficiary based and amount accurate"
```

### Task 5: Slice E - Pass real planning inputs into AgentSkillOS chat plans

**Files:**
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/schemas/chat_plan.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/chat_plans.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/integrations/agentskillos_bridge.py`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/ChatWorkspace.tsx`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/lib/api/outcomex-types.ts`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/lib/plans-order-api.ts`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/api/test_chat_plans_api.py`
- Test: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/test/chat-workspace-api-hooks.test.tsx`
- Test: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/test/plans-order-flow.test.tsx`

- [ ] **Step 1: Add failing tests for `mode` + `attachments/input_files` request contract**
- [ ] **Step 2: Run focused tests to verify failure**
- [ ] **Step 3: Extend `ChatPlanRequest` and bridge planning path to pass files/mode into AgentSkillOS**

```python
class ChatPlanRequest(BaseModel):
    user_id: str
    chat_session_id: str
    user_message: str
    mode: ExecutionStrategy = ExecutionStrategy.QUALITY
    input_files: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Update frontend plan request payloads to send selected mode and attachments**
- [ ] **Step 5: Run focused tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_chat_plans_api.py -q`
Expected: PASS

Run: `cd /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai && npm test -- --run src/test/chat-workspace-api-hooks.test.tsx src/test/plans-order-flow.test.tsx`
Expected: PASS

- [ ] **Step 6: Update canonical backlog doc and commit**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "feat: pass real planning inputs into agentskillos chat plans"
git -C /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai commit -m "feat: pass real planning inputs into agentskillos chat plans"
```

### Task 6: Slice F - Harden selected plan as the execution contract

**Files:**
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/execution/service.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/integrations/agentskillos_execution_service.py`
- Modify: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/app/api/routes/orders.py`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/ChatWorkspace.tsx`
- Modify: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/pages/OrderDetail.tsx`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/api/test_orders_api.py`
- Test: `/mnt/c/users/72988/desktop/OutcomeX/code/backend/tests/integrations/test_agentskillos_execution_service.py`
- Test: `/mnt/c/users/72988/desktop/Hashkey/forge-yield-ai/src/test/plans-order-flow.test.tsx`

- [ ] **Step 1: Add failing tests that selected plan index and strategy are locked from plans -> order -> execution run**
- [ ] **Step 2: Run targeted tests to verify failure**
- [ ] **Step 3: Persist selected plan metadata on order creation and enforce it in execution submission**
- [ ] **Step 4: Update frontend to show locked selected plan on order detail and execution CTA surfaces**
- [ ] **Step 5: Run focused tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_orders_api.py tests/integrations/test_agentskillos_execution_service.py -q`
Expected: PASS

Run: `cd /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai && npm test -- --run src/test/plans-order-flow.test.tsx`
Expected: PASS

- [ ] **Step 6: Update canonical backlog doc and commit**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "fix: harden selected plan as execution contract"
git -C /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai commit -m "fix: harden selected plan as execution contract"
```

### Task 7: Final verification, merge discipline, and push

**Files:**
- Modify if needed: `/mnt/c/users/72988/desktop/OutcomeX/docs/business-logic-implementation-gap-checklist-2026-04-07-cn.md`

- [ ] **Step 1: Run backend full test suite**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider -q`
Expected: PASS

- [ ] **Step 2: Run contracts full test suite**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/contracts && forge test -vv`
Expected: PASS

- [ ] **Step 3: Run frontend lint/build/tests relevant to changed surfaces**

Run: `cd /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai && npm run lint && npm run build && npm test -- --run src/test/product-closure.test.tsx`
Expected: PASS

- [ ] **Step 4: Push both repos**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX push origin main
git -C /mnt/c/users/72988/desktop/Hashkey/forge-yield-ai push origin main
```

- [ ] **Step 5: Record final status in canonical backlog doc**

```markdown
- Slice A: 已完成 | commit: <hash> | 验证: forge + pytest + frontend test
- Slice B: 已完成 | commit: <hash> | 验证: pytest + frontend test
...
```
