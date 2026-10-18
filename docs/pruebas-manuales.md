# Pruebas manuales de todo el backend

Guía para correr **tú mismo, en tu terminal**, el backend completo y comprobar cada historia a mano: sesión y roles
(HU-M02), oferta con proveedores sanos y degradados (HU-W01) y cobros con conciliación (HU-W05). Es la guía de entrada: cada
tema tiene además su documento con el detalle.

| Quieres probar | Guía de detalle |
|---|---|
| Usuarios, roles, bloqueo y suspensión | [autenticacion-usuarios.md](autenticacion-usuarios.md) (sección 6) |
| Cobros, conciliación y nombre del cliente | [cobros-conciliacion.md](cobros-conciliacion.md) (sección 7) |
| Cómo está armado el BFF | [bff-implementacion.md](bff-implementacion.md) |

Todos los datos son ficticios. Los comandos se corren desde la **raíz del repo**, en **una sola terminal** (los pasos
reutilizan variables). Si cierras la terminal, vuelve a correr el paso 0 de la sección 2.

## 1. Requisitos y puertos

- Docker con Compose, `curl` y `python3` (solo para leer campos del JSON). No hace falta instalar Python ni dependencias
  del proyecto para estas pruebas.
- Si en tu máquina ya están ocupados el 5432 (Postgres) o el 6379 (Redis), antepón
  `POSTGRES_PORT=5433 REDIS_PORT=6380` a **cada** comando de `docker compose` (o expórtalas una vez en la terminal).

| Servicio | URL local | Para qué lo usas a mano |
|---|---|---|
| BFF (GraphQL) | http://localhost:8000/graphql/web | **La puerta de entrada**: casi todas las pruebas van aquí |
| AUTH | http://localhost:8001 | Usuarios, sesiones, consentimientos |
| RISK | http://localhost:8002 | Perfil de riesgo (MongoDB) |
| RATING | http://localhost:8003 | Cálculo de la oferta |
| PAYMENTS | http://localhost:8004 | Cobros |
| ACL Worker | http://localhost:8005 | Único contacto con proveedores; `GET /circuitos` |
| Consolidador de fuentes | http://localhost:8006 | Reconciliación de fuentes |
| Stub Open Finance / Open Data / Pasarela | http://localhost:8101 / 8102 / 8103 | Proveedores simulados; `POST /control/modo` los degrada |
| Prometheus / Grafana | http://localhost:9190 / http://localhost:3100 | Métricas y tableros (Grafana sin clave en local) |

Cada servicio publica su Swagger en `/docs` (p. ej. http://localhost:8004/docs) y sus métricas en `/metrics`.

## 2. Arranque

### Paso 0. Atajos

```bash
BFF=http://localhost:8000/graphql/web
AUTH=http://localhost:8001
PAY=http://localhost:8004
ACL=http://localhost:8005
OPEN_FINANCE=http://localhost:8101
PASARELA=http://localhost:8103

# gql '<cuerpo JSON>' [token]: envía una operación GraphQL, con token si se lo pasas.
gql() {
  if [ -n "$2" ]; then
    curl -s -X POST "$BFF" -H 'Content-Type: application/json' -H "Authorization: Bearer $2" -d "$1"
  else
    curl -s -X POST "$BFF" -H 'Content-Type: application/json' -d "$1"
  fi
  echo
}
# campo <ruta>: extrae un campo de la respuesta JSON (sin jq). Ejemplo: ... | campo data.yo.rol
campo() { python3 -c "import sys,json; d=json.load(sys.stdin); [d:=d[k] for k in sys.argv[1].split('.')]; print(d)" "$1"; }
# modo <url-del-stub> <sano|lento|error|caido|rechaza>: degrada o restaura un proveedor simulado.
modo() { curl -s -X POST "$1/control/modo" -H 'Content-Type: application/json' -d "{\"modo\":\"$2\"}"; echo; }
```

### Paso 1. Levantar todo

```bash
docker compose --profile servicios up -d --build --wait
```

Construye las imágenes, **migra** los esquemas (AUTH y PAYMENTS en Postgres, RISK en MongoDB), crea el primer BACKOFFICE
y espera a que todo esté sano. La primera vez tarda varios minutos.

**Esperado:** el comando termina sin error y todos los contenedores quedan `Healthy`. Si falla porque Postgres queda
`unhealthy` en el **primer** arranque con un volumen nuevo, repite el mismo comando: es un problema conocido del primer
`up` (ver "Si algo falla").

### Paso 2. Comprobar que todo responde

```bash
for p in 8000 8001 8002 8003 8004 8005 8006 8101 8102 8103; do
  echo "$p -> $(curl -s -o /dev/null -w '%{http_code}' localhost:$p/health)"
done
curl -s $ACL/circuitos
```

**Esperado:** diez líneas con `200` y los circuitos del ACL Worker en `closed` (cerrados = los proveedores se están
usando normalmente).

## 3. Sesión y roles (HU-M02)

El backend solo reconoce tres roles: `CLIENTE`, `ASESOR` y `BACKOFFICE`. El primer BACKOFFICE lo crea el arranque
(`admin.solventa` / `admin-local-solventa`, **solo local**); desde ahí se crean los demás.

### Paso 3. Entrar como BACKOFFICE y crear un ASESOR

```bash
TOKEN_BO=$(gql '{"query":"mutation { iniciarSesion(entrada:{usuario:\"admin.solventa\", clave:\"admin-local-solventa\"}) { ... on Sesion { token } } }"}' | campo data.iniciarSesion.token)
echo "${TOKEN_BO:0:20}..."
# Sufijo de letras: un alias con 6 o más dígitos seguidos se rechaza por parecer un documento.
SUFIJO=$(python3 -c "import random,string;print(''.join(random.choices(string.ascii_lowercase,k=8)))")
gql '{"query":"mutation { crearUsuario(entrada:{usuario:\"asesor.'"$SUFIJO"'\", clave:\"clave-demo-0001\", rol:ASESOR, nombre:\"Asesora Demo\"}) { __typename ... on Usuario { id rol estado } ... on ErrorUsuario { mensaje } } }"}' "$TOKEN_BO"
TOKEN_ASESOR=$(gql '{"query":"mutation { iniciarSesion(entrada:{usuario:\"asesor.'"$SUFIJO"'\", clave:\"clave-demo-0001\"}) { ... on Sesion { token usuario { rol } } } }"}' | campo data.iniciarSesion.token)
gql '{"query":"{ yo { usuario rol estado nombre } }"}' "$TOKEN_ASESOR"
```

**Esperado:** un JWT, el usuario creado con `"rol":"ASESOR"` y `"estado":"ACTIVO"`, y `yo` devolviendo ese mismo
usuario **con su propio nombre** (`Asesora Demo`; cada usuario ve solo el suyo y el JWT no lo lleva). El JWT lleva el rol dentro (puedes pegarlo en jwt.io para verlo); el BFF lo verifica en cada petición.

### Paso 4. Un intento prohibido

```bash
gql '{"query":"{ usuarios { id } }"}' "$TOKEN_ASESOR"
```

**Esperado:** `"message":"No tienes permiso para esta operación."`. Un ASESOR no gestiona usuarios.

## 4. Oferta con proveedores sanos y degradados (HU-W01)

El caso insignia: registrar el consentimiento del cliente, pedir la oferta y ver cómo el sistema **se degrada sin dar un
5xx** cuando un proveedor falla.

### Paso 5. Registrar el consentimiento

```bash
CONSENTIMIENTO=$(gql '{"query":"mutation { registrarConsentimiento(entrada:{finalidad:\"cotizacion-vida-hipotecario\", versionTexto:\"v1\"}) { id vigente } }"}' "$TOKEN_ASESOR" | campo data.registrarConsentimiento.id)
echo $CONSENTIMIENTO
curl -s $AUTH/consentimientos/$CONSENTIMIENTO
echo
```

**Esperado:** un id y, en AUTH, el consentimiento `vigente` con el ASESOR como `actor`. **Sin consentimiento vigente no
se consulta ninguna fuente** (regla 7): el consentimiento se registra antes.

### Paso 6. Oferta COMPLETA

```bash
DOC="CC-DEMO-$(python3 -c "import random;print(random.randint(10**7,10**8-1))")"
OFERTA='{"query":"mutation($e: SolicitudOfertaInput!) { solicitarOferta(entrada: $e) { __typename ... on Oferta { tipo prima { valor moneda } rangoPrima { minima { valor } maxima { valor } } fuentes { nombre estado } } ... on ErrorOferta { mensaje puedeReintentar } } }","variables":{"e":{"consentimientoId":"'"$CONSENTIMIENTO"'","documentoIdentidad":"'"$DOC"'","valorCredito":"200000000","plazoMeses":240,"direccionInmueble":"Calle 1 # 2-3"}}}'
gql "$OFERTA" "$TOKEN_ASESOR"
```

**Esperado:** `"tipo":"COMPLETA"`, una `prima` puntual (el dinero viaja como texto decimal, nunca como número),
`rangoPrima` en `null` y las fuentes en `OK`. Tarda menos de un segundo.

### Paso 7. Degradar Open Finance: oferta PRELIMINAR

```bash
modo $OPEN_FINANCE lento
gql "$OFERTA" "$TOKEN_ASESOR"
```

**Esperado:** `"tipo":"PRELIMINAR"`, `prima` en `null`, un `rangoPrima` con mínima y máxima, y alguna fuente en `CACHE`
(se usó el dato guardado de la consulta anterior, porque el proveedor tardó más de 700 ms). **Nunca un 5xx.**

Con un documento **nuevo** (sin dato en caché) no hay de dónde sacar el historial crediticio, que es la única fuente
obligatoria:

```bash
OTRO='{"query":"mutation($e: SolicitudOfertaInput!) { solicitarOferta(entrada: $e) { __typename ... on ErrorOferta { mensaje puedeReintentar } } }","variables":{"e":{"consentimientoId":"'"$CONSENTIMIENTO"'","documentoIdentidad":"CC-DEMO-99887766","valorCredito":"200000000","plazoMeses":240,"direccionInmueble":"Calle 1 # 2-3"}}}'
gql "$OTRO" "$TOKEN_ASESOR"
curl -s $ACL/circuitos
```

**Esperado:** `"__typename":"ErrorOferta"` con `"puedeReintentar":true`, y algún circuito del ACL en `opened`
(el Circuit Breaker dejó de llamar al proveedor caído).

### Paso 8. Restaurar y esperar a que el circuito cierre

```bash
modo $OPEN_FINANCE sano
sleep 12
curl -s $ACL/circuitos
gql "$OFERTA" "$TOKEN_ASESOR"
```

**Esperado:** los circuitos vuelven a `closed` (la sonda de recuperación corre fuera del camino del usuario, por eso
tarda unos segundos) y la oferta vuelve a ser `COMPLETA`. Si la oferta aún sale `PRELIMINAR`, espera otros 10 s y repite.

### Paso 9. Un dato inválido, contactar al asesor y revocar

```bash
gql '{"query":"mutation($e: SolicitudOfertaInput!) { solicitarOferta(entrada: $e) { __typename ... on ErrorOferta { mensaje puedeReintentar } } }","variables":{"e":{"consentimientoId":"'"$CONSENTIMIENTO"'","documentoIdentidad":"'"$DOC"'","valorCredito":"200000000","plazoMeses":0,"direccionInmueble":"Calle 1"}}}' "$TOKEN_ASESOR"
gql '{"query":"mutation { contactarAsesor(ofertaId:\"o-1\", canal: CHAT) { canal resumen } }"}' "$TOKEN_ASESOR"
gql '{"query":"mutation { revocarConsentimiento(id:\"'"$CONSENTIMIENTO"'\") { vigente revocadoEn } }"}' "$TOKEN_ASESOR"
gql "$OFERTA" "$TOKEN_ASESOR"
```

**Esperado:** en orden: `ErrorOferta` por el plazo (sin reintento), la confirmación del contacto
(`contactarAsesor` hoy solo confirma, no hay servicio de asesores), el consentimiento con `"vigente":false` y, al pedir
la oferta otra vez, `ErrorOferta`: **revocar corta el acceso a las fuentes**.

## 5. Cobros y conciliación (HU-W05)

Todo cobro lo ordena un BACKOFFICE. Aquí va el recorrido corto; el detalle completo (rechazos, caída larga, logs) está en
[cobros-conciliacion.md](cobros-conciliacion.md).

### Paso 10. Crear un cliente con nombre y ordenar un cobro con la pasarela lenta

```bash
ALIAS="cliente.$SUFIJO"
ID_CL=$(gql '{"query":"mutation { crearUsuario(entrada:{usuario:\"'"$ALIAS"'\", clave:\"clave-demo-0001\", rol:CLIENTE, nombre:\"Ana Pérez Demo\"}) { ... on Usuario { id } } }"}' "$TOKEN_BO" | campo data.crearUsuario.id)
CLAVE="manual-$(date +%s)"
CREAR='mutation($e: CrearCobroInput!) { crearCobro(entrada: $e) { __typename ... on Cobro { id estado cliente { nombre } } ... on ErrorCobro { mensaje } } }'
entrada() { echo '{"query":"'"$CREAR"'","variables":{"e":{"idIntentoCobro":"'"$1"'","polizaId":"POL-MANUAL-1","monto":{"valor":"'"$2"'","moneda":"COP"},"tokenMedioPago":"tok_demo","clienteId":"'"$ID_CL"'"}}}'; }
modo $PASARELA lento
ID=$(gql "$(entrada "$CLAVE" 90000.00)" "$TOKEN_BO" | campo data.crearCobro.id)
modo $PASARELA sano
curl -s $PAY/cobros/$ID | campo estado
curl -s $PASARELA/v1/cobros/$CLAVE | campo referencia
```

**Esperado:** PAYMENTS dice `pendiente` y la pasarela, aun así, tiene una referencia `pg-…`: **la pasarela sí cobró**, pero
tardó más de 700 ms y Solventa lo registró como pendiente. Es el caso que antes terminaba en doble cobro.

### Paso 11. Ver la tabla y reintentar (concilia, no cobra de nuevo)

```bash
gql '{"query":"{ cobros(polizaId:\"POL-MANUAL-1\") { id estado motivo cliente { nombre } } }"}' "$TOKEN_BO"
gql '{"query":"mutation { reintentarCobro(cobroId:\"'"$ID"'\") { estado idTransaccion } }"}' "$TOKEN_BO"
echo "PAYMENTS: $(curl -s $PAY/cobros/$ID | campo referencia)"
echo "Pasarela: $(curl -s $PASARELA/v1/cobros/$CLAVE | campo referencia)"
gql "$(entrada "$CLAVE" 1.00)" "$TOKEN_BO"
```

**Esperado:** la tabla muestra el cobro con el nombre del cliente; el reintento lo deja `COBRADO` y su `idTransaccion` es la referencia de la pasarela; **las dos referencias son
idénticas** (no hubo un segundo cobro); y reenviar el mismo `idIntentoCobro` con otro monto da `ErrorCobro`
("ya se usó con otros datos"). Un CLIENTE o un ASESOR que lo intente recibe "No tienes permiso".

## 6. Probar los servicios por separado

Útil para aislar un problema. Todo servicio tiene Swagger en `/docs`, donde puedes ejecutar sus endpoints desde el
navegador.

```bash
curl -s $AUTH/usuarios | head -c 300; echo       # AUTH: lista de usuarios (sin hash ni nombre)
curl -s $ACL/circuitos                            # ACL Worker: estado de cada Circuit Breaker
curl -s localhost:8004/metrics | grep -m3 "^http_requests_total"   # métricas de PAYMENTS
```

Los servicios internos **no están pensados para el público**: en la nube son privados y solo el BFF los llama. En local los
tienes abiertos para depurar.

## 7. Mirar qué pasó

```bash
docker compose logs bff payments acl-worker --no-color --tail 40     # lo que hizo cada servicio
docker compose logs auth --no-color | grep -c "clave-demo-0001"      # esperado: 0
```

Los logs **no llevan datos personales** (regla 6): ni claves, ni tokens, ni nombres, ni pólizas en crudo; solo ids opacos.
Si encuentras uno, es un defecto: repórtalo.

En Grafana (http://localhost:3100) y Prometheus (http://localhost:9190) ves las métricas de cada servicio mientras corres
los pasos anteriores.

## 8. Pruebas automáticas (opcional)

```bash
python3 scripts/humo_bff.py                                  # humo de punta a punta (necesita el stack arriba)
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json    # toda la colección contra el BFF (Node 18+)
```

**Esperado:** `OK: humo del BFF en verde` y Newman con todas las aserciones en cero fallidas. Las pruebas unitarias de
cada servicio (`python -m pytest` dentro de `services/<servicio>`) se explican en el [README](../README.md).

## 9. Apagar

```bash
docker compose --profile servicios stop        # conserva los datos
docker compose --profile servicios down -v     # BORRA los datos (usuarios, cobros...): úsalo para empezar de cero
```

## 10. Si algo falla

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `bind: address already in use` al levantar | 5432 o 6379 ocupados | Antepón `POSTGRES_PORT=5433 REDIS_PORT=6380` a los comandos de compose |
| El primer `up` falla con Postgres `unhealthy` | Problema conocido del primer arranque con volumen nuevo | Repite el mismo `up` |
| `ErrorUsuario`: "Ya existe un usuario con ese alias" | Repetiste un alias de una corrida anterior | Usa otro sufijo, o `down -v` |
| `Debes iniciar sesión.` | Falta el token o variable vacía (terminal nueva) | Repite el paso 0 y el paso 3 |
| `Tu sesión no es válida o venció` | El JWT dura 60 minutos | Inicia sesión de nuevo |
| La oferta sale `PRELIMINAR` con todo sano | Los circuitos del ACL aún no cierran | Espera 10 s más y revisa `curl -s $ACL/circuitos` |
| `ErrorOferta` sin motivo aparente | Consentimiento revocado o inexistente | Registra uno nuevo (paso 5) |
| Un servicio `unhealthy` | Falló su migración o su dependencia | `docker compose logs <servicio> --tail 50` |
