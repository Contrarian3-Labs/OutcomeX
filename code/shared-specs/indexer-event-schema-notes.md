# OutcomeX Indexer Event Schema Notes (First Pass)

These notes document the event names and payload fields expected by `code/backend/app/indexer/events.py`.

## MachineAsset

- `MachineAssetRegistered`
- `MachineAssetUpdated`
- Expected args: `machineId`, `owner`, optional `metadataURI`, optional `pwrQuota`

## Order lifecycle

- `OrderOpened`
- `OrderMatched`
- `OrderResultSubmitted`
- `OrderResultConfirmed`
- `OrderSettled`
- `OrderCancelled`
- Expected args: `orderId`, optional `machineId`, optional `buyer`, optional `amountWei`, optional `status`

## SettlementSplit

- `SettlementSplit`
- Expected args: `orderId`, `recipient`, `amountWei`, optional `role`, optional `bps`

## RevenueClaimed

- `RevenueClaimed`
- Expected args: `account`, `amountWei`, optional `nonce`

## TransferGuardUpdated

- `TransferGuardUpdated`
- Expected args: `assetId`/`machineId`/`tokenId`, `isTransferable`, optional `reason`, optional `activeTasks`, optional `unsettledRevenue`

## PWRMinted

- `PWRMinted`
- Expected args: `to`, `amountWei`, optional `reason`
