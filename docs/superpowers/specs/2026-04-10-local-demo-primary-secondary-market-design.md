# Local Demo Primary + Secondary Market Design

## Goal

Make the local browser demo support a complete buyer/owner walkthrough with:
- one buyer wallet
- three owner wallets
- three pre-minted hosted machine assets
- two active secondary-market listings
- one owner-controlled machine left unlisted for listing/cancel/transfer-guard demos
- a new market-page primary issuance section for platform-controlled hosted machine minting via HSP

This design is explicitly for the local hackathon demo path. It optimizes for deterministic setup, repeatable testing, and clean separation between primary issuance and secondary trading.

## Current Context

### What already exists

- Local stack bootstrap exists in `scripts/start_local_browser_demo.sh`.
- Demo seed currently prepares only a minimal world in `code/backend/scripts/prepare_local_browser_demo.py`.
- Backend can already perform platform-controlled machine minting through `POST /api/v1/machines` and `OnchainLifecycleService.mint_machine_for_owner(...)`.
- Frontend already supports:
  - marketplace projection view on `src/pages/NodeMarket.tsx`
  - machine detail with direct wallet secondary-market actions on `src/pages/NodeDetail.tsx`
  - a hosted machine acquisition page on `src/pages/NodePurchase.tsx`
- Secondary-market trading already follows the intended model:
  - frontend wallet signs and submits onchain marketplace tx
  - backend indexes chain events and projects listing / ownership state

### What is missing

- The local demo world is too sparse for a convincing buyer/owner walkthrough.
- There is no market-first primary issuance entry point.
- Primary issuance inventory is not modeled as a first-class backend concept.
- The current hosted machine acquisition screen is machine-template-centric rather than primary-market-centric.
- The start script does not seed multiple owners or secondary listings.

## Product Semantics

### Primary issuance

Primary issuance is platform-controlled issuance of a new hosted machine asset, not a transfer of an existing asset.

Rules:
- shown on the market page as a dedicated `Primary Issuance` section
- fixed hardware profile: `Apple Silicon 96GB Unified Memory`
- fixed model family label: `Qwen Family`
- `Gemma` may exist in backend capability policy, but is not selectable in this UI
- fixed price: `3.9`
- stock is shown as an integer such as `10 units left`
- stock truth lives in backend, not in contracts
- payment rail for primary issuance is HSP only
- once HSP payment succeeds, backend decrements stock and mints a fresh machine NFT to the buyer wallet
- each successful purchase creates a distinct new machine asset; it does not modify or transfer the viewed template machine

### Secondary market

Secondary market remains fully wallet-to-contract for the trade itself.

Rules:
- listings shown in market page come from backend projection of indexed chain events
- listing create / cancel / buy remain direct onchain wallet actions
- backend remains a projection and enrichment layer for machine metadata, listing summaries, owner view, and transfer readiness
- transfer guard semantics stay unchanged: active tasks and unsettled revenue block transfer and therefore block marketplace sale completion

### Demo seed world

The deterministic local demo should contain:
- `buyer-1`
- `owner-1`
- `owner-2`
- `owner-3`
- one machine per owner
- two machines listed on the secondary market
- one machine intentionally left unlisted for owner-side listing/cancel demos
- buyer funded for local PWR flows as already done today
- HSP primary SKU inventory initialized to a demo-friendly stock count such as `10`

## Recommended Approach

### Chosen approach

Use backend-seeded deterministic demo state plus a backend-owned primary SKU read/write model.

Why this is the best fit:
- matches the product semantics you confirmed: primary inventory should be backend truth
- preserves the current architecture split: primary issuance is platform-controlled, secondary trades are onchain user actions
- minimizes contract churn
- produces a repeatable local demo world for browser testing

### Alternatives considered

#### Alternative A: put primary inventory onchain

Rejected for now because:
- it complicates the platform-controlled mint model
- it adds unnecessary contract scope for hackathon demo
- it does not improve the user walkthrough materially

#### Alternative B: reuse existing `NodePurchase` as the only primary entry

Rejected because:
- it hides primary issuance behind a detail page
- it mixes primary issuance with per-machine detail semantics
- it does not make the market page tell the full buyer story

## Architecture

### Backend

Add a primary issuance module that owns:
- fixed SKU definition
- stock count
- HSP purchase initiation data for that SKU
- purchase completion logic that decrements stock and mints the machine

The backend is the source of truth for:
- primary SKU catalog
- primary SKU stock
- mapping the successful primary purchase to a newly minted machine asset

The backend does **not** become the executor of secondary-market transfers.

### Contracts

No new primary-sale contract is required for this slice.

Contracts remain responsible for:
- machine NFT minting
- secondary-market listing and purchase
- transfer guard enforcement
- existing order / settlement / revenue machinery

For primary issuance, the only chain write remains the backend-controlled machine mint transaction after HSP success.

### Frontend

The market page becomes a two-zone screen:
- `Primary Issuance` zone for platform-issued hosted machine SKU
- `Secondary Market` zone for live onchain listings

Primary zone responsibilities:
- display fixed SKU card
- display inventory
- display fixed configuration summary
- start HSP payment
- show in-progress / success / out-of-stock states
- route to the newly minted machine detail after success if practical

Secondary zone responsibilities remain unchanged except for improved seeded content.

## API Design

### New backend read model

Add a primary SKU response with fields along these lines:
- `sku_id`
- `display_name`
- `hardware_profile`
- `model_family_label`
- `price_amount`
- `price_currency`
- `stock_available`
- `hosted_by`
- `payment_rail`
- `image_key` or hero metadata if needed later

### New backend endpoints

#### 1. List primary issuance SKUs

`GET /api/v1/primary-issuance/skus`

Returns the fixed local demo SKU list.

#### 2. Start primary issuance purchase

`POST /api/v1/primary-issuance/skus/{sku_id}/purchase-intent`

Responsibilities:
- validate connected buyer identity / wallet mapping
- ensure stock is available
- create a primary purchase record
- create HSP payment intent payload
- return what the frontend needs to continue payment

#### 3. Finalize primary issuance after HSP success

This can be driven by webhook or existing payment-finalization flow, but the semantic result must be:
- purchase marked paid
- stock decremented exactly once
- machine minted exactly once
- newly minted machine id returned or queryable

If the existing HSP webhook path is reused, primary issuance needs an explicit branch in that flow.

### Persistence

Add tables or equivalent persistence for:
- primary SKU inventory state
- primary purchase records

Minimum persisted purchase fields:
- purchase id
- sku id
- buyer user id
- buyer wallet address
- payment id / provider reference
- status (`pending_payment`, `paid`, `minted`, `failed`, `cancelled`)
- minted machine id
- stock snapshot at purchase time if useful for auditing

## Demo Seed / Bootstrap Design

### Seed behavior

Extend `code/backend/scripts/prepare_local_browser_demo.py` so that a fresh local run:
- ensures buyer + owner wallet mappings exist or are expected by config
- mints three machines, one per owner
- writes matching backend machine rows with deterministic ids / names
- creates two onchain marketplace listings via demo owner keys
- leaves one machine unlisted
- seeds primary SKU stock to `10`
- keeps buyer PWR funding behavior for later PWR order walkthroughs

### Suggested seeded assets

- `owner-1` → machine listed
- `owner-2` → machine listed
- `owner-3` → machine unlisted

Suggested names:
- `OutcomeX Qwen Rack Alpha`
- `OutcomeX Qwen Rack Beta`
- `OutcomeX Qwen Rack Gamma`

All share the same visible hardware profile:
- `Apple Silicon 96GB Unified Memory`

## Frontend UX Design

### Node Market page

Add a primary section above secondary listings.

Primary card should show:
- title such as `Primary Issuance`
- fixed machine image / badge if available
- `Apple Silicon 96GB Unified Memory`
- `Qwen Family`
- `3.9 via HSP`
- stock count
- CTA such as `Buy New Hosted Machine`

Secondary section remains the current grid, but with better seeded data.

### Primary purchase flow

Recommended browser flow:
1. buyer connects wallet
2. buyer opens market page
3. buyer sees primary inventory and secondary listings separately
4. buyer clicks primary buy CTA
5. frontend opens existing HSP path / purchase intent flow
6. after successful HSP confirmation, frontend shows minted machine success state
7. buyer navigates to the newly created machine detail

### Owner flow

Recommended browser flow:
1. connect as `owner-3`
2. open owned machine detail
3. create listing
4. optionally cancel listing
5. optionally start task flow later to show transfer guard behavior

### Buyer flow for secondary

Recommended browser flow:
1. connect as `buyer-1`
2. open market page
3. choose seeded listing
4. approve token if needed
5. buy listing onchain
6. wait for backend projection to update
7. verify ownership changed on machine detail and market card disappears

## Error Handling

### Primary issuance

Handle clearly:
- out of stock
- missing wallet mapping
- HSP payment pending
- HSP payment failed
- mint failed after payment success
- duplicate webhook / duplicate finalize attempt

Important idempotency rule:
- stock decrement and machine mint must be exactly-once per successful primary purchase

### Secondary market

Existing patterns remain, but the seeded demo should avoid accidental listing expiry during testing by using long-enough expiry timestamps.

## Testing Strategy

### Backend

Add tests for:
- primary SKU list endpoint
- purchase-intent creation with stock available
- out-of-stock rejection
- successful HSP finalize path causing stock decrement + one mint
- duplicate finalize does not double-mint or double-decrement stock
- local demo seed creates three machines and two active listings deterministically

### Frontend

Add tests for:
- market page renders primary issuance section
- stock count and fixed SKU data display correctly
- primary buy CTA triggers purchase intent flow
- market page still renders secondary listings beneath primary section
- seeded owner/buyer views remain consistent

### Local E2E

The expected manual walkthrough after this slice:
- start stack with `./scripts/start_local_browser_demo.sh`
- connect buyer wallet
- verify primary stock displays
- verify two secondary listings display
- buy one secondary listing
- switch to owner wallet and create/cancel listing on the unlisted machine
- trigger primary issuance purchase via HSP stub/demo path and verify a new machine appears

## Files Expected To Change

### Backend

Likely modify:
- `code/backend/scripts/prepare_local_browser_demo.py`
- `code/backend/app/api/routes/payments.py`
- `code/backend/app/api/routes/machines.py` or a new primary issuance route module
- `code/backend/app/domain/models.py`
- `code/backend/app/core/container.py`
- local seed / test files under `code/backend/tests/...`

Likely add:
- `code/backend/app/api/routes/primary_issuance.py`
- `code/backend/app/schemas/primary_issuance.py`
- persistence model(s) for SKU inventory / primary purchase
- tests for primary issuance and seed behavior

### Frontend

Likely modify:
- `forge-yield-ai/src/pages/NodeMarket.tsx`
- `forge-yield-ai/src/hooks/use-outcomex-api.ts`
- `forge-yield-ai/src/lib/api/outcomex-types.ts`
- `forge-yield-ai/src/lib/api/query-keys.ts`
- `forge-yield-ai/src/test/...`

Likely add:
- primary issuance API helper
- optional dedicated primary issuance card component if the page gets too large

### Contracts

No contract changes required by design unless local seed needs minor deploy-script helper tweaks.

## Non-Goals

This slice does not attempt to:
- move primary inventory onchain
- make Gemma user-selectable
- redesign HSP integration deeply
- unify primary issuance into existing order/settlement machinery
- change secondary-market trade ownership semantics

## Success Criteria

The slice is successful when:
- local demo starts with one buyer, three owners, three machines, and two active secondary listings
- market page clearly separates primary issuance from secondary listings
- primary issuance shows fixed Apple 96GB + Qwen config, price `3.9`, and stock count
- successful primary purchase via HSP path mints a new machine exactly once and decrements stock exactly once
- secondary-market buy/list/cancel still work against chain + backend projection
- you can demo both buyer-side and owner-side stories from a fresh local stack without manual chain surgery
