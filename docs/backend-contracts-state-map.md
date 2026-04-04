# OutcomeX Backend and Contracts State Map

This document describes the current MVP implementation in `OutcomeX` across:

- backend services and APIs
- smart contracts
- backend/contract responsibility boundaries
- order and settlement lifecycle
- state transitions and event surfaces

It is written for engineers who need to understand how the current system works before extending it.

## 1. System Overview

OutcomeX is implemented as two cooperating layers:

- `code/backend`
  - FastAPI-based product backend
  - owns chat/order/payment/execution/preview/settlement orchestration
  - owns self-use policy, payment sufficiency checks, mock lifecycle transitions, and API-facing read models
- `code/contracts`
  - Foundry-based contract suite
  - owns machine asset ownership, transfer guard, order receipt lifecycle, settlement split logic, refundable/platform balances, and machine-side claimable PWR

The current MVP principle is:

- backend decides product policy and freezes settlement classification
- contracts execute frozen settlement semantics and enforce machine-asset transfer restrictions
- indexer projects on-chain contract state into query-friendly backend models

## 2. Backend Modules

### 2.1 API Layer

Main router:

- `code/backend/app/api/router.py`

Key route groups:

- `code/backend/app/api/routes/chat_plans.py`
  - chat-native plan summary endpoint
- `code/backend/app/api/routes/orders.py`
  - create order
  - fetch order
  - mark mock result ready
  - confirm result
- `code/backend/app/api/routes/payments.py`
  - create payment intent through mock HSP boundary
  - mock confirm payment success/failure
  - freeze settlement policy when full payment is reached
- `code/backend/app/api/routes/settlement.py`
  - preview settlement
  - lock settlement
- `code/backend/app/api/routes/revenue.py`
  - distribute locked settlement into backend revenue records
  - list machine revenue
- `code/backend/app/api/routes/machines.py`
  - create/list machine
  - transfer machine with active-task/unsettled-revenue blocking
- `code/backend/app/api/routes/health.py`
  - health check

### 2.2 Domain Layer

Key files:

- `code/backend/app/domain/enums.py`
- `code/backend/app/domain/models.py`
- `code/backend/app/domain/rules.py`
- `code/backend/app/domain/planning.py`

Important backend entities:

- `Machine`
  - backend-side hosted machine object
  - tracks current owner and coarse transfer-block flags
- `Order`
  - product order record
  - stores quoted amount, execution state, preview state, settlement state, and frozen settlement policy fields
- `Payment`
  - off-chain payment record
  - current MVP uses mock HSP-style payment intents and mock confirmation
- `SettlementRecord`
  - backend-side settlement lock record
- `RevenueEntry`
  - backend-side distributed revenue record for query/reporting
- `ChatPlan`
  - chat-native recommendation snapshot

### 2.3 Execution Layer

Key files:

- `code/backend/app/execution/contracts.py`
- `code/backend/app/execution/normalizer.py`
- `code/backend/app/execution/matcher.py`
- `code/backend/app/execution/service.py`
- `code/backend/app/runtime/hardware_simulator.py`
- `code/backend/app/runtime/preview_policy.py`
- `code/backend/app/integrations/providers/alibaba_mulerouter.py`

Responsibilities:

- normalize an outcome intent into an execution recipe
- match the recipe to a supported provider/model family
- simulate machine runtime capacity, memory, concurrency, and queue depth
- decide preview policy for text/image/video
- expose a provider boundary for Alibaba/MuleRouter-backed generation

Current MVP execution rule:

- single-step execution only
- multi-output intents are preserved in metadata but explicitly rejected as unsupported
- the system does not silently drop outputs anymore

### 2.4 Indexer Layer

Key files:

- `code/backend/app/onchain/adapter.py`
- `code/backend/app/indexer/events.py`
- `code/backend/app/indexer/replay.py`
- `code/backend/app/indexer/projections.py`
- `code/backend/app/indexer/cursor.py`

Responsibilities:

- define decoded-chain-event adapters
- normalize real emitted contract events into backend domain events
- replay events in canonical chain/log order
- skip unsupported events safely
- keep idempotent projection state
- expose projection stores for orders, machine assets, revenue, and transfer eligibility

Current MVP indexer rule:

- the indexer is chain-authoritative in intent
- but still skeleton-level in persistence and reorg rollback depth
- current implementation is suitable for MVP demos and backend projection alignment, not production-grade archival replay

## 3. Contract Modules

### 3.1 `MachineAssetNFT`

File:

- `code/contracts/src/MachineAssetNFT.sol`

Role:

- machine asset NFT
- mints hosted machine assets
- blocks transfer through `ITransferGuard`

Important events:

- `MachineMinted`
- `TransferGuardSet`

### 3.2 `OrderBook`

File:

- `code/contracts/src/OrderBook.sol`

Role:

- on-chain order receipt/state machine
- stores buyer, machine, gross amount, status timestamps
- snapshots settlement beneficiary at order creation
- freezes dividend eligibility and refund authorization at payment time
- blocks transfer while tasks are active or unsettled revenue exists

Important events:

- `OrderCreated`
- `OrderClassified`
- `OrderPaid`
- `PreviewReady`
- `OrderSettled`

Important policy now encoded:

- refund from plain `Paid` state is only allowed if backend/payment adapter explicitly authorized that path
- self-use is not inferred from live owner state
- contract settlement consumes frozen classification from backend/payment adapter

### 3.3 `SettlementController`

File:

- `code/contracts/src/SettlementController.sol`

Role:

- executes settlement split
- records refundable buyer balance
- records platform accrued USDT
- tells `RevenueVault` to accrue machine-side value

Important events:

- `Settled`
- `RefundClaimed`
- `PlatformRevenueClaimed`

Settlement rules:

- confirmed result:
  - `10%` platform
  - `90%` machine side
- valid preview rejected:
  - `70%` refund to buyer
  - remaining `30%` split `10/90`
- failed/no valid preview:
  - `100%` refund

### 3.4 `RevenueVault`

File:

- `code/contracts/src/RevenueVault.sol`

Role:

- machine-side claimability and unsettled revenue accounting
- accrues dividend-eligible machine revenue
- mints/holds PWR claim balance
- exposes `hasUnsettledRevenue(machineId)`

Important events:

- `RevenueAccrued`
- `RevenueClaimed`

Important current behavior:

- dividend-eligible revenue becomes claimable by the snapshotted settlement beneficiary
- non-dividend-eligible revenue is tracked separately and does not become claimable PWR

### 3.5 `PWRToken`

File:

- `code/contracts/src/PWRToken.sol`

Role:

- machine-side settlement token
- minted into `RevenueVault` on eligible settlement accrual

The current MVP does not yet implement:

- market pricing
- trading venue logic
- direct user PWR payment path

## 4. Backend/Contract Responsibility Mapping

### 4.1 Order Creation

Backend:

- creates off-chain `Order`
- stores prompt, quote, and machine selection

Contract:

- `OrderBook.createOrder(machineId, grossAmount)`
- creates chain receipt and snapshots settlement beneficiary

Boundary:

- backend will eventually call into `OrderBook`
- current MVP keeps backend and contract flows parallel rather than fully bridged in one transaction path

### 4.2 Payment Success

Backend:

- mock payment success through `payments.py`
- checks cumulative payment sufficiency
- freezes:
  - settlement beneficiary
  - self-use flag
  - dividend eligibility
- sets machine unsettled-revenue transfer block

Contract:

- `OrderBook.markOrderPaid(orderId, dividendEligible, refundFailedOrNoValidPreviewAuthorized)`
- freezes settlement classification
- increments machine active task count

Key alignment rule:

- backend and contract now both freeze settlement classification at payment time, not at result confirmation time

### 4.3 Result Ready / Preview Ready

Backend:

- current MVP uses `POST /api/v1/orders/{order_id}/mock-result-ready`
- moves order into a confirmable state by setting:
  - `execution_state = succeeded`
  - `preview_state = ready`

Contract:

- `OrderBook.markPreviewReady(orderId, validPreview)`

Key alignment rule:

- result confirmation is not allowed until result/preview readiness exists

### 4.4 Result Confirmation

Backend:

- `POST /api/v1/orders/{order_id}/confirm-result`
- requires:
  - full payment
  - execution succeeded
  - preview ready
  - frozen settlement policy already present

Contract:

- `OrderBook.confirmResult(orderId)`
- requires preview-ready and valid preview

### 4.5 Settlement Lock

Backend:

- `POST /api/v1/settlement/orders/{order_id}/start`
- creates `SettlementRecord`
- transitions settlement state to locked

Contract:

- `SettlementController.settle(...)` is reached from `OrderBook._settleOrder(...)`

Key difference:

- backend keeps a query/reporting settlement record
- contracts keep the authoritative split semantics

### 4.6 Revenue Distribution / Claim

Backend:

- `POST /api/v1/revenue/orders/{order_id}/distribute`
- creates `RevenueEntry`
- recalculates whether other unsettled orders still exist for the machine before reopening transfer

Contract:

- `RevenueVault.accrueRevenue(...)`
- `RevenueVault.claim(machineId)`

Key rule:

- backend distribution is a product-side record
- contract claimability is machine-side asset-side truth

## 5. Backend State Machine

### 5.1 Order State

Enum:

- `code/backend/app/domain/enums.py`

States:

- `draft`
- `plan_recommended`
- `user_confirmed`
- `executing`
- `result_pending_confirmation`
- `result_confirmed`
- `cancelled`

Current actual MVP usage:

- order starts at `plan_recommended`
- mock result-ready route moves order to `result_pending_confirmation`
- confirm-result moves order to `result_confirmed`

### 5.2 Execution State

States:

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`

Current MVP usage:

- defaults to `queued`
- mock result-ready sets to `succeeded`

### 5.3 Preview State

States:

- `draft`
- `generating`
- `ready`
- `expired`

Current MVP usage:

- confirm requires `ready`

### 5.4 Settlement State

States:

- `not_ready`
- `ready`
- `locked`
- `distributed`

Transition path:

1. order created -> `not_ready`
2. result confirmed -> `ready`
3. settlement started -> `locked`
4. revenue distributed -> `distributed`

## 6. Contract State Machine

### 6.1 `OrderStatus`

Defined in:

- `code/contracts/src/types/OutcomeXTypes.sol`

Current lifecycle:

- `Created`
- `Paid`
- `PreviewReady`
- `Confirmed`
- `Rejected`
- `Refunded`

Transition path:

1. `createOrder` -> `Created`
2. `markOrderPaid` -> `Paid`
3. `markPreviewReady(validPreview)` -> `PreviewReady`
4. then one of:
   - `confirmResult` -> `Confirmed`
   - `rejectValidPreview` -> `Rejected`
   - `refundFailedOrNoValidPreview` -> `Refunded`

### 6.2 Transfer Guard

Machine transfer is blocked when either is true:

- active task count for machine is non-zero
- `RevenueVault.hasUnsettledRevenue(machineId)` is true

This logic lives in:

- `code/contracts/src/OrderBook.sol`
- enforced by `code/contracts/src/MachineAssetNFT.sol`

## 7. Real Product Flows

### 7.1 Happy Path: Paid -> Ready -> Confirmed -> Distributed

Backend:

1. create order
2. create payment intent
3. mock confirm payment success
4. freeze settlement beneficiary and dividend eligibility
5. mark result ready
6. confirm result
7. preview settlement
8. start settlement
9. distribute revenue

Contracts:

1. `createOrder`
2. `markOrderPaid`
3. `markPreviewReady(validPreview=true)`
4. `confirmResult`
5. `SettlementController.settle(...Confirmed...)`
6. `RevenueVault.accrueRevenue(...)`
7. beneficiary claims PWR

### 7.2 Valid Preview Rejected

Contracts:

1. `markPreviewReady(validPreview=true)`
2. `rejectValidPreview`
3. `SettlementController` computes:
   - `70%` refund
   - `30%` rejection fee
   - fee split `10/90`

### 7.3 Failed / No Valid Preview

Contracts:

1. backend/payment path must have authorized refund-from-paid if needed
2. `refundFailedOrNoValidPreview`
3. `100%` buyer refund

## 8. Current MVP Boundaries and Known Limits

### 8.1 Implemented

- mock HSP boundary
- backend order/payment/settlement APIs
- execution runtime and provider shell
- explicit multi-output rejection
- machine transfer guard logic
- Foundry contract suite
- indexer event alignment to current contract events

### 8.2 Not Yet Fully Implemented

- real HSP integration
- backend -> contract write path wiring
- production-grade reorg rollback/replay compensation
- persistent DB-backed indexer cursor/projection stores
- real preview artifact storage/unlock flow
- direct PWR payment path
- multi-step or multi-output execution workflows

### 8.3 Important Product Truths Preserved in Current Code

- users buy outcomes, not tools
- settlement starts only after result confirmation
- platform fee is `10%`
- machine side is `90%`
- self-use classification is backend-owned, not live contract inference
- transfer is blocked while active tasks or unsettled machine revenue exist
- machine-side claimability belongs to the snapshotted beneficiary, not a later transferred owner

## 9. Suggested Next Build Steps

1. Wire backend payment/result-confirm routes to real contract calls and tx receipts.
2. Replace backend boolean unsettled flag with derived/order-count-based state or chain projection.
3. Persist indexer cursor/projection stores in Postgres.
4. Add artifact records and preview/final unlock objects to backend order lifecycle.
5. Add contract ABI bindings in backend so event decoding uses deployed contract truth directly.
