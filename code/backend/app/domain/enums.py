from enum import Enum


class OrderState(str, Enum):
    DRAFT = "draft"
    PLAN_RECOMMENDED = "plan_recommended"
    USER_CONFIRMED = "user_confirmed"
    EXECUTING = "executing"
    RESULT_PENDING_CONFIRMATION = "result_pending_confirmation"
    RESULT_CONFIRMED = "result_confirmed"
    CANCELLED = "cancelled"


class PaymentState(str, Enum):
    CREATED = "created"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


class ExecutionState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionRunStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PreviewState(str, Enum):
    DRAFT = "draft"
    GENERATING = "generating"
    READY = "ready"
    EXPIRED = "expired"


class SettlementState(str, Enum):
    NOT_READY = "not_ready"
    READY = "ready"
    LOCKED = "locked"
    DISTRIBUTED = "distributed"

