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
        order_payment_router_address: str = "0x0000000000000000000000000000000000000134",
        usdc_address: str = "0x79AEc4EeA31D50792F61D1Ca0733C18c89524C9e",
        usdt_address: str = "0x372325443233fEbaC1F6998aC750276468c83CC6",
        pwr_address: str = "0x0000000000000000000000000000000000000A11",
    ) -> None:
        self._order_book = ContractTarget(
            chain_id=chain_id,
            contract_name="OrderBook",
            contract_address=order_book_address,
        )
        self._payment_router = ContractTarget(
            chain_id=chain_id,
            contract_name="OrderPaymentRouter",
            contract_address=order_payment_router_address,
        )
        self._payment_tokens = {
            "USDC": usdc_address,
            "USDT": usdt_address,
            "PWR": pwr_address,
        }

    def order_book(self) -> ContractTarget:
        return self._order_book

    def payment_router(self) -> ContractTarget:
        return self._payment_router

    def payment_token(self, currency: str) -> str:
        normalized = currency.upper()
        if normalized not in self._payment_tokens:
            raise KeyError(normalized)
        return self._payment_tokens[normalized]
