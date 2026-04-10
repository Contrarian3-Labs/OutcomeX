# Local Demo Primary + Secondary Market Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic local buyer/owner demo world with primary issuance on the market page, backend-owned stock, HSP-driven primary mint completion, and richer seeded secondary-market listings.

**Architecture:** Keep primary issuance platform-controlled in the backend and keep secondary-market trades wallet-to-contract. Extend the local seed script to create one buyer, three owners, three machine assets, and two active listings; add a backend primary issuance read/write model plus HSP finalize hook; then render a dedicated market-page primary issuance section in the frontend.

**Tech Stack:** FastAPI, SQLAlchemy, Web3.py, existing OutcomeX HSP adapter flow, React, TanStack Query, wagmi, Vitest, pytest.

---

### Task 1: Add backend primary issuance persistence and schemas

**Files:**
- Modify: `code/backend/app/domain/models.py`
- Modify: `code/backend/app/db/base.py`
- Create: `code/backend/app/schemas/primary_issuance.py`
- Test: `code/backend/tests/api/test_primary_issuance_api.py`

- [ ] **Step 1: Write the failing backend API/schema tests**

```python
from fastapi.testclient import TestClient


def test_primary_skus_returns_fixed_demo_catalog(client: TestClient) -> None:
    response = client.get("/api/v1/primary-issuance/skus")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["sku_id"] == "apple-96g-qwen"
    assert payload[0]["hardware_profile"] == "Apple Silicon 96GB Unified Memory"
    assert payload[0]["model_family_label"] == "Qwen Family"
    assert payload[0]["price_amount"] == "3.9"
    assert payload[0]["payment_rail"] == "HSP"
    assert payload[0]["stock_available"] == 10


def test_primary_purchase_intent_rejects_out_of_stock(client: TestClient) -> None:
    response = client.post(
        "/api/v1/primary-issuance/skus/apple-96g-qwen/purchase-intent",
        json={"buyer_user_id": "buyer-1", "buyer_wallet_address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"},
    )
    assert response.status_code in {409, 422}
```

- [ ] **Step 2: Run the new failing tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider tests/api/test_primary_issuance_api.py -q`
Expected: FAIL because the route, schema, and persistence model do not exist yet.

- [ ] **Step 3: Add persistence models for stock and primary purchases**

```python
class PrimaryIssuanceSku(Base):
    __tablename__ = "primary_issuance_skus"

    sku_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    hardware_profile: Mapped[str] = mapped_column(String(128), nullable=False)
    model_family_label: Mapped[str] = mapped_column(String(64), nullable=False)
    price_amount: Mapped[str] = mapped_column(String(32), nullable=False)
    price_currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    payment_rail: Mapped[str] = mapped_column(String(32), default="HSP", nullable=False)
    stock_available: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hosted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class PrimaryIssuancePurchase(Base):
    __tablename__ = "primary_issuance_purchases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    sku_id: Mapped[str] = mapped_column(ForeignKey("primary_issuance_skus.sku_id"), index=True)
    buyer_user_id: Mapped[str] = mapped_column(String(64), index=True)
    buyer_wallet_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_payment", nullable=False)
    minted_machine_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stock_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
```

- [ ] **Step 4: Add Pydantic schemas for primary issuance**

```python
class PrimaryIssuanceSkuResponse(BaseModel):
    sku_id: str
    display_name: str
    hardware_profile: str
    model_family_label: str
    price_amount: str
    price_currency: str
    payment_rail: str
    stock_available: int
    hosted_by: str


class PrimaryIssuancePurchaseIntentRequest(BaseModel):
    buyer_user_id: str
    buyer_wallet_address: str | None = None


class PrimaryIssuancePurchaseIntentResponse(BaseModel):
    purchase_id: str
    sku_id: str
    stock_available: int
    payment_id: str
    provider: str
    checkout_url: str
    provider_reference: str
    state: PaymentState
```

- [ ] **Step 5: Run the targeted tests again**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider tests/api/test_primary_issuance_api.py -q`
Expected: still FAIL, but now only because the route implementation is missing.

- [ ] **Step 6: Commit**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX add code/backend/app/domain/models.py code/backend/app/schemas/primary_issuance.py code/backend/tests/api/test_primary_issuance_api.py
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "feat: add primary issuance persistence models"
```

### Task 2: Implement backend primary issuance routes and HSP finalize hook

**Files:**
- Create: `code/backend/app/api/routes/primary_issuance.py`
- Modify: `code/backend/app/main.py`
- Modify: `code/backend/app/api/routes/hsp_webhooks.py`
- Modify: `code/backend/app/api/routes/payments.py`
- Modify: `code/backend/app/core/container.py`
- Modify: `code/backend/tests/api/test_primary_issuance_api.py`
- Test: `code/backend/tests/api/test_primary_issuance_api.py`

- [ ] **Step 1: Expand failing tests for purchase intent and HSP completion**

```python
def test_primary_purchase_intent_creates_hsp_payment_and_purchase_record(client: TestClient) -> None:
    response = client.post(
        "/api/v1/primary-issuance/skus/apple-96g-qwen/purchase-intent",
        json={"buyer_user_id": "buyer-1", "buyer_wallet_address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "hsp"
    assert payload["payment_id"]
    assert payload["purchase_id"]


def test_successful_hsp_webhook_decrements_stock_and_mints_once(client: TestClient) -> None:
    purchase = client.post(
        "/api/v1/primary-issuance/skus/apple-96g-qwen/purchase-intent",
        json={"buyer_user_id": "buyer-1", "buyer_wallet_address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"},
    ).json()

    first = client.post(
        "/api/v1/payments/hsp/webhooks",
        headers={"x-signature": "test-signature"},
        content=_completed_hsp_webhook_body(payment_request_id=purchase["provider_reference"]),
    )
    second = client.post(
        "/api/v1/payments/hsp/webhooks",
        headers={"x-signature": "test-signature"},
        content=_completed_hsp_webhook_body(payment_request_id=purchase["provider_reference"]),
    )

    assert first.status_code == 200
    assert second.status_code == 200
```

- [ ] **Step 2: Run the tests to confirm they fail on missing route behavior**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider tests/api/test_primary_issuance_api.py -q`
Expected: FAIL because no route is mounted and no HSP branch updates purchase status.

- [ ] **Step 3: Implement the primary issuance route module**

```python
router = APIRouter(prefix="/primary-issuance", tags=["primary-issuance"])


@router.get("/skus", response_model=list[PrimaryIssuanceSkuResponse])
def list_primary_issuance_skus(db: Session = Depends(get_db)) -> list[PrimaryIssuanceSkuResponse]:
    rows = db.execute(select(PrimaryIssuanceSku).order_by(PrimaryIssuanceSku.sku_id)).scalars().all()
    return [PrimaryIssuanceSkuResponse.model_validate(row) for row in rows]


@router.post("/skus/{sku_id}/purchase-intent", response_model=PrimaryIssuancePurchaseIntentResponse)
def create_primary_purchase_intent(
    sku_id: str,
    payload: PrimaryIssuancePurchaseIntentRequest,
    db: Session = Depends(get_db),
    container: Container = Depends(get_dependency_container),
) -> PrimaryIssuancePurchaseIntentResponse:
    sku = db.get(PrimaryIssuanceSku, sku_id)
    if sku is None:
        raise HTTPException(status_code=404, detail="Primary issuance SKU not found")
    if sku.stock_available <= 0:
        raise HTTPException(status_code=409, detail="Primary issuance SKU is out of stock")
    # create purchase row + HSP payment row via existing adapter path
    payment = Payment(
        order_id=synthetic_order.id,
        provider="hsp",
        provider_reference=provider_reference,
        merchant_order_id=purchase.id,
        checkout_url=checkout_url,
        amount_cents=390,
        currency="USD",
        state=PaymentState.PENDING,
    )
    purchase.payment_id = payment.id
```

- [ ] **Step 4: Hook HSP success into stock decrement and machine mint**

```python
def _finalize_primary_issuance_payment(purchase: PrimaryIssuancePurchase, sku: PrimaryIssuanceSku, *, lifecycle_service: OnchainLifecycleService, db: Session):
    if purchase.status in {"minted", "paid"}:
        return purchase
    sku.stock_available -= 1
    minted = lifecycle_service.mint_machine_for_owner(
        owner_user_id=purchase.buyer_user_id,
        token_uri="ipfs://outcomex-machine/primary-issuance",
    )
    purchase.status = "minted"
    purchase.minted_machine_id = resolved_machine_id
```

Wire this from `hsp_webhooks.py` after HSP success is authenticated and before commit, with idempotency on `purchase.status` and `payment.callback_event_id`.

- [ ] **Step 5: Register the route and container dependencies**

```python
app.include_router(primary_issuance.router, prefix="/api/v1")
```

If a helper service is needed, register it in `container.py` with the same settings/session patterns used by other route modules.

- [ ] **Step 6: Run targeted backend tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider tests/api/test_primary_issuance_api.py -q`
Expected: PASS.

- [ ] **Step 7: Run the broader backend suite for regressions**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q`
Expected: PASS with the existing suite plus the new tests.

- [ ] **Step 8: Commit**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX add code/backend/app/api/routes/primary_issuance.py code/backend/app/api/routes/hsp_webhooks.py code/backend/app/api/routes/payments.py code/backend/app/main.py code/backend/app/core/container.py code/backend/tests/api/test_primary_issuance_api.py
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "feat: add primary issuance hsp flow"
```

### Task 3: Extend deterministic local demo seed for buyer + owners + listings + stock

**Files:**
- Modify: `code/backend/scripts/prepare_local_browser_demo.py`
- Modify: `scripts/start_local_browser_demo.sh`
- Test: `code/backend/tests/scripts/test_prepare_local_browser_demo.py`
- Test: `code/backend/tests/api/test_marketplace_api.py`

- [ ] **Step 1: Add failing seed tests for the richer local world**

```python
def test_prepare_local_browser_demo_seeds_three_machines_and_two_active_listings(run_prepare_demo) -> None:
    result = run_prepare_demo()
    assert result.machine_count == 3
    assert result.active_listing_count == 2
    assert result.unlisted_machine_count == 1
    assert result.primary_stock_available == 10
```

- [ ] **Step 2: Run the failing seed tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider tests/scripts/test_prepare_local_browser_demo.py -q`
Expected: FAIL because the script only seeds one machine and no primary SKU inventory.

- [ ] **Step 3: Update the prepare script to seed the full demo world**

```python
OWNER_TARGETS = [
    {"user_id": "owner-1", "machine_id": "machine-alpha", "display_name": "OutcomeX Qwen Rack Alpha", "list_on_market": True},
    {"user_id": "owner-2", "machine_id": "machine-beta", "display_name": "OutcomeX Qwen Rack Beta", "list_on_market": True},
    {"user_id": "owner-3", "machine_id": "machine-gamma", "display_name": "OutcomeX Qwen Rack Gamma", "list_on_market": False},
]
PRIMARY_SKU_ID = "apple-96g-qwen"
PRIMARY_INITIAL_STOCK = 10
```

Add helper functions to:
- ensure the fixed primary SKU row exists
- mint one machine per owner if missing
- create two marketplace listings with long expiries using demo owner keys
- keep buyer PWR funding logic intact
- print a richer seed summary to stdout

- [ ] **Step 4: Update the start script banner so manual testing is obvious**

```bash
Suggested wallets on Anvil:
- buyer-1
- owner-1
- owner-2
- owner-3

Seeded demo:
- 3 hosted machines
- 2 active secondary listings
- 1 owner-controlled unlisted machine
- primary issuance stock: 10
```

- [ ] **Step 5: Run targeted tests**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX/code/backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider tests/scripts/test_prepare_local_browser_demo.py tests/api/test_marketplace_api.py -q`
Expected: PASS.

- [ ] **Step 6: Smoke the seed manually**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX && ./scripts/start_local_browser_demo.sh --prepare-only`
Expected: stdout describes three owners, three machines, two listings, and primary stock; Anvil remains running on `http://127.0.0.1:8545`.

- [ ] **Step 7: Commit**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX add code/backend/scripts/prepare_local_browser_demo.py scripts/start_local_browser_demo.sh code/backend/tests/scripts/test_prepare_local_browser_demo.py code/backend/tests/api/test_marketplace_api.py
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "feat: seed richer local market demo"
```

### Task 4: Add frontend primary issuance market UX and API hooks

**Files:**
- Modify: `forge-yield-ai/src/lib/api/outcomex-types.ts`
- Modify: `forge-yield-ai/src/lib/api/query-keys.ts`
- Modify: `forge-yield-ai/src/lib/api/outcomex-client.ts`
- Modify: `forge-yield-ai/src/hooks/use-outcomex-api.ts`
- Modify: `forge-yield-ai/src/pages/NodeMarket.tsx`
- Create: `forge-yield-ai/src/lib/primary-issuance-api.ts`
- Test: `forge-yield-ai/src/test/node-market-primary-issuance.test.tsx`
- Test: `forge-yield-ai/src/lib/primary-issuance-api.test.ts`

- [ ] **Step 1: Write the failing frontend tests**

```tsx
it("renders a primary issuance section above secondary listings", async () => {
  render(<NodeMarket />)
  expect(await screen.findByText(/Primary Issuance/i)).toBeInTheDocument()
  expect(screen.getByText(/Apple Silicon 96GB Unified Memory/i)).toBeInTheDocument()
  expect(screen.getByText(/Qwen Family/i)).toBeInTheDocument()
  expect(screen.getByText(/3.9 via HSP/i)).toBeInTheDocument()
})

it("starts a primary purchase intent when the CTA is clicked", async () => {
  render(<NodeMarket />)
  await userEvent.click(await screen.findByRole("button", { name: /Buy New Hosted Machine/i }))
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v1/primary-issuance/skus/apple-96g-qwen/purchase-intent"), expect.anything())
})
```

- [ ] **Step 2: Run the failing frontend tests**

Run: `cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai && npx vitest run src/test/node-market-primary-issuance.test.tsx src/lib/primary-issuance-api.test.ts`
Expected: FAIL because the API helper and new market section do not exist yet.

- [ ] **Step 3: Add frontend API types and hooks**

```ts
export interface PrimaryIssuanceSkuResponse {
  sku_id: string;
  display_name: string;
  hardware_profile: string;
  model_family_label: string;
  price_amount: string;
  price_currency: string;
  payment_rail: string;
  stock_available: number;
  hosted_by: string;
}

export interface PrimaryIssuancePurchaseIntentResponse {
  purchase_id: string;
  sku_id: string;
  payment_id: string;
  provider: string;
  checkout_url: string;
  stock_available: number;
}
```

Expose:
- `listPrimaryIssuanceSkus()`
- `createPrimaryIssuancePurchaseIntent()`
- `useOutcomeXPrimaryIssuanceSkusQuery()`
- `useOutcomeXPrimaryIssuancePurchaseIntentMutation()`

- [ ] **Step 4: Render the market page in two sections**

```tsx
<section>
  <h2>Primary Issuance</h2>
  <PrimarySkuCard sku={primarySku} onPurchase={handlePrimaryPurchase} isPending={purchaseMutation.isPending} />
</section>
<section>
  <h2>Secondary Market</h2>
  {/* existing listing grid */}
</section>
```

The primary card should:
- show stock
- show `Apple Silicon 96GB Unified Memory`
- show `Qwen Family`
- show `3.9 via HSP`
- disable CTA when stock is zero
- on click, call the purchase-intent mutation and open or display the HSP checkout URL

- [ ] **Step 5: Run frontend tests and typecheck**

Run: `cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai && npx vitest run src/test/node-market-primary-issuance.test.tsx src/lib/primary-issuance-api.test.ts && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 6: Run the existing frontend suite for regression confidence**

Run: `cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai && npm run build && npx vitest run`
Expected: PASS with the current suite plus the new primary issuance coverage.

- [ ] **Step 7: Commit**

```bash
git -C /mnt/c/users/72988/desktop/hashkey/forge-yield-ai add src/lib/api/outcomex-types.ts src/lib/api/query-keys.ts src/lib/api/outcomex-client.ts src/hooks/use-outcomex-api.ts src/pages/NodeMarket.tsx src/lib/primary-issuance-api.ts src/test/node-market-primary-issuance.test.tsx src/lib/primary-issuance-api.test.ts
git -C /mnt/c/users/72988/desktop/hashkey/forge-yield-ai commit -m "feat: add primary issuance market flow"
```

### Task 5: Verify the full local browser walkthrough and update docs

**Files:**
- Modify: `LOCAL_BROWSER_DEMO_CN.md`
- Modify: `code/backend/README.md`
- Modify: `forge-yield-ai/README.md`
- Test: local manual walkthrough + existing smoke checks

- [ ] **Step 1: Update the local demo documentation**

```md
## Demo roles
- buyer-1: primary issuance + secondary purchase
- owner-1: pre-listed machine owner
- owner-2: pre-listed machine owner
- owner-3: unlisted machine owner for listing/cancel demo

## What the fresh stack contains
- 3 hosted machines
- 2 active secondary listings
- primary issuance stock: 10
```

- [ ] **Step 2: Start the full local stack**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX && ./scripts/start_local_browser_demo.sh`
Expected: frontend on `http://127.0.0.1:8080`, backend on `http://127.0.0.1:8787`, anvil on `http://127.0.0.1:8545`.

- [ ] **Step 3: Verify backend read APIs directly**

Run:

```bash
curl -fsS http://127.0.0.1:8787/api/v1/primary-issuance/skus
curl -fsS http://127.0.0.1:8787/api/v1/marketplace/listings
curl -fsS http://127.0.0.1:8787/api/v1/machines
```

Expected:
- one primary SKU with stock `10`
- two active listings
- three seeded machines before primary mint

- [ ] **Step 4: Perform the manual browser walkthrough**

Expected manual checks:
- buyer sees primary section and two secondary listings
- buyer can start a primary HSP purchase intent
- buyer can buy a seeded secondary listing
- owner-3 can create and cancel a listing from machine detail
- backend projection updates after wallet-to-contract actions

- [ ] **Step 5: Stop the local stack cleanly**

Run: `cd /mnt/c/users/72988/desktop/OutcomeX && ./scripts/stop_local_browser_demo.sh`
Expected: ports `8545`, `8787`, and `8080` are closed.

- [ ] **Step 6: Commit documentation updates**

```bash
git -C /mnt/c/users/72988/desktop/OutcomeX add LOCAL_BROWSER_DEMO_CN.md code/backend/README.md
git -C /mnt/c/users/72988/desktop/OutcomeX commit -m "docs: update local market demo walkthrough"

git -C /mnt/c/users/72988/desktop/hashkey/forge-yield-ai add README.md
git -C /mnt/c/users/72988/desktop/hashkey/forge-yield-ai commit -m "docs: update frontend local market walkthrough"
```
