from typing import Protocol


class ExecutionGateway(Protocol):
    def enqueue_order(self, order_id: str) -> str:
        ...


class NullExecutionGateway:
    """Temporary noop gateway until execution engine integration is available."""

    def enqueue_order(self, order_id: str) -> str:
        return f"mock-exec-{order_id}"

