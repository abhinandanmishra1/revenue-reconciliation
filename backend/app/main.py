import logging

from fastapi import FastAPI

from app import errors
from app.config import get_settings
from app.routers import auth, explain, imports
from app.security.origin import OriginCheckMiddleware

logging.basicConfig(level=logging.INFO)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Revenue Reconciliation API", docs_url=None, redoc_url=None, openapi_url=None)
    errors.install(app)
    app.add_middleware(OriginCheckMiddleware, allowed=settings.app_origins)
    app.include_router(auth.router)
    app.include_router(imports.router)
    app.include_router(explain.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
