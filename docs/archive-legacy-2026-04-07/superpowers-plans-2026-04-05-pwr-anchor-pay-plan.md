# PWR Anchor + Direct Pay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable a minimal but real PWR anchor and direct PWR payment path that works end-to-end across backend control plane and contracts.

**Architecture:** Keep OutcomeX backend as the thin control plane. The backend will continue to own quote and anchor math, expose PWR quote metadata, and generate direct payment intents. Contracts will own the actual `payWithPWR` funds flow and settlement semantics. AgentSkillOS remains untouched.

**Tech Stack:** FastAPI, SQLAlchemy, Pytest, Foundry Solidity tests

---

### Task 1: Lock the minimal PWR anchor contract at the backend boundary

**Files:**
- Modify: `code/backend/app/runtime/cost_service.py`
- Modify: `code/backend/app/schemas/quote.py`
- Test: `code/backend/tests/runtime/test_cost_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_quote_for_order_amount_exposes_pwr_anchor_metadata() -> None:
    service = RuntimeCostService()

    quote = service.quote_for_order_amount(1000)

    assert quote.pwr_quote == "36.0000"
    assert quote.pwr_anchor_price_cents == 25
    assert quote.pricing_version == "phase1_v3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/runtime/test_cost_service.py::test_quote_for_order_amount_exposes_pwr_anchor_metadata -q`
Expected: FAIL because `QuoteResponse` has no `pwr_anchor_price_cents` and pricing version is still old.

- [ ] **Step 3: Write minimal implementation**

```python
class QuoteResponse(BaseModel):
    ...
    pwr_anchor_price_cents: int = Field(gt=0)
    pricing_version: str = "phase1_v3"
```

```python
class RuntimeCostService:
    pricing_version = "phase1_v3"

    def quote_for_order_amount(self, official_quote_cents: int) -> QuoteResponse:
        ...
        return QuoteResponse(
            ...,
            pwr_anchor_price_cents=self.pwr_anchor_price_cents,
            pricing_version=self.pricing_version,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/runtime/test_cost_service.py::test_quote_for_order_amount_exposes_pwr_anchor_metadata -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add code/backend/app/runtime/cost_service.py code/backend/app/schemas/quote.py code/backend/tests/runtime/test_cost_service.py
git commit -m "feat: expose pwr anchor metadata in quote"
```

### Task 2: Add backend PWR direct payment intent support

**Files:**
- Modify: `code/backend/app/api/routes/payments.py`
- Modify: `code/backend/app/onchain/order_writer.py`
- Modify: `code/backend/app/schemas/payment.py`
- Test: `code/backend/tests/api/test_direct_payments_api.py`
- Test: `code/backend/tests/onchain/test_order_writer.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_create_direct_payment_intent_supports_pwr_when_anchor_exists(...):
    response = test_client.post(
        f"/api/v1/payments/orders/{order[id]}/direct-intent",
        json={"amount_cents": 1000, "currency": "PWR"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["method_name"] == "payWithPWR"
    assert payload["submit_payload"]["currency"] == "PWR"
    assert payload["submit_payload"]["pwr_amount"] == "36000000000000000000"
```

```python
def test_writer_builds_pwr_direct_payment_call_spec() -> None:
    intent = writer.build_direct_payment_intent(order, payment, pwr_amount="36000000000000000000", pricing_version="phase1_v3", pwr_anchor_price_cents=25)
    assert intent.method_name == "payWithPWR"
    assert intent.payload["pwr_amount"] == "36000000000000000000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_direct_payments_api.py tests/onchain/test_order_writer.py -q`
Expected: FAIL because PWR is still rejected and writer does not emit PWR payload.

- [ ] **Step 3: Write minimal implementation**

```python
if currency == "PWR":
    pwr_quote = cost_service.quote_for_order_amount(order.quoted_amount_cents)
    direct_intent = order_writer.build_direct_payment_intent(
        order,
        payment,
        pwr_amount=_decimal_to_wei_string(pwr_quote.pwr_quote),
        pricing_version=pwr_quote.pricing_version,
        pwr_anchor_price_cents=pwr_quote.pwr_anchor_price_cents,
    )
```

```python
if currency == "PWR":
    return self._submit_to_target(..., "payWithPWR", {
        ...,
        "pwr_amount": pwr_amount,
        "pricing_version": pricing_version,
        "pwr_anchor_price_cents": pwr_anchor_price_cents,
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_direct_payments_api.py tests/onchain/test_order_writer.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add code/backend/app/api/routes/payments.py code/backend/app/onchain/order_writer.py code/backend/app/schemas/payment.py code/backend/tests/api/test_direct_payments_api.py code/backend/tests/onchain/test_order_writer.py
git commit -m "feat: add backend pwr direct payment intent"
```

### Task 3: Enable real `payWithPWR` settlement semantics in contracts

**Files:**
- Modify: `code/contracts/src/OrderPaymentRouter.sol`
- Test: `code/contracts/test/OrderPaymentRouter.t.sol`
- Optionally modify: `code/contracts/README.md`

- [ ] **Step 1: Write the failing tests**

```solidity
function testPWRConfirmedOrderCreatesPlatformClaimAndMachineReserve() public {
    vm.prank(BUYER);
    uint256 orderId = orderBook.createOrder(machineId, 1_000);

    vm.prank(ADMIN);
    pwr.setMinter(ADMIN, true);
    vm.prank(ADMIN);
    pwr.mint(BUYER, 1_000);

    vm.prank(BUYER);
    pwr.approve(address(router), 1_000);
    vm.prank(BUYER);
    router.payWithPWR(orderId, 1_000);

    vm.prank(MACHINE_OWNER);
    orderBook.markPreviewReady(orderId, true);
    vm.prank(BUYER);
    orderBook.confirmResult(orderId);

    assertEq(settlement.platformAccruedByToken(address(pwr)), 100);
    assertEq(revenueVault.unsettledRevenueByMachine(machineId), 900);
}
```

```solidity
function testPWRFailedBeforePreviewCanRefundPaidPWR() public {
    ...
    router.payWithPWR(orderId, 1_000);
    vm.prank(BUYER);
    orderBook.refundFailedOrNoValidPreview(orderId);
    assertEq(settlement.refundableByToken(BUYER, address(pwr)), 1_000);
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd code/contracts && forge test -vv --match-path test/OrderPaymentRouter.t.sol`
Expected: FAIL because `payWithPWR` still reverts.

- [ ] **Step 3: Write minimal implementation**

```solidity
function payWithPWR(uint256 orderId, uint256 amount) external {
    bool dividendEligible = _validateOrderForPayment(orderId, amount);
    bool success = pwr.transferFrom(msg.sender, _settlementEscrow(), amount);
    require(success, "PWR_TRANSFER_FAILED");
    _markOrderPaid(orderId, amount, address(pwr), PAYMENT_SOURCE_PWR, dividendEligible);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd code/contracts && forge test -vv --match-path test/OrderPaymentRouter.t.sol`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add code/contracts/src/OrderPaymentRouter.sol code/contracts/test/OrderPaymentRouter.t.sol code/contracts/README.md
git commit -m "feat: enable pwr direct pay onchain"
```

### Task 4: Verify full backend + contract regression surface and refresh docs

**Files:**
- Modify: `docs/backend-contract-interface-map-cn.md`
- Modify: `code/backend/README.md`
- Modify: `code/contracts/README.md`

- [ ] **Step 1: Update docs**

Document that the current minimal anchor is backend-priced, deterministic, and versioned; direct PWR pay now uses backend quote metadata plus onchain `payWithPWR`.

- [ ] **Step 2: Run backend tests**

Run: `cd code/backend && PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests -q`
Expected: PASS

- [ ] **Step 3: Run contract tests**

Run: `cd code/contracts && forge test -vv`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add docs/backend-contract-interface-map-cn.md code/backend/README.md code/contracts/README.md
git commit -m "docs: describe pwr anchor and payment flow"
```
