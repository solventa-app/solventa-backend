# Autenticación de usuarios en el BFF (HU-M02, KAN-24)

Cómo se identifica a quien usa el BFF, qué puede hacer cada rol y cómo probarlo paso a paso. Es la versión **simple**
de HU-M02 para el canal web: usuario y clave → JWT → el BFF valida el token y aplica permisos por rol.

Documentos relacionados: [implementación del BFF](bff-implementacion.md) · [contrato GraphQL](../contracts/graphql/schema.graphql) ·
[README de AUTH](../services/auth/README.md) · [README del BFF](../services/bff/README.md) · [pruebas Newman](../tests/newman/README.md).

## 1. Decisiones

| Decisión | Resultado | Por qué |
|---|---|---|
| Roles | `CLIENTE`, `ASESOR`, `BACKOFFICE` | Son los del plan: cliente, asesor comercial y back-office |
| Credenciales | Alias + clave; la clave se guarda como hash **argon2id** | Simple y seguro: un robo de la tabla no revela las claves |
| Alias | Texto plano; **no puede ser un correo ni un documento** | Así la tabla no guarda datos personales y no depende del cifrado de campo (SEG-002), que aún no existe |
| Token | **JWT HS256**, 60 minutos, firmado por AUTH y verificado por el BFF con el mismo secreto (`clave-jwt`) | Lo más simple. El paso a ES256 queda listo para cuando llegue el API Gateway (sección 8) |
| Transición | Bandera `AUTENTICACION_USUARIOS=opcional` | Las operaciones existentes siguen funcionando sin token, para no romper al frontend |
| API Gateway | **No por ahora** | El BFF ya es la única puerta pública; el gateway llega en el Sprint 2 (LAT-004 / ESC-001) |
| Base de datos | **El mismo Postgres**, base `auth` | Solo se agrega la tabla `usuarios` (migración `0002`); ningún contenedor nuevo |
| Móvil | **Fuera de alcance** | Biometría y refresh tokens quedan documentados en la sección 7 |

## 2. Flujo

```
Navegador ─► BFF ─► AUTH        iniciarSesion(usuario, clave)
                                 AUTH verifica el hash (argon2id) y firma el JWT (HS256)
          ◄──────────────────── { token, expiraEn, usuario }

Navegador ─► BFF                 cada operación con  Authorization: Bearer <token>
              │                  el BFF verifica la firma y el vencimiento (app/autenticacion_usuarios.py)
              │                  y cada resolutor aplica su regla con exigir_rol (app/esquema.py)
              └─► AUTH / ACL / RISK / RATING   solo si el rol tiene permiso
```

AUTH es interno: solo lo llama el BFF, que ya validó el token y el rol. Por eso AUTH no vuelve a autorizar.

## 3. Modelo de datos (`services/auth`, migración `0002_usuarios`)

Tabla `usuarios` en la base `auth`: `id`, `usuario` (alias único), `hash_clave` (argon2id), `rol`, `estado`
(`ACTIVO` / `SUSPENDIDO`), `intentos_fallidos`, `bloqueado_hasta`, `creado_en` y `actualizado_en`. La baja es lógica
(`estado`): nunca se borran filas. El primer BACKOFFICE lo crea la tarea `crear-backoffice` (sección 6, paso 1).

## 4. Matriz de permisos

| Operación | CLIENTE | ASESOR | BACKOFFICE | Sin token (modo `opcional`) |
|---|---|---|---|---|
| `registrarConsentimiento`, `revocarConsentimiento` | ✅ | ✅ (en nombre del cliente) | ❌ | ✅ como antes |
| `solicitarOferta`, `oferta`, `contactarAsesor` | ✅ | ✅ | ❌ | ✅ como antes |
| `cobros`, `reintentarCobro` (siguen "no disponible: KAN-28") | ❌ | ❌ | ✅ | ✅ como antes |
| `usuarios`, `crearUsuario`, `cambiarRolUsuario`, `suspenderUsuario` | ❌ | ❌ | ✅ | ❌ siempre exige token |
| `yo` | ✅ | ✅ | ✅ | ❌ siempre exige token |
| `iniciarSesion` | pública | pública | pública | pública |

Un token **inválido o vencido** se rechaza en cualquier modo, aunque la operación no lo exija. En modo `obligatoria`,
la última columna desaparece: todo exige token.

Cuando un ASESOR registra un consentimiento, AUTH guarda el **actor** (`{rol, usuario_id}`): queda constancia de que lo
otorgó en nombre del cliente (CA-W01-02 y CA-W01-11).

## 5. Reglas de seguridad

- **Mensaje genérico** "Usuario o clave incorrectos." para alias inexistente, clave errónea, usuario bloqueado o
  suspendido: no revela qué alias existen. Si el alias no existe se verifica igual contra un hash ficticio, para que
  el tiempo de respuesta tampoco lo revele.
- **Bloqueo**: 5 fallos seguidos bloquean el usuario 15 minutos; un inicio de sesión correcto reinicia el contador.
- **Nada sensible en logs**: ni el alias, ni la clave, ni el token. Solo el id opaco del usuario (`id_opaco`).
- **Secretos**: en local, valores fijos y públicos (`solo-local-no-secreta-jwt-solventa-0001`, `admin-local-solventa`);
  en Cloud Run, Secret Manager. AUTH y el BFF **se niegan a arrancar en la nube** con el valor local, el marcador de
  Terraform o un secreto de menos de 32 bytes.
- **Errores**: nunca un 5xx. Los errores de permiso viajan como error de GraphQL con HTTP 200 y mensaje claro.

### Limitaciones conocidas

| Limitación | Efecto | Cómo se cierra |
|---|---|---|
| **El token no se revoca** | Suspender a un usuario impide **nuevos** inicios de sesión, pero el token que ya tenía sigue valiendo hasta que vence (máx. 60 min) | Tokens más cortos con refresh (sección 7), o que el BFF consulte el estado del usuario en cada petición |
| **No hay cierre de sesión en el servidor** | El frontend cierra sesión borrando el token; si alguien lo copió, vale hasta que vence | Igual que la anterior |
| **Bloqueo por usuario** | Alguien que conozca un alias puede bloquearlo 15 min a propósito | Límite por IP en el API Gateway (Sprint 2) |
| **HS256 con secreto compartido** | Quien tenga el secreto puede verificar **y** emitir tokens; hoy solo AUTH y el BFF lo leen | Paso a ES256 (sección 8) |
| **Consentimiento en memoria** | Es el mock de T-W01-4; con más de una instancia de AUTH en Cloud Run se pierde | T-W01-4 (Postgres) |

## 6. Cómo probarlo paso a paso

Todo se corre desde la raíz del repo, en **una sola terminal** (los pasos reutilizan variables). Los alias `*.demo`
son de ejemplo: si ya los creaste en una corrida anterior verás `ErrorUsuario` ("Ya existe..."); cambia el alias o
baja el stack con `down -v`.

### Paso 0. Atajos

```bash
BFF=http://localhost:8000/graphql/web

# gql '<cuerpo JSON>' [token]: envía una operación GraphQL, con token si se lo pasas.
gql() {
  if [ -n "$2" ]; then
    curl -s -X POST "$BFF" -H 'Content-Type: application/json' -H "Authorization: Bearer $2" -d "$1"
  else
    curl -s -X POST "$BFF" -H 'Content-Type: application/json' -d "$1"
  fi
  echo
}

# campo <ruta>: extrae un campo de la respuesta JSON (sin jq), por ejemplo  campo data.yo.rol
campo() { python3 -c "import sys,json; d=json.load(sys.stdin); [d:=d[k] for k in sys.argv[1].split('.')]; print(d)" "$1"; }
```

### Paso 1. Levantar el stack

```bash
# Si 5432 o 6379 están ocupados en tu máquina, usa los puertos alternos:
POSTGRES_PORT=5433 REDIS_PORT=6380 docker compose --profile servicios up -d --build --wait
docker compose ps -a --format '{{.Service}}: {{.Status}}' | grep -E "crear-backoffice|migrar-auth|auth|bff"
docker compose logs crear-backoffice --no-color | tail -1
```

**Esperado:** `migrar-auth` y `crear-backoffice` en `Exited (0)`, `auth` y `bff` en `healthy`, y en el log
`usuario creado rol=BACKOFFICE` (o `usuario ya existía` desde la segunda vez).
**Demuestra:** la migración creó la tabla `usuarios` en el mismo Postgres y existe el primer BACKOFFICE.

### Paso 2. Iniciar sesión como BACKOFFICE

```bash
TOKEN_BO=$(gql '{"query":"mutation { iniciarSesion(entrada:{usuario:\"admin.solventa\", clave:\"admin-local-solventa\"}) { ... on Sesion { token } } }"}' | campo data.iniciarSesion.token)
echo "${TOKEN_BO:0:25}..."
```

**Esperado:** el inicio de un JWT (`eyJhbGciOiJIUzI1NiIs...`).
**Demuestra:** AUTH verificó la clave contra el hash argon2id y firmó el token.

### Paso 3. Preguntar quién soy

```bash
gql '{"query":"{ yo { usuario rol estado } }"}' "$TOKEN_BO"
```

**Esperado:** `{"data":{"yo":{"usuario":"admin.solventa","rol":"BACKOFFICE","estado":"ACTIVO"}}}`
**Demuestra:** el BFF validó el token y resolvió al usuario. Es lo que usa el frontend para saber qué mostrar.

### Paso 4. Crear un ASESOR y un CLIENTE

```bash
gql '{"query":"mutation { crearUsuario(entrada:{usuario:\"asesor.demo\", clave:\"clave-demo-0001\", rol:ASESOR}) { __typename ... on Usuario { id rol } ... on ErrorUsuario { mensaje } } }"}' "$TOKEN_BO"
ID_CLIENTE=$(gql '{"query":"mutation { crearUsuario(entrada:{usuario:\"cliente.demo\", clave:\"clave-demo-0001\", rol:CLIENTE}) { ... on Usuario { id } } }"}' "$TOKEN_BO" | campo data.crearUsuario.id)
echo "$ID_CLIENTE"

# Un alias con forma de correo se rechaza:
gql '{"query":"mutation { crearUsuario(entrada:{usuario:\"persona@correo.com\", clave:\"clave-demo-0001\", rol:CLIENTE}) { __typename ... on ErrorUsuario { mensaje } } }"}' "$TOKEN_BO"
```

**Esperado:** `"__typename":"Usuario"` con `"rol":"ASESOR"`; un id (UUID) para el cliente; y
`ErrorUsuario` con "El usuario no puede ser un correo ni un número de documento."
**Demuestra:** solo el BACKOFFICE da de alta usuarios, y el alias no admite datos personales.

### Paso 5. Iniciar sesión como ASESOR

```bash
TOKEN_ASESOR=$(gql '{"query":"mutation { iniciarSesion(entrada:{usuario:\"asesor.demo\", clave:\"clave-demo-0001\"}) { ... on Sesion { token } } }"}' | campo data.iniciarSesion.token)
gql '{"query":"{ yo { usuario rol } }"}' "$TOKEN_ASESOR"
```

**Esperado:** `{"data":{"yo":{"usuario":"asesor.demo","rol":"ASESOR"}}}`

### Paso 6. Pedir una oferta como ASESOR (CA-W01-11)

```bash
CONSENTIMIENTO=$(gql '{"query":"mutation { registrarConsentimiento(entrada:{finalidad:\"cotizacion-vida-hipotecario\", versionTexto:\"v1\"}) { id } }"}' "$TOKEN_ASESOR" | campo data.registrarConsentimiento.id)

# AUTH guardó quién otorgó el consentimiento:
curl -s localhost:8001/consentimientos/$CONSENTIMIENTO | campo actor

gql '{"query":"mutation($e: SolicitudOfertaInput!) { solicitarOferta(entrada:$e) { __typename ... on Oferta { tipo prima { valor moneda } } } }","variables":{"e":{"consentimientoId":"'"$CONSENTIMIENTO"'","documentoIdentidad":"CC-DEMO-0001","valorCredito":"200000000","plazoMeses":240,"direccionInmueble":"x"}}}' "$TOKEN_ASESOR"
```

**Esperado:** el actor `{'rol': 'ASESOR', 'usuario_id': '...'}` y una `Oferta` `COMPLETA` con prima en COP.
**Demuestra:** el asesor autenticado usa el mismo flujo que el cliente y queda registrado como actor (CA-W01-11).

### Paso 7. Un intento prohibido

```bash
TOKEN_CLIENTE=$(gql '{"query":"mutation { iniciarSesion(entrada:{usuario:\"cliente.demo\", clave:\"clave-demo-0001\"}) { ... on Sesion { token } } }"}' | campo data.iniciarSesion.token)
gql '{"query":"{ usuarios { usuario } }"}' "$TOKEN_CLIENTE"
gql '{"query":"{ usuarios { usuario } }"}'
```

**Esperado:** primero `"message":"No tienes permiso para esta operación."`; sin token, `"message":"Debes iniciar sesión."`.
En ambos casos la respuesta HTTP es 200, nunca un 5xx.
**Demuestra:** la matriz de permisos de la sección 4.

### Paso 8. Clave errónea y bloqueo

Se usa un usuario aparte para no bloquear a los de los pasos anteriores.

```bash
gql '{"query":"mutation { crearUsuario(entrada:{usuario:\"bloqueo.demo\", clave:\"clave-demo-0001\", rol:CLIENTE}) { __typename } }"}' "$TOKEN_BO"
for i in 1 2 3 4 5; do
  gql '{"query":"mutation { iniciarSesion(entrada:{usuario:\"bloqueo.demo\", clave:\"no-es-la-clave\"}) { ... on ErrorAutenticacion { mensaje } } }"}'
done
# Ahora incluso con la clave correcta:
gql '{"query":"mutation { iniciarSesion(entrada:{usuario:\"bloqueo.demo\", clave:\"clave-demo-0001\"}) { __typename ... on ErrorAutenticacion { mensaje } } }"}'
```

**Esperado:** cinco veces "Usuario o clave incorrectos."; y la sexta, con la clave correcta, **también**
`ErrorAutenticacion`: el usuario queda bloqueado 15 minutos.
**Demuestra:** el bloqueo por intentos, con el mismo mensaje genérico (no revela que la cuenta está bloqueada).

### Paso 9. Suspender un usuario

```bash
gql '{"query":"mutation($id: ID!) { suspenderUsuario(id:$id) { ... on Usuario { usuario estado } } }","variables":{"id":"'"$ID_CLIENTE"'"}}' "$TOKEN_BO"
gql '{"query":"mutation { iniciarSesion(entrada:{usuario:\"cliente.demo\", clave:\"clave-demo-0001\"}) { __typename } }"}'
```

**Esperado:** `"estado":"SUSPENDIDO"`, y después `"__typename":"ErrorAutenticacion"`.
**Demuestra:** la baja lógica: el usuario se conserva, pero ya no inicia sesión. Ojo: `$TOKEN_CLIENTE`, emitido antes
de suspenderlo, **sigue valiendo hasta que vence** (sección 5, limitaciones).

### Paso 10. Token alterado

```bash
ALTERADO=$(python3 -c "import sys; c,p,f=sys.argv[1].split('.'); print(c+'.'+p+'.'+('B' if f[0]=='A' else 'A')+f[1:])" "$TOKEN_ASESOR")
gql '{"query":"{ yo { usuario } }"}' "$ALTERADO"
```

**Esperado:** `"message":"Tu sesión no es válida o venció. Inicia sesión de nuevo."`
**Demuestra:** un token con la firma cambiada no pasa. Se altera el **primer** carácter de la firma: el último no
sirve, porque en base64url solo lleva bits de relleno. El token **vencido** lo cubren las pruebas unitarias del BFF
(`tests/test_autenticacion_usuarios.py`), que fabrican un token con `exp` en el pasado.

### Paso 11. Qué mirar en los logs

```bash
docker compose logs auth bff --no-color | grep -E "auth.sesiones|auth.usuarios" | tail -5
docker compose logs auth bff --no-color | grep -c -E "clave-demo-0001|admin-local-solventa|eyJ|asesor.demo" || true
```

**Esperado:** líneas como `sesión iniciada usuario=3f9c2a1b7d4e rol=ASESOR` (id opaco, sin alias), y un conteo de `0`.
**Demuestra:** ni claves, ni tokens, ni alias en los logs.

### Paso 12. Pruebas automáticas

```bash
python3 scripts/humo_bff.py
npx --yes newman@6.2.2 run tests/newman/solventa-bff.postman_collection.json \
  -e tests/newman/solventa-local.postman_environment.json
POSTGRES_PORT=5433 REDIS_PORT=6380 docker compose --profile servicios down -v   # al terminar
```

**Esperado:** `OK: humo del BFF en verde` y Newman con 95 aserciones, 0 fallidas.

### Con GraphiQL (navegador)

Abre http://localhost:8000/graphql/web. Ejecuta `iniciarSesion`, copia el `token` y, en la pestaña **Headers**, pon
`{"Authorization": "Bearer <token>"}`. Desde ahí cualquier operación va autenticada.

## 7. Pendiente para móvil

Fuera de alcance por decisión del equipo; queda el diseño para retomarlo:

- **La biometría no llega al servidor.** El móvil inicia sesión con usuario y clave una vez y recibe, además del JWT
  corto, un **refresh token** que guarda en el almacén seguro del teléfono (Keychain / Keystore) protegido por huella
  o rostro. Después, la biometría solo desbloquea ese refresh token local.
- **Backend necesario:** tabla `sesiones` (`id`, `usuario_id`, `hash_refresh`, `expira_en`, `revocada_en`,
  `dispositivo`), mutación `renovarSesion(refreshToken)` con **rotación** (cada uso emite uno nuevo e invalida el
  anterior) y `cerrarSesion` que revoca. Eso también cierra la limitación "el token no se revoca" de la sección 5.
- La clave podría dejar de ser obligatoria (columna `hash_clave` nula) si el alta se hace por otro medio (invitación u OTP).

## 8. Cuando llegue el API Gateway

1. Pasar de HS256 a **ES256**: AUTH firma con clave privada y publica `GET /.well-known/jwks.json`; el BFF (o el
   gateway) verifica con la clave pública. Así solo AUTH puede emitir tokens.
2. Mover la verificación del token al gateway (`jwks_uri`). En el BFF, `app/autenticacion_usuarios.py` es el único
   módulo que cambia; los resolutores siguen llamando a `exigir_rol`.
3. Aprovechar el gateway para el límite de intentos por IP y las cuotas.

## 9. Otros pasos siguientes

- **Pasar la bandera a `obligatoria`** cuando el frontend ya envíe el token: cambiar `AUTENTICACION_USUARIOS` en
  `.github/servicios.json` (`entorno` del BFF) y en `docker-compose.yml`, avisar en `contracts/CHANGELOG.md` y abrir el
  PR gemelo en `solventa-frontend`.
- **Eventos de usuario** (`usuario.creado`, `usuario.suspendido`, `usuario.rol-cambiado`): no se publican porque nadie
  los consume todavía. Se definen en `contracts/events/` cuando exista un consumidor.
- **Staging**: cargar `clave-jwt` y crear el primer BACKOFFICE (ver `infra/README.md`).
