"""RISK: único escritor del perfil de riesgo."""

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-risk", version="0.1.0")

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "risk", "estado": "ok"}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
