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
- `USDT` direct pay via Permit2-style transfer
- `PWR` direct pay via ERC-20 approve + transferFrom
- real token escrow into `SettlementController`
- real refund claims in the original payment token
- real platform revenue claims in the original payment token
- stablecoin reserve left in `SettlementController` as backing after payout splits
- paid-in `PWR` stays locked in `SettlementController` while machine-side value still accrues as minted `PWR`

Current PWR anchor assumption:

- the current repo uses a minimal backend-priced deterministic anchor
- the quote metadata is versioned and can be replaced later by a richer anchor / conversion layer

## Important current assumptions

- For legacy lifecycle-only tests, `address(0)` payment token still represents conceptual accounting without real token movement.
- For direct stablecoin payments, settlement and refund are real token flows, not receipt-only bookkeeping.
- Machine-side value still accrues as minted `PWR` in `RevenueVault`.
- For direct `PWR` payments, buyer-paid `PWR` is escrowed in `SettlementController`, refundable/platform-claimable in `PWR`, and the machine-side accrual remains minted `PWR`.
