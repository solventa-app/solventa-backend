# Observabilidad: trazas distribuidas

Qué hace cada servicio para que un flujo (por ejemplo una oferta) se pueda seguir de punta a punta, incluidas las
consultas a las bases de datos. Para **verlo**, ver `observabilidad/README.md` (Grafana local contra Google Cloud).

## Cómo funciona

- Cada servicio (los 7 y los 3 stubs) llama a `configurar_trazas(...)` de su `app/observabilidad.py` al arrancar. Es el mismo
  archivo en todos (los servicios no comparten código); solo cambia la lista de instrumentos.
- Está **apagado por defecto**: solo se activa con `TRAZAS_GCP=true`, que Terraform pone en Cloud Run desde
  `.github/servicios.json`. En local y en CI no se importa nada de OpenTelemetry y los logs no cambian.
- Los tramos viajan por OTLP/HTTP a la **API de Telemetry** de Google (`telemetry.googleapis.com`) con la identidad de la
  cuenta de servicio del servicio (rol `roles/telemetry.tracesWriter`, en `infra/servicios.tf`). Se leen en Cloud Trace.
- El contexto se propaga entre servicios con el encabezado `traceparent`, que añaden las llamadas `httpx`. Por eso la traza de
  una oferta empieza en el BFF y sigue por el ACL, los stubs, RATING y RISK.
- Los logs pasan a una línea JSON con `logging.googleapis.com/trace`, para saltar de un log a su traza (y al revés).

| Servicio | Tramos de entrada | Salidas instrumentadas |
|---|---|---|
| `bff` | FastAPI + una operación GraphQL (`GraphQL mutation solicitarOferta`) | `httpx` |
| `auth`, `payments` | FastAPI | `httpx`, Postgres (`psycopg`) |
| `risk` | FastAPI | `httpx`, MongoDB (`pymongo`) |
| `rating` | FastAPI | `httpx`, MongoDB, Redis |
| `acl-worker`, `consolidador-fuentes` | FastAPI | `httpx`, Redis |
| stubs | FastAPI | — |

## Privacidad (regla 6)

Las trazas no llevan datos personales:

- **Postgres:** el tramo lleva el texto de la sentencia con marcadores (`%(id)s`), nunca los parámetros.
- **MongoDB:** no se captura la sentencia (`capture_statement=False`); solo la operación y la colección.
- **Redis:** los argumentos se enmascaran (`GET ?`).
- **GraphQL:** el tramo lleva el tipo y los campos de primer nivel; **no** el texto de la consulta ni las variables
  (podrían traer un documento o una clave). Ver `services/bff/app/trazas_graphql.py` y su prueba.
- **HTTP:** el nombre del tramo es la ruta con plantilla; la ruta real solo trae identificadores opacos (el `cliente_id` es
  un hash), nunca un documento.

## Para tener en cuenta

- **CPU limitada en Cloud Run:** el envío de tramos es en segundo plano (cada ~1 s), y entre peticiones Cloud Run limita la
  CPU. Los tramos de las últimas peticiones antes de un silencio pueden llegar con retraso, hasta la siguiente petición o al
  apagarse la instancia. Se resuelve con CPU siempre asignada (decisión pendiente, ver la memoria del proyecto).
- **Arranque:** cargar OpenTelemetry suma tiempo de importación al arranque en frío.
- **Muestreo:** `TRAZAS_MUESTREO` (0 a 1, por defecto 1: todas las trazas). Con el volumen de staging cabe en la capa gratuita
  de Cloud Trace; si el tráfico crece, bajarlo.
- **Un fallo no tumba el servicio:** si faltan credenciales o la API no responde, se registra un aviso y el servicio sigue sin
  trazas.
- **Apagarlas:** quitar `TRAZAS_GCP` de `.github/servicios.json` (o ponerlo en `false`) y volver a desplegar.
