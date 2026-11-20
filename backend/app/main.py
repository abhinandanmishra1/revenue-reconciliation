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

    # TEMPORARY: shows the caller only its own proxy headers (last IP part masked)
    # to verify TRUSTED_PROXY_HOPS on the live chain. Remove after verification.
    from fastapi import Request

    from app.security.client_ip import client_ip

    def _mask(value: str) -> str:
        out = []
        for part in value.split(","):
            ip = part.strip()
            sep = "." if "." in ip else ":"
            out.append(sep.join(ip.split(sep)[:-1] + ["x"]) if sep in ip else ip)
        return ", ".join(out)

    @app.get("/api/debug/forwarded")
    def debug_forwarded(request: Request) -> dict:
        names = ("x-forwarded-for", "x-real-ip", "x-vercel-forwarded-for", "x-envoy-external-address", "forwarded")
        return {
            "peer": _mask(request.client.host if request.client else ""),
            "headers": {n: (_mask(request.headers[n]) if n != "forwarded" else "<present>") for n in names if n in request.headers},
            "client_ip_used": _mask(client_ip(request, settings.trusted_proxy_hops, settings.client_ip_header.strip().lower())),
            "trusted_proxy_hops": settings.trusted_proxy_hops,
            "client_ip_header": settings.client_ip_header,
        }

    return app


app = create_app()
