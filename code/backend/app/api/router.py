from fastapi import APIRouter

from app.api.routes.chat_plans import router as chat_plans_router
from app.api.routes.debug import router as debug_router
from app.api.routes.execution_runs import router as execution_runs_router
from app.api.routes.health import router as health_router
from app.api.routes.hsp_webhooks import router as hsp_webhooks_router
from app.api.routes.machines import router as machines_router
from app.api.routes.orders import router as orders_router
from app.api.routes.payments import router as payments_router
from app.api.routes.revenue import router as revenue_router
from app.api.routes.self_use import router as self_use_router
from app.api.routes.settlement import router as settlement_router
from app.core.config import get_settings


def create_api_router() -> APIRouter:
    api_router = APIRouter()
    api_router.include_router(health_router, tags=["health"])
    api_router.include_router(chat_plans_router, prefix="/chat", tags=["chat"])
    api_router.include_router(self_use_router, prefix="/self-use", tags=["self-use"])
    api_router.include_router(execution_runs_router, prefix="/execution-runs", tags=["execution-runs"])
    api_router.include_router(orders_router, prefix="/orders", tags=["orders"])
    api_router.include_router(machines_router, prefix="/machines", tags=["machines"])
    api_router.include_router(settlement_router, prefix="/settlement", tags=["settlement"])
    api_router.include_router(revenue_router, prefix="/revenue", tags=["revenue"])
    api_router.include_router(payments_router, prefix="/payments", tags=["payments"])
    api_router.include_router(hsp_webhooks_router, prefix="/payments/hsp", tags=["payments", "webhooks"])
    if get_settings().env in {"dev", "test"}:
        api_router.include_router(debug_router, prefix="/debug", tags=["debug"])
    return api_router


api_router = create_api_router()
