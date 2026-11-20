"""Scopes del token del socio (HA-SEG-004, EC025). Cada ruta `/v1` con token declara el suyo y el
BFF lo exige en cada llamada, antes de cualquier otra cosa (presupuesto, modo prueba u otro
servicio). Deben coincidir con `SCOPES_SOCIO` de AUTH (`services/auth/app/tokens_socio.py`)."""

COTIZACIONES = "cotizaciones"
PROPUESTAS = "propuestas"
POLIZAS = "polizas"
CUENTA = "cuenta"
TODOS = frozenset({COTIZACIONES, PROPUESTAS, POLIZAS, CUENTA})
