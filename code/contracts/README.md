# OutcomeX Contracts (Foundry)

This contract set now includes a reviewed payment-router subset on top of the core OutcomeX lifecycle.

## Current scope

Included:

- `MachineAssetNFT` for hosted machine ownership
- `OrderBook` for order lifecycle and transfer guards
- `SettlementController` for settlement accounting
- `RevenueVault` for machine-side PWR accrual and claims
- `OrderPaymentRouter` for direct stablecoin and PWR payment entry

## Direct payment status

Implemented and tested:

- `USDC` direct pay via EIP-3009-style authorization
- `USDT` direct pay via ERC-20 approve + transferFrom
- `PWR` direct pay via ERC-20 approve + transferFrom
- real token escrow into `SettlementController`
- real refund claims in the original payment token
- real platform revenue claims in the original payment token
- stablecoin reserve left in `SettlementController` as backing after payout splits
- paid-in `PWR` stays locked in `SettlementController` while machine-side value still accrues as minted `PWR`
- HSP adapter path (`createPaidOrderByAdapter`) now performs a real `transferFrom` of `paymentToken` into `SettlementController` escrow before marking the order as paid

Current PWR anchor assumption:

- the current repo uses a minimal backend-priced deterministic anchor
- the quote metadata is versioned and can be replaced later by a richer anchor / conversion layer

## Important current assumptions

- For legacy lifecycle-only tests, `address(0)` payment token still represents conceptual accounting without real token movement.
- For direct stablecoin payments, settlement and refund are real token flows, not receipt-only bookkeeping.
- Machine-side value still accrues as minted `PWR` in `RevenueVault`.
- For direct `PWR` payments, buyer-paid `PWR` is escrowed in `SettlementController`, refundable/platform-claimable in `PWR`, and the machine-side accrual remains minted `PWR`.

## Local deployment

The repository now includes a local deployment script that deploys:

- `MockUSDCWithAuthorization`
- `MockUSDT`
- `PWRToken`
- `MachineAssetNFT`
- `RevenueVault`
- `SettlementController`
- `OrderBook`
- `OrderPaymentRouter`

and wires all required links (`setSettlementEscrow`, `setOrderBook`, `setPaymentAdapter`, transfer guard, PWR minter, etc.).

Run against a local Anvil node:

```bash
anvil
```

```bash
cd code/contracts
forge script script/DeployLocal.s.sol:DeployLocal \
  --rpc-url http://127.0.0.1:8545 \
  --private-key <ANVIL_PRIVATE_KEY> \
  --broadcast \
  -vvvv
```

Optional explicit owner/treasury/machine-owner:

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

The script emits `DeploymentAddress(name, addr)` and `DeploymentMachineId(machineId)` so you can copy deployed addresses directly from logs.
