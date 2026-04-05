# OutcomeX Contracts (Foundry)

This contract set now includes a reviewed payment-router subset on top of the core OutcomeX lifecycle.

## Current scope

Included:

- `MachineAssetNFT` for hosted machine ownership
- `OrderBook` for order lifecycle and transfer guards
- `SettlementController` for settlement accounting
- `RevenueVault` for machine-side PWR accrual and claims
- `OrderPaymentRouter` for direct stablecoin payment entry

## Direct payment status

Implemented and tested:

- `USDC` direct pay via EIP-3009-style authorization
- `USDT` direct pay via Permit2-style transfer
- real token escrow into `SettlementController`
- real refund claims in the original payment token
- real platform revenue claims in the original payment token
- stablecoin reserve left in `SettlementController` as backing after payout splits

Intentionally gated for now:

- `PWR` direct payment is disabled until a proper quote / anchor / conversion layer exists

## Important current assumptions

- For legacy lifecycle-only tests, `address(0)` payment token still represents conceptual accounting without real token movement.
- For direct stablecoin payments, settlement and refund are real token flows, not receipt-only bookkeeping.
- Machine-side value still accrues as minted `PWR` in `RevenueVault`.
