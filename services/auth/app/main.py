"""AUTH: consentimiento e identidad."""

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-auth", version="0.1.0")

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "auth", "estado": "ok"}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
