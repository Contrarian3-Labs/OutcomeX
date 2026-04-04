from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.container import get_container
from app.db.base import Base


@asynccontextmanager
async def lifespan(_: FastAPI):
    container = get_container()
    if container.settings.auto_create_tables:
        Base.metadata.create_all(bind=container.engine)
    yield


def create_app() -> FastAPI:
    container = get_container()
    app = FastAPI(
        title=container.settings.app_name,
        version=container.settings.app_version,
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix=container.settings.api_prefix)
    return app


app = create_app()

