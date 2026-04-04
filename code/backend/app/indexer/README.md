# OutcomeX Indexer First Pass

This folder contains a first-pass skeleton for the on-chain indexing and projection layer.

## Scope implemented

- `app.onchain.adapter` provides EVM/web3.py-friendly adapter boundaries:
  - subscription metadata
  - raw log normalization
  - decoded event transport
  - chunked block replay via `Web3ChainAdapter`
- `app.indexer.events` defines normalized domain event models for:
  - machine assets
  - order lifecycle
  - settlement split
  - revenue claimed
  - transfer guard updated
  - PWR minted
- `app.indexer.replay` adds replay loop, cursor handling, confirmation-depth guardrails, and idempotent skip behavior.
- `app.indexer.projections` adds read-model interfaces and an in-memory projection store for orders, machine assets, revenue, and transfer eligibility.

## Assumptions

- Smart contracts remain the source of truth. Indexer projections are query acceleration only.
- Events are processed in log order from the adapter. Reorg handling is currently "skip removed logs" and should be expanded to compensate state later.
- Event decoding strategy is intentionally pluggable (`EventDecoder` protocol) because ABI ownership and deploy topology are expected to evolve.
- Confirmation depth defaults to `6` and should be tuned per chain/network stability policy.
- This pass stores cursor/idempotency in memory only; persistent stores should back these interfaces in production.
