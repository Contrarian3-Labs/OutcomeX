from typing import Protocol


class OnchainIndexer(Protocol):
    def mark_settlement_distributed(self, settlement_id: str) -> None:
        ...


class NullOnchainIndexer:
    """Temporary noop boundary for future on-chain settlement indexing."""

    def mark_settlement_distributed(self, settlement_id: str) -> None:
        return None

