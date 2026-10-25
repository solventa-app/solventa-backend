"""Dummy de siniestros para el back-office (HU-W04, KAN-27).

Microservicio autónomo: sin base de datos, sin tokens y sin llamar a ningún otro servicio. Expone
su propio GraphQL en `/graphql/siniestros`, sirve las fotos de evidencia en
`/graphql/siniestros/medios/` y guarda el estado en memoria (las acciones lo cambian;
`POST /graphql/siniestros/control/reiniciar` vuelve a los datos de `app/datos/`).

Es un sustituto temporal: cuando exista el servicio real de siniestros, el front solo cambia la URL
a la que llama. Por ahora corre solo en local (ver `README.md`).
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from strawberry.fastapi import GraphQLRouter

from app.esquema import schema
from app.observabilidad import configurar_trazas
from app.repositorio import DATOS, Repositorio

PREFIJO = "/graphql/siniestros"


def create_app(repo: Repositorio | None = None) -> FastAPI:
    app = FastAPI(title="solventa-stub-siniestros", version="0.1.0")
    repo = repo or Repositorio()

    async def contexto() -> dict:
        return {"repo": repo}

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "stub-siniestros", "estado": "ok"}

    @app.post(f"{PREFIJO}/control/reiniciar", tags=["control"])
    def reiniciar() -> dict[str, int]:
        """Vuelve a los datos originales del JSON (para demos y pruebas E2E)."""
        return {"siniestros": repo.reiniciar()}

    app.include_router(GraphQLRouter(schema, context_getter=contexto), prefix=PREFIJO)
    app.mount(f"{PREFIJO}/medios", StaticFiles(directory=DATOS / "medios"), name="medios")

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


configurar_trazas("stub-siniestros")

app = create_app()
