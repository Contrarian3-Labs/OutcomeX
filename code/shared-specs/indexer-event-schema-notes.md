# OutcomeX Indexer Event Schema Notes (Current Contract Surface)

These notes document the currently normalized event names and payload fields in
`code/backend/app/indexer/events.py`, aligned to the active contracts:
`MachineAssetNFT`, `OrderBook`, `SettlementController`, `RevenueVault`, and `PWRToken`/`SimpleERC20`.

## MachineAssetNFT

- `MachineMinted`
  - Expected args: `machineId`, `owner`, `tokenURI`
  - Normalizes to `MachineAssetEvent`.
- `Transfer` (ERC721 transfer where args include `tokenId`)
  - Expected args: `from`, `to`, `tokenId`
  - Normalizes owner changes to `MachineAssetEvent`.

## Order lifecycle (`OrderBook` + `SettlementController`)

- `OrderCreated`
- `OrderClassified`
- `OrderPaid`
- `PreviewReady`
- `OrderSettled`
- `Settled` (from `SettlementController`)
- Shared expected args: `orderId`; optional `machineId`, `buyer`; settlement events use `kind`.
- Amount source:
  - `OrderCreated`/`OrderPaid`: `grossAmount`
  - `OrderSettled`: derived from `refundToBuyer + platformShare + machineShare`
  - `Settled`: `grossAmount`

`kind` mapping to normalized order status:
- `0` or `Confirmed` -> `CONFIRMED`
- `1` or `RejectedValidPreview` -> `REJECTED`
- `2` or `FailedOrNoValidPreview` -> `REFUNDED`

## Revenue settlement and claims (`RevenueVault` + `SettlementController`)

- `RevenueAccrued`
  - Expected args: `machineId`, `orderId`, `machineOwner`, `amount`, `dividendEligible`
  - Normalizes to `SettlementSplitEvent` with role:
    - `MACHINE_OWNER_DIVIDEND` when dividend-eligible
    - `MACHINE_OWNER_NON_DIVIDEND` otherwise
- `RevenueClaimed`
  - Expected args: `machineId`, `machineOwner`, `amount`
  - Normalizes to `RevenueClaimedEvent`.
- `RefundClaimed`
  - Expected args: `buyer`, `amount`
  - Normalizes to `RevenueClaimedEvent`.
- `PlatformRevenueClaimed`
  - Expected args: `treasury`, `amount`
  - Normalizes to `RevenueClaimedEvent`.

## PWR token mint surface (`PWRToken`/`SimpleERC20`)

- `Transfer` when `from == 0x0000000000000000000000000000000000000000`
  - Expected args: `from`, `to`, `value`
  - Normalizes to `PWRMintedEvent` with reason `MINT`.

## Unsupported event handling

Events outside this normalized surface are intentionally treated as unsupported.
`ReplayIndexer` skips unsupported events safely and continues applying supported ones.
