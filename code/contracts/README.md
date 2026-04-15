# OutcomeX Contracts

`code/contracts` contains the onchain protocol for OutcomeX. These contracts define ownership, payment truth, settlement, refunds, claims, and transfer safety for machine-backed delivery assets.

## Protocol components

### Machine assets

- `MachineAssetNFT` represents hosted machine ownership onchain.
- `MachineMarketplace` supports fixed-price listing and purchase flows.

### Order and payment lifecycle

- `OrderBook` tracks order lifecycle and enforces transfer guards.
- `OrderPaymentRouter` handles supported payment-entry paths.

### Settlement and revenue

- `SettlementController` computes refunds, platform share, and machine-side value distribution.
- `RevenueVault` tracks machine-side claimable revenue and unsettled balances.
- `PWRToken` is the protocol token used for direct pay and machine-side payout accounting.

## Economic model

The contract set expresses a simple economic loop:

1. a buyer pays for an outcome
2. the order becomes paid onchain
3. the buyer later confirms or rejects the result
4. settlement logic routes value into refund, platform, and machine-side buckets
5. machine-side value becomes claimable
6. transfer guards block machine transfers while unresolved obligations remain

That is why the machine asset behaves like productive infrastructure rather than a cosmetic NFT.

## Payment modes currently implemented

### Direct payment

The router includes direct paths for:

- `USDC` via authorization-style payment
- `USDT` via `transferFrom`
- `PWR` via `transferFrom`

### HSP-backed payment

The router also supports an adapter-paid path where stablecoin payment is first confirmed offchain and then finalized onchain through `payOrderByAdapter`.

For product positioning, OutcomeX should present `PWR` direct and `USDC/USDT via HSP` as the main rails, even though direct stablecoin compatibility still exists in the contract surface.

## Settlement semantics

`SettlementController` handles three broad outcomes:

- confirmed delivery
- rejected valid preview
- failed or invalid-preview refund

It tracks:

- refundable balances
- platform-accrued balances
- token-specific claim paths
- machine-side accrual forwarded into `RevenueVault`

`RevenueVault` then converts eligible machine-side value into claimable `PWR` balances and keeps enough state to support transfer guards.

## Why this matters for DeFi and RWA

The contract layer is what makes OutcomeX financially credible:

- payment is not just UI state; it is onchain state
- confirmation and refund are not just backend flags; they are onchain decisions
- revenue is not just analytics; it becomes claimable protocol value
- ownership is not just profile metadata; it is a transfer-constrained asset with yield implications

This is the strongest technical basis for pitching OutcomeX as productive digital RWA infrastructure on top of a DeFi settlement model.

## Local deployment

Start a local chain:

```bash
anvil
```

Deploy the full local stack:

```bash
cd code/contracts
forge script script/DeployLocal.s.sol:DeployLocal \
  --rpc-url http://127.0.0.1:8545 \
  --private-key <ANVIL_PRIVATE_KEY> \
  --broadcast \
  -vvvv
```

Optional explicit config:

```bash
cd code/contracts
forge script script/DeployLocal.s.sol:DeployLocal \
  --rpc-url http://127.0.0.1:8545 \
  --private-key <ANVIL_PRIVATE_KEY> \
  --broadcast \
  --sig "runWithConfig(address,address,address)" \
  <INITIAL_OWNER> <PLATFORM_TREASURY> <MACHINE_OWNER> \
  -vvvv
```

The script deploys and wires:

- `MockUSDCWithAuthorization`
- `MockUSDT`
- `PWRToken`
- `MachineAssetNFT`
- `MachineMarketplace`
- `RevenueVault`
- `SettlementController`
- `OrderBook`
- `OrderPaymentRouter`

## Tests

Run the full suite:

```bash
cd code/contracts
forge test -vv
```

Important coverage areas:

- `OutcomeXLifecycle.t.sol` - order, settlement, refund, and claim flows
- `OrderPaymentRouter.t.sol` - payment router behavior across rails
- `MachineMarketplace.t.sol` - listing, buy, and transfer interactions
- `HashkeyNonStandardERC20.t.sol` - token behavior edge cases

## Related docs

- `../backend/README.md`
- `../../docs/onchain-boundary-and-event-driven-architecture-evaluation-cn.md`
- `../../docs/e2e-validation-2026-04-09-cn.md`
