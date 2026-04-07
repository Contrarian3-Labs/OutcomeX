import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.router import create_api_router
from app.core.container import get_container
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

    if _is_feature_enabled("OUTCOMEX_EXECUTION_SYNC_ENABLED", default=True):
        tasks.append(
            asyncio.create_task(
                _execution_sync_worker(container=container),
                name="outcomex-execution-sync-worker",
            )
        )

    if getattr(onchain_indexer, "status", None) and onchain_indexer.status.enabled:
        tasks.append(
            asyncio.create_task(
                _onchain_indexer_worker(onchain_indexer=onchain_indexer),
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
    interval_seconds = _env_positive_float("OUTCOMEX_EXECUTION_SYNC_POLL_SECONDS", default=2.0)
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


async def _onchain_indexer_worker(*, onchain_indexer) -> None:
    interval_seconds = get_onchain_indexer_poll_seconds(default=2.0)
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


def _env_positive_float(name: str, *, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _is_feature_enabled(name: str, *, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def create_app() -> FastAPI:
    container = get_container()
    app = FastAPI(
        title=container.settings.app_name,
        version=container.settings.app_version,
        lifespan=lifespan,
    )
    app.include_router(create_api_router(), prefix=container.settings.api_prefix)
    return app


app = create_app()

