-- Migración simple de PAYMENTS (T-W05-3): una sola tabla, sin framework de
-- migraciones (regla de "capas simples" — nada de Alembic/SQLAlchemy para un
-- único CRUD). `RepositorioCobrosPostgres.preparar_esquema` ejecuta este
-- archivo tal cual al arrancar la app (ver app/application/repositorio_cobros.py).
--
-- Idempotencia real (CA-W05-04): `clave_idempotencia` es UNIQUE. El INSERT de
-- un cobro usa `ON CONFLICT (clave_idempotencia) DO NOTHING RETURNING ...`,
-- así que un mismo intento recibido dos veces (incluso en paralelo) nunca
-- crea dos filas: Postgres serializa el conflicto con el lock del índice
-- único, sin necesidad de un SELECT-luego-INSERT con carrera.
--
-- Dinero como texto decimal, nunca float (regla de contratos): `monto` es
-- TEXT, no NUMERIC ni FLOAT, para no perder la representación exacta que
-- envía el cliente. El balance (`SUM`) hace el cast a NUMERIC solo para la
-- agregación y vuelve a TEXT en la respuesta.
--
-- Cero datos de tarjeta (regla de arquitectura #6): `token_medio_pago` es el
-- token opaco del proveedor PCI-DSS, no un dato de tarjeta real — se
-- persiste tal cual, sin cifrado adicional (ver README, sección de huecos).

CREATE TABLE IF NOT EXISTS cobros (
    id TEXT PRIMARY KEY,
    clave_idempotencia TEXT NOT NULL UNIQUE,
    poliza_id TEXT NOT NULL,
    monto TEXT NOT NULL,
    moneda TEXT NOT NULL,
    token_medio_pago TEXT NOT NULL,
    estado TEXT NOT NULL,
    referencia TEXT,
    motivo TEXT,
    intentos INTEGER NOT NULL DEFAULT 1,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cobros_poliza ON cobros (poliza_id);
CREATE INDEX IF NOT EXISTS idx_cobros_estado ON cobros (estado);
