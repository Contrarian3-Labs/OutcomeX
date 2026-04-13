from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.machines import MACHINE_ASSET_COST_CENTS, MOCK_MACHINE_SPEC
from app.domain.models import Machine, Order, RevenueEntry
from app.runtime.cost_service import get_runtime_cost_service
from app.schemas.network import NetworkOverviewResponse

router = APIRouter()

_CAPABILITY_LABELS = {
    "image_generation": "Image",
    "video_generation": "Video",
    "text_reasoning": "Text",
    "multimodal": "Multimodal",
    "agentic_workflows": "Agentic",
}


def _supported_capability_labels() -> list[str]:
    raw_categories = MOCK_MACHINE_SPEC.get("supported_categories", [])
    labels: list[str] = []
    for category in raw_categories:
        label = _CAPABILITY_LABELS.get(str(category), str(category).replace("_", " ").title())
        if label not in labels:
            labels.append(label)
    return labels


@router.get("/overview", response_model=NetworkOverviewResponse)
def get_network_overview(db: Session = Depends(get_db)) -> NetworkOverviewResponse:
    hosted_machines = int(db.scalar(select(func.count(Machine.id))) or 0)
    capability_labels = _supported_capability_labels() if hosted_machines > 0 else []
    trailing_cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    confirmed_deliveries_30d = int(
        db.scalar(
            select(func.count(Order.id)).where(
                Order.result_confirmed_at.is_not(None),
                Order.result_confirmed_at >= trailing_cutoff,
            )
        )
        or 0
    )

    trailing_30d_machine_revenue_cents = int(
        db.scalar(
            select(func.coalesce(func.sum(RevenueEntry.machine_share_cents), 0))
            .join(Order, Order.id == RevenueEntry.order_id)
            .where(
                Order.result_confirmed_at.is_not(None),
                Order.result_confirmed_at >= trailing_cutoff,
            )
        )
        or 0
    )

    acquisition_total_cents = hosted_machines * MACHINE_ASSET_COST_CENTS
    indicative_realized_apr_network = (
        round((trailing_30d_machine_revenue_cents * 12 * 100) / acquisition_total_cents, 2)
        if acquisition_total_cents > 0
        else 0.0
    )

    return NetworkOverviewResponse(
        hosted_machines=hosted_machines,
        live_capability_families=len(capability_labels),
        live_capability_family_labels=capability_labels,
        confirmed_deliveries_30d=confirmed_deliveries_30d,
        indicative_realized_apr_network=indicative_realized_apr_network,
        pwr_anchor_price_cents=get_runtime_cost_service().pwr_anchor_price_cents,
    )
