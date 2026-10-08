"""Prueba de humo del ciclo de vida del trabajador: arrancar y detener no debe
lanzar ni colgarse, incluso sin Redis real (puerto sin nadie escuchando)."""

from app.worker import TrabajadorReconciliacion


def test_iniciar_y_detener_sin_redis_real_no_se_cuelga() -> None:
    trabajador = TrabajadorReconciliacion("redis://127.0.0.1:1/2")
    trabajador.iniciar()
    trabajador.detener()
