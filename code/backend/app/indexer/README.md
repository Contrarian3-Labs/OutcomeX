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
- Events are canonicalized in chain/log order before projection apply so cross-subscription replay remains deterministic.
- Reorg handling is intentionally conservative in this MVP: removed logs mark an unsafe block boundary, replay applies only blocks below that boundary, cursor advancement is capped to the last safe block, and the replay outcome flags a rewind requirement.
- Event decoding strategy is intentionally pluggable (`EventDecoder` protocol) because ABI ownership and deploy topology are expected to evolve.
- Confirmation depth defaults to `6` and should be tuned per chain/network stability policy.
- This pass stores cursor/idempotency in memory only; persistent stores should back these interfaces in production.
