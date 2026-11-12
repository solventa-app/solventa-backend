# HU-M06 · Lo que falta en mobile y backend (y «Mis asistencias»)

> Copia en el backend del plan de HU-M06. La copia de referencia, con los bocetos, está en `solventa-frontend-mobile/docs/hu-m06/plan-pendientes.md`.

## Estado y ajustes al ejecutar el plan (se actualiza en cada PR)

Revisión del 2026-11-12 contra `develop` (mobile `8dac02f`, backend `fb3b4c6`). El plan de abajo es el original del
2026-11-10; donde choca con esta sección, **gana esta sección**.

### Decisiones de Felipe al revisar (2026-11-12)

1. La dirección escrita se **geocodifica** (2.0) y reemplaza la dirección a mano sin coordenadas del PR #61
   (`solicitarAsistenciaConDireccion`, v0.41): se depreca en el backend y la app deja de usarla (S1, B4).
2. La barra inferior es **Inicio · Asistencia · Mis asistencias · Cerrar sesión** (4.1). El boceto de ubicación, que
   muestra «Perfil», está mal en eso.
3. Las notificaciones (4.5) se configuran y se prueban como trabajo propio: no se dan por hechas.
4. Jira no se toca en este trabajo.

### Ajustes al plan

| # | Ajuste | Motivo |
|---|---|---|
| A1 | Una versión de contrato por fase: **v0.49** catálogo y grúa, **v0.50** dirección ubicada, **v0.51** rutas, **v0.52** «Mis asistencias» | El contrato iba en v0.45 y el PR #589 del backend (KAN-259, push de HU-M04) ya reserva v0.46 a v0.48 |
| A2 | La notificación local de llegada **convive con FCM** (`firebase_messaging` ya está en el `pubspec.yaml`, KAN-259) en un canal propio «Asistencias» | El plan la elegía porque FCM «faltaba»; ya no es así |
| A3 | Antes de tocar el CI de mobile se confirma si Gradle necesita token de descargas de Mapbox para **compilar** (Emuladores, MobSF y CD compilan un APK aunque usen el mapa falso) | El plan solo lo consideraba para ejecutar |
| A4 | La app envía `Accept-Language` en el cliente GraphQL | 2.0 lo necesita y hoy no se envía |
| A5 | **Resuelto:** CLAIMS llama directo al ACL Worker, como ya lo hacen PAYMENTS, POLICY y UNDER (`llama` en `.github/servicios.json`) | Se verificó en el código |
| A6 | La ubicación sale del teléfono al abrir el mapa (rutas por el backend) y `auditoria_rutas` la guarda 90 días cifrada: actualizar el aviso de privacidad y `docs/hu-m06-backend.md` §1 de mobile | Contradice la decisión anterior de que no saliera del teléfono |
| A7 | Claves de texto `misAsistenciasEnCamino` y `misAsistenciasYaLlegaron` en vez de `homeEnCamino` y `homeYaLlegaron` | Ya no está en Inicio |
| A8 | Volver a pasar MobSF y revisar R8 por Mapbox y los permisos nuevos | Cambian el APK y sus permisos |
| A9 | En la máquina de desarrollo solo se corre lo que cabe en contenedores (análisis, unitarias, widget, goldens, pytest, ruff, migraciones contra Mongo y Newman con el compose). **Emuladores, Mapbox real, teléfono real y staging quedan pendientes** para una prueba posterior | Regla del equipo |
| A10 | `stub-prestadores` usa el puerto **8109** | El 8107 que proponía el plan ya es de `stub-vehiculos` y el 8108 lo toma `stub-eventos-parametricos` en el PR #589 |
| A11 | `fotoUrl` queda **null** en toda la semilla: ALMACENAMIENTO no sirve archivos públicos, así que no hay de dónde salir una foto de sede. La app usa el placeholder del tipo y está lista para la foto (se prueba con el fake) | B3 pedía fotos en algunos |
| A19 | La dirección se guarda en la asistencia tal como la devolvió Google en el idioma de la app; la versión en español para soporte y auditoría (2.0) se agrega con `auditoria_rutas` (fase 4) | Evita una segunda llamada a Google en cada solicitud |
| A20 | `origenUbicacion` es `OrigenUbicacion! = GPS` (no nulo con valor por defecto): así una app anterior a la v0.50 sigue funcionando sin mandarlo | Strawberry lo expone así y el contrato debe ser idéntico |
| A24 | Las rutas simuladas (local y CI) van **dentro del ACL** (`MAPAS_PROVEEDOR=simulado`, línea recta con las reglas de la app), no en un `stub-mapas` aparte | Mismo resultado y un contenedor menos |
| A25 | En `auditoria_rutas` la **dirección aproximada también va cifrada**, no solo la ubicación | También es un dato personal (regla 6) |
| A26 | Aún no hay una consulta de `auditoria_rutas` para roles de auditoría: la colección existe con su TTL de 90 días y se lee en Mongo. Una consulta sin prestadores conocidos no se audita (no llama a Mapbox) | No hay rol de auditoría en la app hoy |
| A27 | **Hallazgo:** los endpoints del ACL que llevan dirección o coordenadas en la query string las dejan en el log de acceso de uvicorn. `/lugares/ubicar` (v0.50) y `/lugares/inversa` (v0.51) pasaron a `POST` con cuerpo. **Preexistente, fuera de este trabajo:** `GET /lugares/autocompletar?texto=` y `GET /lugares/validar?place_id=` (T19) tienen el mismo problema | Regla 6 |
| A12 | Los minutos de llegada simulados se calculan en `stubs/prestadores` (Haversine, 20 km/h + 2 min) y `PRESTADORES_MINUTOS_FIJOS` vive allí | CLAIMS ya no simula (B7) |

### Avance por fase

| Fase | Backend | Mobile |
|---|---|---|
| 1 · Catálogo con detalle, grúa y prestadores detrás del ACL (B1, B2, B3, B7) | PR #590 (v0.49, CLAIMS `0007`) | PR #83 (mobile) |
| 2 · Mapbox, pines, detalle, hoja arrastrable (M0–M14) | — | PR #84 (mobile) |
| 3 · Dirección ubicada (B4, B9, 2.0, S1) | PR #591 (v0.50, apilado sobre #590) | PR #85 (mobile) |
| 4 · Rutas y auditoría (B8, M15) | `feat/KAN-36-rutas-prestadores` (v0.51, CLAIMS `0008`, apilada sobre #591) | pendiente |
| 5 · «Mis asistencias» y notificación (B5, B6, 4.x) | pendiente | pendiente |

### Pendiente de verificar fuera de esta máquina (A9)

- Emuladores (`emuladores.yml`) con las pruebas de integración nuevas de M06.
- Mapbox real (token público en la app y secreto en el ACL), estilos calles y satélite.
- Teléfono real: permisos, GPS, «Cómo llegar», notificación con la app cerrada, «Te alejaste».
- Staging (Anexo B.6) y `auditoria_rutas` en Mongo de staging.

---

Para el compañero que termina HU-M06 (KAN-36). Revisado contra `develop` el 2026-11-10: `solventa-frontend-mobile` en `756154f` y `solventa-backend` en `039b6b0`. **Solo lista lo que falta**; lo que ya está hecho aparece en la sección 1 para no rehacerlo.

Se complementa con el prototipo de Figma ([M06-01](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-2), [M06-02](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-140)) y con los bocetos `boceto-M06-01-tap-pin.png`, `boceto-mis-asistencias.png` y `boceto-ubicacion.png`. Si algo choca con el Figma, se pregunta antes de implementar. Lo marcado **Propuesta** no está en el Figma y se puede ajustar.

> **Decisión del mapa (Felipe, 2026-11-10): Mapbox con su librería oficial, `mapbox_maps_flutter`.** Se retiran `flutter_map`, `latlong2` y OpenStreetMap (esto reemplaza la decisión D-M06; actualizar `CLAUDE.md` y `docs/integracion-backend.md` de mobile). Dos estilos (calles y satélite) con un botón flotante para cambiarlos, y las rutas a los 3 más cercanos pintadas tipo semáforo. La navegación sigue en la app externa. Felipe ya tiene cuenta de Mapbox.

## 0. Decisiones tomadas con Felipe (2026-11-10)

| Tema | Decisión |
|---|---|
| Mapa | **Mapbox** con `mapbox_maps_flutter`; se retira `flutter_map`/OpenStreetMap (M0) |
| Estilos | Calles y satélite con botón flotante de capas (M14) |
| Rutas | Las 3 más cercanas pintadas tipo semáforo; sin navegación dentro de la app (M15) |
| Navegación | «Cómo llegar» abre Google Maps directo, sin selector; luego otra app de mapas, luego navegador predeterminado (M13) |
| Radio de cobertura | No se dibuja; solo filtra (M16) |
| Ubicación | GPS por defecto. **Sin permiso o con el GPS apagado, el usuario puede escribir su dirección** (autocompletado de Google Places, ya existe por W03) y el backend la convierte en coordenadas; con eso el mapa funciona igual (2.0). Actualiza la decisión anterior de «sin dirección a mano» |
| Pin y fila | El pin abre el detalle; la fila solo enfoca el pin; «Solicitar» va directo a M06-02 (M4, M5) |
| Rutas y tiempos | Por el backend (ACL + Mapbox), no directo desde el teléfono (B8) |
| Auditoría | `auditoria_rutas` con ubicación exacta cifrada y dirección aproximada por Geocoding de Google (B8) |
| Prestadores | Simulación detrás del ACL con `stubs/prestadores` (B7), obligatorio |
| Mis asistencias | Pestaña nueva de la barra inferior con badge; acordeones «En camino» y «Ya llegaron» (4.1) |
| No confirmadas | Se muestran en «En camino» con «Reintentar» (4.1) |
| Llegada | Notificación **local** programada; con la app abierta, aviso con «Ver» (4.5) |
| Si se aleja | Aviso a más de 500 m con «Cancelar y pedir otra»; la hora no se recalcula (4.4b) |

Pendiente solo con diseño (no bloquea): color por aliado en el Figma, color del pin del usuario, ícono de grúa, colores exactos del semáforo y opciones de «Tipo de asistencia» por tipo.

## 1. Lo que ya existe (no rehacer)

| Dónde | Qué hay |
|---|---|
| Mobile `lib/features/assistance/` | M06-01 a M06-06: mapa (`widgets/mapa_prestadores.dart`, `flutter_map`, sin rotación), lista de 3 en hoja fija (`SolSheetLayout` + `SolProviderRow`), permisos y GPS apagado con reintento al volver de Ajustes, solicitud (M06-02), enviando y reenvío (M06-03/03b), confirmada y no confirmada (M06-04a/b), cancelar (M06-05/06) |
| Mobile `domain/cercania.dart` | Los 3 más cercanos en el teléfono: Haversine, 20 km/h + 2 min de alistamiento, radio máximo 50 km |
| Mobile `data/` | Catálogo de prestadores con copia local versionada (se revisa cada 7 días), `RutaExternaService` (`geo:` y luego Google Maps en el navegador), mutaciones de solicitar y cancelar con `idSolicitud` como llave de idempotencia |
| Mobile `pubspec.yaml` | `geolocator`, `url_launcher`, `permission_handler` (y `flutter_map` + `latlong2`, que se reemplazan por Mapbox: M0) |
| Backend CLAIMS | Colección `prestadores` (migración `0005`, semilla de 12 prestadores ficticios) y `catalogoPrestadores(versionLocal)` (contrato v0.36). Colección `asistencias` (migración `0006`), `solicitarAsistencia` y `cancelarAsistencia` (v0.40), desenlace simulado (`ASISTENCIA_SIMULACION=primero_falla`) y `minutosEstimados` calculado en CLAIMS |
| Backend BFF | Las tres operaciones expuestas solo a CLIENTE |

## 2. Lo que falta en mobile

### 2.0 Ubicación: dónde se valida y qué pasa si no hay permiso (Felipe, 2026-11-10)

**Dónde:** al entrar a la pestaña **Asistencia** (M06-01), no en Inicio ni al abrir la app. Hoy esa revisión ya existe (`PrestadoresSinUbicacion` con reintento al volver de Ajustes); se amplía así:

1. ¿El GPS del teléfono está activo? (`Geolocator.isLocationServiceEnabled`)
2. ¿Hay permiso? Si nunca se pidió, primero una pantalla corta: «Necesitamos tu ubicación para mostrarte los prestadores más cercanos» y «Permitir ubicación»; después el diálogo del sistema.
3. Con permiso y GPS: se toma la posición y se abre el mapa.

**Si rechaza el permiso, lo rechazó para siempre o el GPS está apagado**, la pantalla de aviso muestra dos salidas:

| Caso | Botón principal | Botón secundario |
|---|---|---|
| Rechazó una vez | «Permitir ubicación» (vuelve a pedirlo) | **«Escribir mi dirección»** |
| Rechazó para siempre | «Abrir Ajustes» | **«Escribir mi dirección»** |
| GPS apagado | «Activar ubicación» (abre los ajustes de ubicación) | **«Escribir mi dirección»** |

**«Escribir mi dirección»:**
- Hoja modal con un campo «Dirección, ciudad o punto de referencia» y sugerencias mientras escribe. Reusa la consulta **`autocompletarDireccion`** que ya existe (contrato v0.32, Google Places en el ACL, W03), restringida a Colombia.
- Al elegir una sugerencia, la app llama a una consulta nueva **`ubicarDireccion(placeId)`** (B9), que devuelve latitud, longitud y la dirección normalizada.
- Si Places no responde, se permite enviar el texto escrito y el backend lo geocodifica con la Geocoding API. Si tampoco lo encuentra: «No pudimos ubicar esa dirección. Prueba con otra o con un punto de referencia».
- Con las coordenadas, **el mapa funciona igual que con GPS**: prestadores, rutas, detalle y solicitud. El pin del usuario se dibuja en ese punto con una etiqueta «Dirección escrita».
- Arriba del mapa, un aviso: «Usando la dirección que escribiste: Cra 45 # 22-18 · Cambiar». Si después da el permiso (al volver de Ajustes), aparece «Usar mi ubicación actual».
- M06-02 muestra esa dirección en lugar de «ubicación detectada automáticamente».
- El aviso «Te alejaste» (4.4b) no aplica a las asistencias pedidas con dirección escrita, porque no hay GPS con qué comparar.
- La dirección escrita y sus coordenadas viajan en `solicitarAsistencia` con `origenUbicacion: DIRECCION` (con GPS: `GPS`). Así se guarda en la asistencia y en la auditoría de dónde salió la ubicación.

Boceto: `boceto-ubicacion.png` (6 pasos: explicación, diálogo de Android, permiso negado, GPS apagado, escribir dirección, mapa con la dirección).

**Idiomas (español e inglés), app y backend:**
- Todos los textos de estas pantallas van al ARB en los dos idiomas (tabla abajo). Nada fijo en el código.
- El backend **no devuelve textos para mostrar**: `ubicarDireccion` responde `encontrada: false` o un código (`DIRECCION_NO_ENCONTRADA`, `SERVICIO_NO_DISPONIBLE`) y la app pone el mensaje en el idioma del usuario.
- Las sugerencias y la dirección normalizada sí vienen de Google: el BFF reenvía el idioma de la app (encabezado `Accept-Language`, `es` o `en`) y el ACL lo pasa como `languageCode` a Places y `language` a Geocoding. La caché de Redis incluye el idioma en la llave. La dirección que se guarda en la asistencia y en `auditoria_rutas` va siempre en español (se pide aparte con `es`) para que soporte y auditoría la lean igual.
- El diálogo de permiso lo traduce Android solo.

| Clave | Español | Inglés |
|---|---|---|
| `ubicacionExplicaTitulo` | Necesitamos tu ubicación | We need your location |
| `ubicacionExplicaTexto` | Para mostrarte los prestadores más cercanos y cuánto tardan en llegar. | To show you the nearest providers and how long they take to arrive. |
| `ubicacionPermitir` | Permitir ubicación | Allow location |
| `ubicacionNegadaTitulo` | No tenemos tu ubicación | We don't have your location |
| `ubicacionNegadaTexto` | Puedes darnos el permiso o escribir dónde estás. Con la dirección te mostramos los mismos prestadores. | You can grant permission or type where you are. With the address we show you the same providers. |
| `ubicacionAbrirAjustes` | Abrir Ajustes | Open Settings |
| `gpsApagadoTitulo` | Tu GPS está apagado | Your GPS is off |
| `gpsApagadoTexto` | Enciende la ubicación del teléfono para encontrar los prestadores cerca de ti. | Turn on your phone's location to find providers near you. |
| `gpsActivar` | Activar ubicación | Turn on location |
| `direccionEscribir` | Escribir mi dirección | Type my address |
| `direccionTitulo` | ¿Dónde estás? | Where are you? |
| `direccionCampo` | Dirección, ciudad o punto de referencia | Address, city or landmark |
| `direccionNoEncontrada` | No pudimos ubicar esa dirección. Prueba con otra o con un punto de referencia | We couldn't find that address. Try another one or a landmark |
| `direccionServicioCaido` | No pudimos buscar direcciones ahora. Intenta de nuevo | We couldn't search addresses right now. Try again |
| `direccionUsando` | Usando: {direccion} | Using: {address} |
| `direccionCambiar` | Cambiar | Change |
| `direccionUsarActual` | Usar mi ubicación actual | Use my current location |
| `direccionPinEtiqueta` | Dirección escrita | Typed address |


### 2.1 Mapa M06-01

| # | Falta | Regla |
|---|---|---|
| M0 | **Cambiar el mapa a Mapbox** | Reemplazar `widgets/mapa_prestadores.dart` con `MapWidget` de `mapbox_maps_flutter` (único archivo que conoce el mapa). Token **público** (`pk.…`) por `--dart-define=MAPBOX_ACCESS_TOKEN` desde `config/<ambiente>.json` y `MapboxOptions.setAccessToken` al iniciar; nunca en el repositorio. Si la guía de instalación vigente pide token de descarga para Gradle, va en `~/.gradle/gradle.properties` de cada desarrollador y en un secreto de CI. Restringir el token público a los estilos y scopes de lectura. Pines con `PointAnnotationManager` (imágenes generadas desde `SolMapPin`), rutas con `PolylineAnnotationManager`, cámara con `easeTo`/`flyTo`. Gestos: `rotateEnabled: false`, `pitchEnabled: false`; ocultar brújula y barra de escala. **El logo y la atribución de Mapbox no se quitan** (lo exigen sus términos). En pruebas y goldens se usa un widget falso inyectado por provider (la vista nativa no se pinta en `flutter test`). Quitar `flutter_map` y `latlong2` del `pubspec.yaml` y el `MAP_TILE_URL` de la configuración |
| M14 | **Botón de estilo (calles / satélite)** | Botón flotante circular de 40 dp (área táctil 48) arriba a la derecha del mapa, mismo diseño que «Volver a centrar» (blanco, sombra suave, ícono `layers` en `primary`). Alterna `MapboxStyles.MAPBOX_STREETS` y `MapboxStyles.SATELLITE_STREETS`; el ícono y la etiqueta de TalkBack dicen a cuál cambia («Ver satélite» / «Ver calles»). Al cambiar se mantienen cámara, pines, rutas y selección. Se recuerda la elección en `shared_preferences`. Por defecto: calles |
| M15 | **Rutas tipo semáforo a los 3 más cercanos** | Las 3 rutas que devuelve el backend (B8) se pintan como líneas: **verde** hasta 10 min, **amarillo** de 10 a 20, **rojo** más de 20 (colores a validar con diseño; contraste suficiente sobre calles y satélite, con borde blanco de 2 px). El tiempo siempre va escrito en la etiqueta del pin y en la lista, para no depender solo del color. Sin selección: las 3 de 4 px. Con un prestador seleccionado: su ruta de 6 px y las otras atenuadas al 30 %. Si el backend no devuelve rutas (falla o circuito abierto), no se pintan líneas y los tiempos salen de Haversine marcados como aproximados. **No hay navegación dentro de la app**: «Cómo llegar» sigue abriendo la app externa (M13) |
| M16 | **Radio de cobertura** | **No se dibuja** el círculo de 50 km: a zoom de ciudad no se ve y en una emergencia solo es ruido. Se usa para filtrar prestadores y, si no hay ninguno, aplica M11 |
| M1 | **Todos los prestadores de la ciudad en el mapa**, no solo 3 | Hoy `Cercania.masCercanos(cantidad: 3)` alimenta mapa y lista. Separar: `destacados` (los 3 más cercanos) y `otros` (el resto de la ciudad, ordenados por distancia). Los 3 destacados van más grandes (44 dp), con la etiqueta de tiempo («6 min») y encima; los demás pequeños (32 dp) y sin etiqueta |
| M2 | **Tipo grúa** | `TipoPrestador` pasa a `centroMedico`, `taller`, `grua`. Ícono de grúa `car_crash` (o `auto_towing` si está en `material_symbols_icons`) |
| M3 | **Color por aliado** | El ícono dice el tipo; el color dice si es aliado. Aliado: ícono `primary` sobre `primaryContainer`, más `verified` y el texto «Aliado Solventa». No aliado: `onSurfaceVariant` sobre `surfaceContainer`. Aplica a todos los pines y filas. Cambia el Figma (hoy el color va por tipo): avisar a diseño |
| M4 | **Tocar un pin abre el detalle** | Hoy el pin abre la ruta externa directo. Debe: vibración leve, pin seleccionado (crece un paso y anillo de 3 dp `primary`), enfocar la cámara (M6) y abrir la hoja del detalle (2.2) |
| M5 | **Tocar la fila enfoca el pin**, sin abrir el detalle | «Solicitar» de la fila sigue yendo directo a M06-02. Si la hoja estaba expandida, baja a la altura inicial |
| M6 | **Enfoque de cámara suave** | Un solo movimiento animado (`easeTo` de unos 400 ms; sin animación si «quitar animaciones»). Zoom destino `max(zoomActual, 15)`: nunca se aleja de golpe; si ya está a la vista y cerca, solo centra. Una animación nueva reemplaza a la anterior. Al quitar la selección (tocar mapa vacío) la cámara no se mueve |
| M7 | **Encuadre inicial** | Usuario + 3 destacados, zoom máximo 16, descontando el alto de la hoja. Encuadre con `cameraForCoordinates` y `padding` inferior igual a la hoja. Límites: zoom 11 a 18 y la cámara no sale de la ciudad (`setBounds`) |
| M8 | **Botón «Volver a centrar»** | Círculo blanco de 40 dp (área táctil 48), sombra suave, ícono `my_location` `primary`, a la derecha y 16 dp sobre la hoja. Solo aparece si el usuario movió el mapa con un gesto (o tras enfocar un prestador). Vuelve al encuadre inicial y se oculta. TalkBack: «Volver a centrar en mi ubicación» |
| M9 | **Lista como hoja arrastrable** | Hoy es fija. Tres alturas: mínima (solo título), inicial (título + 3 destacados, como el prototipo) y expandida (~85 %, toda la ciudad con subtítulo «Más prestadores en tu ciudad» y tiempos con «~»). El tirador tiene acciones de TalkBack «Expandir lista» / «Contraer lista» |
| M10 | **Pin del usuario** | El del Figma (pin con halo). Confirmar con diseño el color (el token más cercano es `error`) |
| M11 | **Fuera de cobertura** | Si no hay prestadores en 50 km: mapa con el pin del usuario y la hoja dice «Aún no tenemos prestadores en tu zona» |
| M13 | **«Cómo llegar» sin selector de apps** | Hoy `urisRuta` lanza primero `geo:`, que abre el selector si hay varias apps de mapas. Nuevo orden: 1) enlace universal de Google Maps con `LaunchMode.externalNonBrowserApplication` (abre Google Maps directo); 2) si no está, `geo:` (otra app de mapas); 3) el navegador predeterminado; 4) si nada abre, aviso con la dirección y «Copiar». En el manifest, `<queries>` para `https` y `geo` |
| M12 | **Estados y carreras** | Mapa bloqueado (velo blanco al 60 % y sin gestos) **solo en la primera carga**. Recargas sin bloquear. Descartar resultados viejos (número de generación), revisar `ref.mounted` tras cada `await`, recalcular solo si el usuario se movió más de 500 m. Toques rápidos: un solo detalle abierto, un solo `push` a M06-02, una sola llamada a `abrirRuta` a la vez |

### 2.2 Detalle del prestador (nuevo, al tocar un pin)

Hoja modal (`SolBottomSheet.show`), no el globo del mapa. De arriba abajo:

1. **Foto** de la sede o **placeholder por tipo** (`assets/prestadores/centro_medico.webp`, `taller.webp`, `grua.webp`, 720 × 200, menos de 40 KB, de banco libre con su licencia en `CREDITOS.md`), con velo blanco suave como el banner de M06-02.
2. **Encabezado** en `SolCard`: círculo del tipo (colores de M3), nombre 16/600 y, si es aliado, `verified` + «Aliado Solventa».
3. «Centro médico · Urgencias».
4. `schedule` + «1,2 km · llega en 6 min» (o «~» si no es de los 3).
5. Dirección en caja `surfaceContainer` con `location_on` (mantener pulsado copia).
6. Horario si viene («Abierto 24 h»).
7. **Solicitar asistencia** (`SolButton` primario, va a M06-02) y **Cómo llegar** (`SolButton` secundario, usa `RutaExternaService`; la hoja no se cierra).

Se cierra arrastrando, con atrás o tocando el velo; al cerrar, el pin sigue seleccionado y su fila queda marcada. TalkBack lee el bloque 2 a 4 junto. Boceto: `boceto-M06-01-tap-pin.png`.

### 2.3 Solicitud M06-02

| # | Falta | Regla |
|---|---|---|
| S1 | **Cambiar la dirección a mano por la dirección ubicada** | Hoy M06-02 tiene un campo de texto libre y la mutación `solicitarAsistenciaConDireccion` (sin coordenadas, sin tiempo de llegada). Se reemplaza por el flujo de 2.0: la dirección se escribe **antes** del mapa, se convierte en coordenadas y M06-02 solo la muestra. Quitar el campo y el estado `faltaDireccion` de M06-02 y dejar de usar `solicitarAsistenciaConDireccion` |
| S2 | **Banner con foto** | Usar la foto del prestador o el placeholder de su tipo, con velo blanco fuerte y «Atención inmediata» encima, como el Figma |
| S3 | **Tipo prellenado** | Hoy hay que elegirlo. **Propuesta:** prellenar según el tipo del prestador (y agregar «Grúa») para que el botón quede habilitado de entrada |
| S4 | **Dirección legible** (opcional) | Hoy muestra coordenadas. Si se quiere «Cra 45 # 22-18», el backend la resuelve (sección 3, B6); la app nunca llama a un geocodificador |

### 2.4 «Mis asistencias» (nueva pestaña)

Ver la sección 4.

## 3. Lo que falta en backend

| # | Falta | Detalle |
|---|---|---|
| B1 | **Catálogo con más datos** | Agregar a `Prestador`: `aliado: Boolean!`, `especialidad: String!`, `direccion: String!`, `horario: String`, `fotoUrl: String`. Subir la versión del catálogo para que las apps lo vuelvan a descargar |
| B2 | **Tipo `GRUA`** | En `TipoPrestador` (la app descarta tipos que no conoce, así que no rompe versiones viejas) y en la validación de `tipoAsistencia` |
| B3 | **Semilla** | Agregar grúas, marcar aliados y no aliados, y fotos en algunos (en ALMACENAMIENTO) y no en otros, para probar los dos casos. Actualizar `docs/hu-m06/prestadores-semilla.json` en mobile (lo compara una prueba) |
| B4 | **Retirar la dirección sin coordenadas** | Marcar `solicitarAsistenciaConDireccion` como `@deprecated` (la reemplaza `solicitarAsistencia` con las coordenadas de la dirección ubicada) y quitarla cuando la app deje de usarla. Agregar a `SolicitudAsistenciaInput` `origenUbicacion: OrigenUbicacion!` (`GPS` o `DIRECCION`) y `direccion: String` (obligatoria si es `DIRECCION`) |
| B5 | **Llegada de la asistencia** (para «Mis asistencias») | En `Asistencia`: `confirmadaEn: DateTime`, `llegadaEstimada: DateTime` (= `confirmadaEn` + `minutosEstimados`) y estado **`LLEGO`**, derivado al leer: una asistencia `CONFIRMADA` cuya `llegadaEstimada` ya pasó se devuelve como `LLEGO`. Así app y backend dicen lo mismo sin un job. Cuando haya prestadores reales, el prestador marcará la llegada |
| B7 | **Prestadores detrás del ACL (componente MAPS), obligatorio** | La arquitectura (Documento V8, HU-M06) pone la red de prestadores detrás del ACL, como KYC y firma. Hoy la simulación vive dentro de CLAIMS. Mover a: **`stubs/prestadores`** (servicio nuevo, mismo patrón que `stubs/kyc` y `stubs/firma`: FastAPI, Dockerfile, README, pruebas, puerto 8107 en `docker-compose.yml`) que expone `POST /asistencias` (confirma o no, y devuelve minutos) con el modo `primero_falla` que hoy tiene CLAIMS; **adaptador en `services/acl-worker`** (`adapters/prestadores.py` + puerto en `domain/puertos.py` + circuito en `application/circuitos.py`, variable `PRESTADORES_URL`); y **CLAIMS** deja de simular y llama al ACL. Si el circuito está abierto o el stub no responde, CLAIMS devuelve `NO_CONFIRMADA` (la app ya sabe reintentar). Agregar el servicio a `.github/servicios.json`, Terraform y CI como los otros stubs. La app no cambia |
| B8 | **Rutas con Mapbox en el ACL** | Adaptador `adapters/mapas_mapbox.py` en `acl-worker` (puerto + circuito como KYC): **Matrix API** (`driving-traffic`) para los tiempos de los ~10 más cercanos en línea recta y **Directions API** para la geometría de las 3 rutas (de cada prestador hacia el usuario). Token **secreto** de Mapbox en Secret Manager (`mapbox-token`, se carga a mano como Places; Terraform solo crea el contenedor y da acceso al `acl-worker`). **`stubs/mapas`** para local y CI que devuelve líneas rectas y tiempos por Haversine (variable `MAPAS_PROVEEDOR=mapbox|simulado`). Consulta en el BFF: `rutasPrestadores(latitud, longitud, prestadorIds: [ID!]!): [RutaPrestador!]!` con `prestadorId`, `distanciaMetros`, `duracionSegundos`, `geometria` (polyline6 o GeoJSON) y `estimado`. Caché en Redis por celda de ~200 m durante 2 min. Privacidad: la ubicación viaja al backend y a Mapbox al abrir el mapa (cambia la decisión anterior de que no saliera del teléfono; decidido con Felipe que va por el ACL y no directo desde el teléfono). No va a los logs de texto; actualizar el aviso de privacidad. **Auditoría (Felipe, 2026-11-10):** cada consulta de rutas deja un registro en la colección `auditoria_rutas` (dueño: el servicio que atiende la consulta en el BFF, sugerido CLAIMS): fecha, `clienteId`, `prestadorIds`, proveedor (`mapbox` o `simulado`), resultado (`OK`, `FALLA`, `CIRCUITO_ABIERTO`), latencia, la ubicación **exacta** (latitud y longitud, **cifradas** como en `asistencias`) y la **dirección aproximada** que resuelve el ACL con la **Geocoding API de Google** (geocodificación inversa; ya está activa y en la llave del servidor por HU-W03), con caché en Redis por celda de ~100 m para no pagar una llamada por cada apertura. Si Google falla, se guarda sin dirección. Retención de 90 días con índice TTL; acceso solo para roles de auditoría. Decidido con Felipe (2026-11-10) |
| B9 | **Ubicar una dirección escrita** | Consulta `ubicarDireccion(placeId: String, texto: String): UbicacionDireccion!` con `latitud`, `longitud`, `direccion` y `encontrada`. Con `placeId` usa Place Details de Places (agregar `location` a la máscara de campos del adaptador `adapters/lugares.py`); sin `placeId`, Geocoding API con el texto (restringida a Colombia). Va por el ACL con su circuito, caché en Redis y el modo `simulado` para local. Usa la llave `google-places-api-key` que ya tiene las dos APIs. Recibe el idioma (`es`/`en`) del BFF y responde códigos, no textos (ver «Idiomas» en 2.0) |
| B6 | **Consultas para el cliente** | `misAsistencias: [Asistencia!]!` (las del cliente, más recientes primero, últimos 30 días) y `asistencia(id: ID!): ResultadoAsistencia!` (para abrir el detalle desde la notificación). Solo CLIENTE y solo las suyas. Opcional: `direccionDeUbicacion(latitud, longitud): String` en el ACL con la Geocoding API ya activa (S4) |

Contrato sugerido (siguiente versión libre del contrato):

```graphql
enum TipoPrestador { MEDICO MECANICO GRUA }          # se conservan los valores actuales

type Prestador {
  # … campos actuales …
  aliado: Boolean!
  especialidad: String!
  direccion: String!
  horario: String
  fotoUrl: String
}

enum EstadoAsistencia { CONFIRMADA NO_CONFIRMADA CANCELADA LLEGO }

type Asistencia {
  # … campos actuales …
  confirmadaEn: DateTime
  llegadaEstimada: DateTime        # solo CONFIRMADA o LLEGO
}

type Query {
  misAsistencias: [Asistencia!]!
  asistencia(id: ID!): ResultadoAsistencia!
}
```

## 4. «Mis asistencias» (nueva pestaña de la barra inferior)

Boceto: `boceto-mis-asistencias.png`.

### 4.1 Dónde y qué se ve

- **Decidido con Felipe (2026-11-10):** es un **destino nuevo de la barra inferior**, no una sección de Inicio ni un botón en la barra superior. La barra queda: **Inicio · Asistencia · Mis asistencias · Cerrar sesión** (Material 3 permite hasta 5). Inicio no cambia.
- Ícono `assignment` (relleno cuando está activo) y **badge** con el número de asistencias en camino (sin badge si es 0). TalkBack: «Mis asistencias, 2 en camino».
- Ruta `/mis-asistencias` dentro del `SolAppShell`, con `SolTopLevelScaffold` y título «Mis asistencias».
- Sin asistencias: estado vacío «Aún no has pedido asistencia» con el botón «Solicitar asistencia en sitio», que lleva a la pestaña Asistencia.
- Dos **acordeones**:
  - **«En camino (N)»**, abierto por defecto. Asistencias confirmadas cuya llegada no ha pasado, de la más próxima a la más lejana. Las **no confirmadas** (el usuario salió de M06-04b sin reenviar) también van aquí, con el aviso «No pudimos confirmar» y «Reintentar», que reusa el reenvío de M06-03b con el mismo id (decidido con Felipe, 2026-11-10). No llevan cuenta regresiva ni notificación hasta que se confirmen; al confirmarse se programa la notificación.
  - **«Ya llegaron (N)»**, cerrado por defecto. Las que ya llegaron, de la más reciente a la más vieja. **Propuesta:** las canceladas no se muestran en Inicio.
- Si un acordeón queda vacío, se oculta. Si los dos lo están, se muestra el estado vacío.

### 4.2 Tarjeta

| Parte | Contenido |
|---|---|
| Ícono | Círculo del tipo con los colores de aliado o no (M3) |
| Título | Nombre del prestador (16/600) y, si es aliado, `verified` |
| Subtítulo | Tipo de asistencia · hora de solicitud («Médica · pedida 9:20 a. m.») |
| En camino | Cuenta regresiva grande en `primary`: «Llega en 4 min»; con menos de 1 min, «Llega en menos de 1 min». Debajo, «Hora estimada 9:26 a. m.» y una barra de progreso fina (de la confirmación a la llegada) |
| No confirmada | Aviso de advertencia «No pudimos confirmar» y botón «Reintentar» (reusa el reenvío de M06-03b) |
| Ya llegó | Insignia de éxito «Llegó · 9:26 a. m.»; sin cuenta regresiva |
| Toque | Toda la tarjeta (mín. 48 dp) abre el detalle (4.4). El chevron indica que se puede abrir |

TalkBack lee la tarjeta completa una vez («Clínica Norte, aliado Solventa, médica, llega en 4 minutos») y no anuncia cada segundo.

### 4.3 Cuenta regresiva y paso a «Ya llegaron»

- El tiempo restante sale de `llegadaEstimada` del backend (B5), que viene del tiempo calculado para el mapa. La app no lo recalcula.
- Un solo `Timer` de 1 s para toda la sección, activo solo mientras la pestaña está visible y la app en primer plano (se pausa en segundo plano y al cambiar de pestaña). El texto cambia por minuto; la barra se mueve por segundo.
- **Al llegar a cero:** la tarjeta sale de «En camino» con una animación corta (`AnimatedList`, `SolMotion.medium`) y entra arriba en «Ya llegaron». Se anuncia a TalkBack «Clínica Norte llegó». Si «Ya llegaron» estaba cerrado, su contador se actualiza y no se abre solo.
- Al entrar a la pestaña o volver a primer plano se recarga `misAsistencias` (para alinearse con el backend, que ya la devuelve como `LLEGO`). La respuesta manda sobre el reloj del teléfono.

### 4.4 Detalle de la asistencia (nuevo)

Pantalla `/asistencias/:id` con `SolFlowScaffold` («Tu asistencia», flecha atrás):

1. Banner con la foto o el placeholder del tipo (el mismo de 2.2).
2. Encabezado del prestador (como en 2.2) y el estado: «En camino · llega en 4 min», «Llegó · 9:26 a. m.» o «No pudimos confirmar».
3. Tipo de asistencia, hora de la solicitud y lo que escribió en «Describe qué pasó».
4. Dirección del prestador y «Cómo llegar» (secundario).
5. En camino: **Cancelar asistencia** (texto destructivo, abre `SolConfirmDialog` de M06-05). No confirmada: **Reintentar**. Ya llegó: sin acciones.

Atrás vuelve a «Mis asistencias» con la lista donde estaba. Si se abrió desde la notificación, atrás también va a «Mis asistencias» (no cierra la app).

### 4.4b Si el usuario se aleja del lugar (decidido con Felipe, 2026-11-10)

- La asistencia va al punto desde donde se pidió; la hora de llegada **no se recalcula** (no hay seguimiento en vivo).
- Mientras una asistencia está en camino y la pestaña o el detalle están abiertos, la app compara la posición actual con la de la solicitud. Si está a **más de 500 m**, muestra un `SolStatusBanner` de advertencia en la tarjeta y en el detalle: «Te alejaste del lugar donde pediste la asistencia. El prestador va a {dirección}».
- El aviso trae **«Cancelar y pedir otra»**: cancela la asistencia actual (confirmación de M06-05, cancela su notificación) y abre el mapa (pestaña Asistencia) desde la ubicación nueva.
- No se usa ubicación en segundo plano: solo se revisa con la app abierta.
- Pruebas: con ubicación falsa a 400 m no hay aviso; a 600 m sí; «Cancelar y pedir otra» cancela y navega al mapa.

### 4.5 Notificación al llegar

**Decidido con Felipe (2026-11-10): notificación local programada en el teléfono**, no push del servidor (FCM), porque hoy no hay infraestructura de push en la app ni en el backend (`firebase_messaging` está pendiente de HU-M04) y la llegada es simulada por tiempo. Cuando haya prestadores reales y FCM, se cambia el origen y la pantalla no cambia.

- Paquete: **`flutter_local_notifications`** (+ `timezone`). Al confirmarse una asistencia se programa una notificación para `llegadaEstimada` con `zonedSchedule` y modo `inexactAllowWhileIdle` (no pide el permiso de alarmas exactas; puede llegar hasta un par de minutos tarde, aceptable). Id de la notificación derivado del id de la asistencia.
- **No necesita internet** para mostrarse: queda programada al confirmarse la asistencia. Tras reiniciar el teléfono se reprograma (receptor de arranque de `flutter_local_notifications`, permiso `RECEIVE_BOOT_COMPLETED`, y además al abrir la app).
- Se **cancela** si la asistencia se cancela, y se **reprograma** si al recargar `misAsistencias` cambia `llegadaEstimada`.
- Texto: título «Tu asistencia llegó», cuerpo «Clínica Norte ya debería estar en tu ubicación». Canal de Android «Asistencias», importancia alta. Payload: el id de la asistencia.
- Permiso `POST_NOTIFICATIONS` (Android 13+): se pide al enviar la primera solicitud, con una línea de explicación antes. Si lo niega, todo funciona igual sin notificación.
- **Al tocarla:**
  - App cerrada o en segundo plano: abre la app, se valida la sesión (biometría o PIN, como siempre) y luego va a `/asistencias/:id` con «Mis asistencias» debajo en la pila, para que atrás vuelva ahí.
  - App abierta: no se muestra la notificación del sistema. En su lugar, un `SnackBar` «Clínica Norte llegó» con «Ver», y la tarjeta pasa a «Ya llegaron». Si el usuario ya está en ese detalle, solo cambia el estado.

### 4.6 Carreras y toques rápidos

- La notificación y el reloj pueden marcar la llegada al mismo tiempo: el paso a «Ya llegaron» es idempotente por id.
- Tocar varias tarjetas seguidas: una sola navegación.
- Cancelar mientras corre la cuenta regresiva: la tarjeta sale de la lista y la notificación se cancela antes de responder la mutación (si falla, se reprograma).
- Recargas de `misAsistencias` con número de generación (gana la última).

### 4.7 Textos nuevos (ARB, español e inglés)

| Clave | Español | Inglés |
|---|---|---|
| `navMisAsistencias` | Mis asistencias | My requests |
| `misAsistenciasVacio` | Aún no has pedido asistencia | You haven't requested assistance yet |
| `homeEnCamino` | En camino ({n}) | On the way ({n}) |
| `homeYaLlegaron` | Ya llegaron ({n}) | Arrived ({n}) |
| `asistenciaLlegaEn` | Llega en {minutos} min | Arrives in {minutes} min |
| `asistenciaLlegaMenosDeUno` | Llega en menos de 1 min | Arrives in less than 1 min |
| `asistenciaHoraEstimada` | Hora estimada {hora} | Estimated time {time} |
| `asistenciaLlego` | Llegó · {hora} | Arrived · {time} |
| `asistenciaNotifTitulo` | Tu asistencia llegó | Your assistance has arrived |
| `asistenciaNotifCuerpo` | {prestador} ya debería estar en tu ubicación | {provider} should be at your location now |
| `asistenciaLlegoSnack` | {prestador} llegó | {provider} has arrived |
| `asistenciaDetalleTitulo` | Tu asistencia | Your assistance |
| `asistenciaTeAlejaste` | Te alejaste del lugar donde pediste la asistencia. El prestador va a {direccion} | You moved away from where you requested assistance. The provider is going to {address} |
| `asistenciaCancelarYPedirOtra` | Cancelar y pedir otra | Cancel and request another |

### 4.8 Pruebas

- Notifier: agrupación en los dos acordeones, orden, paso a «Ya llegaron» al cruzar `llegadaEstimada` (con reloj falso), respuesta del backend que manda sobre el reloj, recargas fuera de orden.
- Servicio de notificaciones (falso): se programa al confirmar, se cancela al cancelar, se reprograma si cambia la hora.
- Widget y golden: la pestaña con 0, 1 y varias asistencias, y el badge de la barra; cada estado de tarjeta; detalle en cada estado; español, inglés y texto al 200 %.
- `integration_test`: tocar la notificación abre el detalle y atrás vuelve a «Mis asistencias».

## 5. Sistema de diseño

**Se reutiliza:** `SolTopLevelScaffold`, `SolSheetLayout`, `SolSectionHeader`, `SolProviderRow`, `SolBottomSheet.show`, `SolCard`, `SolButton`, `SolInlineNote`, `SolLoadingState`, `SolResultView`, `SolStatusBanner`, `SolStatusBadge`, `SolConfirmDialog`, `SolFlowScaffold`, `SolActionTile`, `SolIcons` y `SolFormat`.

**Falta agregar:**

| Componente | Para qué |
|---|---|
| `SolProviderType` con `centroMedico`, `taller`, `grua` y `SolIcons.towTruck`, `SolIcons.myLocation` | M2, M8 |
| `SolProviderRow` con `isPartner`, `onTap` de fila completa y tiempo con «~» | M3, M5, M9 |
| `SolDraggableSheet` | Hoja arrastrable de tres alturas (M9) |
| `SolMapPin` | Pin (tipo × aliado × destacado × seleccionado) renderizado a imagen para las anotaciones de Mapbox |
| `SolMapFab` | Botón flotante circular del mapa, para «Volver a centrar» y el cambio de estilo |
| `SolPhotoBanner` | Foto o placeholder con velo blanco y textos opcionales (2.2, S2, 4.4) |
| `SolBottomNav` con badge | Contador de en camino en el destino nuevo |
| `SolAccordion` | Acordeón con título y contador, accesible (4.1) |
| `SolAssistanceCard` | Tarjeta de «Mis asistencias» en sus tres estados (4.2) |
| Galería y goldens | Todas las variantes nuevas |

## 6. Relación con otras HU

- **M05** (reporte de siniestro) es la que registra en la tabla de siniestros que revisa el operador en **W04**. M06 no crea siniestros. Comparten solo el código de GPS y permisos (`core/ubicacion/`).
- **W07** (socios B2B) no tiene relación. Sus «socios» no son los «aliados» de M06: en el código usar siempre `aliado`.
- **M04** traerá FCM; cuando exista, la notificación de 4.5 puede pasar a push del servidor sin cambiar la pantalla.

## Anexo A · Costos y cuenta de Mapbox

- Felipe ya tiene cuenta. Crear dos tokens: uno **público** para la app (solo lectura de estilos y teselas) y uno **secreto** para el backend (Matrix y Directions), cada uno con el mínimo de scopes.
- La capa gratuita mensual de Mapbox (cargas de mapa en móvil, Matrix y Directions) alcanza de sobra para el proyecto; revisar la página de precios vigente y poner una alerta de uso en la cuenta.
- Si algún día se quiere volver a Google o a OpenStreetMap, cambia solo `mapa_prestadores.dart` y el adaptador del ACL: el contrato y las pantallas no cambian.

## Anexo B · Configuración manual, secretos, Terraform y CI/CD

Lo que se hace **a mano una vez**. Todo lo demás lo hacen Terraform y los pipelines a partir de `.github/servicios.json` (backend) y de los scripts de `tool/` (mobile).

### B.1 Mapbox (cuenta de Felipe)

1. En [account.mapbox.com](https://account.mapbox.com) → Tokens, crear:
   - **`solventa-mobile`**: token público (`pk.…`) solo con los scopes públicos por defecto (estilos, teselas, fuentes). Es el que va en la app.
   - **`solventa-acl`**: token aparte para el backend (Matrix y Directions). Aunque esas APIs aceptan scopes públicos, se trata como **secreto**: no va en la app y se puede rotar sin publicar otra versión.
   - Solo si la guía de instalación vigente de `mapbox_maps_flutter` lo pide para Android: **`solventa-descargas`**, token secreto con el scope `DOWNLOADS:READ`.
2. En la cuenta, poner una alerta de uso (Billing → notificaciones) para no pasarse de la capa gratuita.

### B.2 Google Cloud (proyecto `solventa-511815`)

- **No hay APIs nuevas de Google**: Geocoding y Places ya están activas y la llave `google-places-api-key` del `acl-worker` ya incluye las dos (HU-W03, `hu-w03/configuracion-manual-google-places.md`). Solo verificar que Place Details devuelva `location` con esa llave.
- **Secreto nuevo de Mapbox:** al agregar en `.github/servicios.json`, en el `acl-worker`, `"MAPBOX_TOKEN": "mapbox-token"` dentro de `secretos`, Terraform (`infra/servicios.tf`) crea el contenedor vacío y le da lectura a la cuenta del `acl-worker`. El valor se carga a mano (no puede quedar en el estado de Terraform):
  ```bash
  P=solventa-511815
  read -rsp "Token de Mapbox (solventa-acl): " K; echo
  printf '%s' "$K" | gcloud secrets versions add mapbox-token --project=$P --data-file=-; unset K
  ```

### B.3 Cambios en `.github/servicios.json` (backend)

- `acl-worker`:
  - en `entorno`: `"MAPAS_PROVEEDOR": "mapbox"`;
  - en `secretos`: `"MAPBOX_TOKEN": "mapbox-token"`;
  - en `llama`: agregar `"stub-prestadores"`.
- Servicio nuevo `stub-prestadores` (como `stub-kyc`): `"ruta": "stubs/prestadores"`, `"puerto": 8107`, `"url_env": "PRESTADORES_URL"`. Se despliega también en staging, porque no hay prestadores reales.
- `stub-mapas` **no** va en `servicios.json`: solo existe en `docker-compose.yml` para local y CI (en staging se usa Mapbox real).
- Con eso, `deploy-staging.yml` y Terraform crean el servicio, la imagen, los permisos entre servicios y el secreto. Revisar que `ci.yml` tome el servicio nuevo de `servicios.json`; si su matriz es fija, agregar `stubs/prestadores`.
- Actualizar el contrato (`contracts/graphql/schema.graphql`, `CHANGELOG.md`) y copiarlo a mobile (`tool/contrato.sh`).

### B.4 GitHub de mobile (`solventa-frontend-mobile`)

1. **Settings → Secrets and variables → Actions:**
   - secreto `MAPBOX_PUBLIC_TOKEN` con el token `solventa-mobile`;
   - solo si hace falta, secreto `MAPBOX_DOWNLOADS_TOKEN` con el de descargas.
2. En `develop.yml` y `cd.yml`, pasar el secreto como variable de entorno al paso de build. En `tool/build-dev.sh` y `tool/build-prod.sh`, agregar `--dart-define=MAPBOX_ACCESS_TOKEN="$MAPBOX_PUBLIC_TOKEN"`. Si hay token de descargas, exportarlo como `ORG_GRADLE_PROJECT_MAPBOX_DOWNLOADS_TOKEN`, y hacer lo mismo en el devcontainer.
3. Quitar `MAP_TILE_URL` de `config/*.json`. `emuladores.yml` y `test-lab.yml` usan `config/mock.json` con el mapa falso, así que no necesitan token.
4. Agregar `flutter_local_notifications` y `timezone`. En el `AndroidManifest.xml`, los permisos `POST_NOTIFICATIONS` y `RECEIVE_BOOT_COMPLETED` y los `<queries>` de `https` y `geo`. Volver a pasar MobSF (`seguridad.yml`), porque cambian los permisos.
5. Actualizar `CLAUDE.md` (D-M06 → Mapbox) y `docs/integracion-backend.md`.

### B.5 Probar en local

**Backend**
- `docker compose --profile servicios up -d --build --wait` levanta todo con `MAPAS_PROVEEDOR=simulado` (`stub-mapas`: líneas rectas) y `stub-prestadores`.
- Para probar Mapbox real en local: `MAPBOX_TOKEN` en el `.env` (ignorado por git) y `MAPAS_PROVEEDOR=mapbox`.
- Para no esperar en las pruebas de «Mis asistencias», `stub-prestadores` acepta `PRESTADORES_MINUTOS_FIJOS=1`: toda asistencia confirmada llega en 1 minuto.

**Mobile**
- Correr contra el backend local:
  ```bash
  flutter run --flavor dev --dart-define-from-file=config/local.json \
    --dart-define=MAPBOX_ACCESS_TOKEN=pk.xxxx
  ```
  También se puede usar un `config/secretos.local.json` ignorado por git.
- Emulador **con Google Play** (para «Cómo llegar»). La ubicación se cambia en *Extended controls → Location*; sirve para probar «Te alejaste».
- Para probar sin permiso: negarlo o apagar la ubicación del emulador y usar «Escribir mi dirección».
- Notificación: pedir una asistencia con `PRESTADORES_MINUTOS_FIJOS=1`, cerrar la app y esperar el minuto.

### B.6 Probar en staging

1. Al hacer merge a `develop` del backend, `deploy-staging.yml` despliega los servicios (incluido `stub-prestadores`) y aplica Terraform.
2. Cargar el valor de `mapbox-token` (B.2) **antes** del primer despliegue con `MAPAS_PROVEEDOR=mapbox`. Si falta, el ACL abre el circuito y el mapa sigue sin rutas y con tiempos aproximados (no se cae).
3. Al hacer merge a `develop` de mobile, `develop.yml` compila el APK `dev` (contra el gateway de staging, `config/dev.json`) y lo publica en Firebase App Distribution.
4. Recorrido de aceptación en un teléfono real:
   - con permiso;
   - sin permiso, con dirección escrita;
   - estilos calles y satélite;
   - rutas semáforo;
   - detalle y «Cómo llegar»;
   - solicitar (no confirmada → reintentar → confirmada);
   - cuenta regresiva y paso a «Ya llegaron»;
   - notificación con la app cerrada;
   - «Te alejaste».
5. Verificar en Mongo de staging que existan los registros de `auditoria_rutas` (ubicación cifrada y dirección aproximada) y que no aparezcan coordenadas en los logs de Cloud Logging.
