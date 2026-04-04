from dataclasses import dataclass


@dataclass(frozen=True)
class ContractTarget:
    chain_id: int
    contract_name: str
    contract_address: str


class ContractsRegistry:
    def __init__(
        self,
        *,
        chain_id: int = 133,
        order_book_address: str = "0x0000000000000000000000000000000000000133",
    ) -> None:
        self._order_book = ContractTarget(
            chain_id=chain_id,
            contract_name="OrderBook",
            contract_address=order_book_address,
        )

    def order_book(self) -> ContractTarget:
        return self._order_book
