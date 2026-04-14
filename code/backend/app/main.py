import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.middleware.attachment_upload_limit import AttachmentUploadSizeLimitMiddleware
from app.api.router import create_api_router
from app.api.routes.payments import sync_pending_hsp_payments_once
from app.core.container import get_container
from app.core.config import Settings
from app.db.base import Base
from app.indexer.execution_sync import sync_execution_runs_once
from app.integrations.onchain_indexer import get_onchain_indexer_poll_seconds


@asynccontextmanager
async def lifespan(_: FastAPI):
    container = get_container()
    tasks: list[asyncio.Task] = []
    if container.settings.auto_create_tables:
        Base.metadata.create_all(bind=container.engine)

    onchain_indexer = container.onchain_indexer
    _ensure_onchain_runtime_ready(container=container, onchain_indexer=onchain_indexer)

    if container.settings.execution_sync_enabled:
        tasks.append(
            asyncio.create_task(
                _execution_sync_worker(container=container),
                name="outcomex-execution-sync-worker",
            )
        )

    if container.settings.hsp_poll_enabled:
        tasks.append(
            asyncio.create_task(
                _hsp_payment_sync_worker(container=container),
                name="outcomex-hsp-payment-sync-worker",
            )
        )

    if getattr(onchain_indexer, "status", None) and onchain_indexer.status.enabled:
        tasks.append(
            asyncio.create_task(
                _onchain_indexer_worker(onchain_indexer=onchain_indexer, settings=container.settings),
                name="outcomex-onchain-indexer-worker",
            )
        )

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks)


async def _execution_sync_worker(*, container) -> None:
    interval_seconds = _execution_sync_poll_seconds(container.settings)
    while True:
        try:
            await asyncio.to_thread(
                sync_execution_runs_once,
                session_factory=container.session_factory,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep worker alive; transient AgentSkillOS/DB errors should not stop sync loop.
            pass
        await asyncio.sleep(interval_seconds)


async def _hsp_payment_sync_worker(*, container) -> None:
    interval_seconds = _hsp_poll_seconds(container.settings)
    while True:
        try:
            await asyncio.to_thread(
                sync_pending_hsp_payments_once,
                session_factory=container.session_factory,
                container=container,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep worker alive; transient HSP/network errors should not stop polling.
            pass
        await asyncio.sleep(interval_seconds)


async def _onchain_indexer_worker(*, onchain_indexer, settings: Settings) -> None:
    interval_seconds = get_onchain_indexer_poll_seconds(default=2.0, settings=settings)
    while True:
        try:
            await asyncio.to_thread(onchain_indexer.poll_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep worker alive; transient RPC/indexer errors should not stop polling.
            pass
        await asyncio.sleep(interval_seconds)


def _ensure_onchain_runtime_ready(*, container, onchain_indexer) -> None:
    if container.settings.env.strip().lower() != "prod":
        return

    indexer_status = getattr(onchain_indexer, "status", None)
    if indexer_status is None or not indexer_status.enabled:
        reason = getattr(indexer_status, "reason", "unavailable")
        raise RuntimeError(f"Onchain runtime required in prod: indexer_unavailable:{reason}")

    health_report = container.onchain_health_report
    if health_report.healthy:
        return

    details = ", ".join(health_report.errors[:3]) or "healthcheck_failed"
    raise RuntimeError(f"Onchain runtime required in prod: unhealthy:{details}")


def _execution_sync_poll_seconds(settings: Settings, *, default: float = 2.0) -> float:
    return settings.execution_sync_poll_seconds if settings.execution_sync_poll_seconds > 0 else default


def _hsp_poll_seconds(settings: Settings, *, default: float = 5.0) -> float:
    return settings.hsp_poll_seconds if settings.hsp_poll_seconds > 0 else default


def create_app() -> FastAPI:
    container = get_container()
    app = FastAPI(
        title=container.settings.app_name,
        version=container.settings.app_version,
        lifespan=lifespan,
    )
    app.add_middleware(AttachmentUploadSizeLimitMiddleware)
    app.include_router(create_api_router(), prefix=container.settings.api_prefix)
    return app


app = create_app()

