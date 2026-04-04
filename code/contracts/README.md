# OutcomeX Contracts (Foundry)

First-pass OutcomeX contract suite for machine assets, order lifecycle, settlement logic,
and machine-side value accrual using PWR.

## Key v1 assumptions

- HSP payment rail is not integrated yet; `OrderBook.markOrderPaid` is adapter-driven.
- USDT custody is conceptual accounting only in this MVP.
- Machine-side claimable value is represented by minted `PWR` in `RevenueVault`.
