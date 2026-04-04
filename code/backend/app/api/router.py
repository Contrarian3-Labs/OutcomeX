from fastapi import APIRouter

from app.api.routes.chat_plans import router as chat_plans_router
from app.api.routes.health import router as health_router
from app.api.routes.hsp_webhooks import router as hsp_webhooks_router
from app.api.routes.machines import router as machines_router
from app.api.routes.orders import router as orders_router
from app.api.routes.payments import router as payments_router
from app.api.routes.revenue import router as revenue_router
from app.api.routes.settlement import router as settlement_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(chat_plans_router, prefix="/chat", tags=["chat"])
api_router.include_router(orders_router, prefix="/orders", tags=["orders"])
api_router.include_router(machines_router, prefix="/machines", tags=["machines"])
api_router.include_router(settlement_router, prefix="/settlement", tags=["settlement"])
api_router.include_router(revenue_router, prefix="/revenue", tags=["revenue"])
api_router.include_router(payments_router, prefix="/payments", tags=["payments"])
api_router.include_router(hsp_webhooks_router, prefix="/payments/hsp", tags=["payments", "webhooks"])
