# Payment Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an on-chain payment router that accepts `USDC`, `USDT`, and `PWR` while driving the existing order lifecycle safely.

**Architecture:** Keep `OrderBook.sol` focused on order state and add `OrderPaymentRouter.sol` as the token-entry boundary. The router validates token-specific authorization paths, records a unified payment event, and then advances the order into paid state through `OrderBook`.

**Tech Stack:** Solidity, Foundry, existing OutcomeX contracts, HashKey testnet token assumptions, Permit2 interface stubs

---

### Task 1: Add payment router interfaces and event model

**Files:**
- Create: `code/contracts/src/OrderPaymentRouter.sol`
- Create: `code/contracts/src/interfaces/IOrderPaymentRouter.sol`
- Create: `code/contracts/src/interfaces/IPermit2.sol`
- Modify: `code/contracts/src/interfaces/IOrderLifecycle.sol`
- Test: `code/contracts/test/OrderPaymentRouter.t.sol`

- [ ] Define the router interface, payment-source enum/constant pattern, and unified `OrderPaymentReceived` event.
- [ ] Expose only the minimum order lifecycle hooks the router needs from `OrderBook`.
- [ ] Add a failing Foundry test covering event emission and duplicate-payment rejection.
- [ ] Implement the minimal contract shape and compile.
- [ ] Commit the interface scaffold.

### Task 2: Implement `USDC` / `USDT` / `PWR` payment paths

**Files:**
- Modify: `code/contracts/src/OrderPaymentRouter.sol`
- Modify: `code/contracts/src/OrderBook.sol`
- Modify: `code/contracts/src/PWRToken.sol`
- Test: `code/contracts/test/OrderPaymentRouter.t.sol`

- [ ] Add `payWithUSDCByAuthorization(...)` with explicit authorization payload validation boundary.
- [ ] Add `payWithUSDT(...)` with `Permit2`-driven transfer boundary.
- [ ] Add `payWithPWR(...)` and route PWR settlement source into the same paid-state transition.
- [ ] Extend tests for each payment path and duplicate-settlement guards.
- [ ] Commit the payment-path implementation.

### Task 3: Integrate with settlement and transfer guard expectations

**Files:**
- Modify: `code/contracts/src/SettlementController.sol`
- Modify: `code/contracts/src/RevenueVault.sol`
- Modify: `code/contracts/src/MachineAssetNFT.sol`
- Test: `code/contracts/test/OrderPaymentRouter.t.sol`
- Test: `code/contracts/test/SettlementController.t.sol`

- [ ] Verify paid-state transitions still drive active-task counts and transfer blocking correctly.
- [ ] Ensure payment source metadata does not break confirm / reject / refund flows.
- [ ] Add coverage for payment -> confirm -> settlement -> claim with at least one stablecoin path and the PWR path.
- [ ] Run `forge test -vv` in `code/contracts`.
- [ ] Commit the integration pass.
