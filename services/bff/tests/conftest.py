"""Configuración común de las pruebas del BFF."""

import os

# El límite de tasa por credencial de socio (KAN-217) se apaga en las pruebas que no son de él:
# los dobles responden en microsegundos y cualquier flujo pasaría de 5 llamadas por segundo. Sus
# propias pruebas (`test_socios_limite_tasa.py`) lo arman con su tope.
os.environ.setdefault("LIMITE_SOCIO_RPS_PRODUCCION", "0")
os.environ.setdefault("LIMITE_SOCIO_RPS_PRUEBAS", "0")
