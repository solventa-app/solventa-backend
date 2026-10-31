# Sprint 3: alcance, subtareas, pesos y gráficos de cierre

> **Estado:** documentación de planeación, 2026-10-31. Solo lectura de Jira: no se creó el sprint ni se movió ningún issue.  
> **Fuentes:** Jira (proyecto KAN) para pesos, estados y fechas; `Solventa_Prototipado_Jira_Subtareas_v2.0.xlsx` (hoja «Subtareas v24», v24 del Excel de prototipado) para el detalle de cada pantalla.  
> **Libro de trabajo:** `Solventa_Sprint3_Cierre.xlsx` (mismos números; no versionado, ver la sección 8).

## 1. Alcance y fechas

- **Sprint:** KAN Sprint 3, del lunes **2 de noviembre** al **22 de noviembre de 2026** (3 semanas, 21 días calendario). Jira todavía no tiene el sprint creado; el Sprint 2 cierra el 2/11.
- **Criterio de alcance (decidido por Frans el 2026-10-31):** lo que falta hoy en Jira. Incluye el plan del Excel que sigue sin hacer (HU-M04 y HU-M06 condicional), HU-M02 (sin sprint en Jira) y el arrastre del Sprint 2 (HU-W03 y HU-W07).
- **Fuera del alcance:** HU-W04 (KAN-27) y HU-M01 (KAN-31). El Excel las ubicaba en el Sprint 3, pero en Jira están *Finalizadas dentro del Sprint 2* con todas sus subtareas cerradas. Tampoco entra el backlog sin sprint: HU-W02 (6 pts), HU-W06 (6), HU-M03 (13) y HU-M05 (6).

## 2. Pesos

- **Peso de la HU:** campos de Jira «Puntos de historia» y «Valor de negocio» (este último como fracción del total del producto).
- **Peso de la subtarea:** `puntos de la HU × factor ÷ suma de los factores de la HU`. Jira no tiene puntos por subtarea; el factor vale 1 por omisión (reparto igual) y se puede subir o bajar en el libro para una pantalla más o menos pesada. El peso de la HU no cambia, solo cómo se reparte.
- **Arrastre:** una subtarea cerrada antes del 2/11 (p. ej. W03-05, cerrada el 27/10) sale del alcance y no suma puntos. Por eso HU-W03 pesa 10,8 de 13 (5 de 6 subtareas abiertas).
- **Avance:** una subtarea cuenta como hecha solo si está *Finalizada* y tiene fecha de cierre; el burndown baja en esa fecha por el peso de la subtarea.

### Resumen por historia de usuario

| HU | Clave | Resp. | Puntos de la HU | Valor de negocio | Subtareas (en alcance) | **Puntos en alcance** | **% del sprint** | Origen |
|---|---|---|---:|---:|---:|---:|---:|---|
| HU-W03 | KAN-26 | f.taboada | 13 | 15% | 5 de 6 | **10,83** | 25,9% | Arrastre del Sprint 2 (solo lo que sigue abierto) |
| HU-W07 | KAN-30 | Daniel Felipe Urrego | 6 | 15% | 3 de 3 | **6,00** | 14,3% | Arrastre del Sprint 2 |
| HU-M02 | KAN-32 | Sin asignar | 6 | 3% | 5 de 5 | **6,00** | 14,3% | Pendiente en Jira (el Excel la ubicaba en el Sprint 2) |
| HU-M04 | KAN-38 | Sin asignar | 13 | 8% | 3 de 3 | **13,00** | 31,1% | Plan del Excel (Sprint 3) |
| HU-M06 | KAN-36 | Sin asignar | 6 | 3% | 8 de 8 | **6,00** | 14,3% | Plan del Excel (Sprint 3, condicional) |
| **Total** | | | | **41,5%** de valor en alcance | **24** | **41,83** | 100 % | |

**Lectura de riesgo:** la velocidad de los sprints cerrados fue de 26 puntos (Sprint 1: 26 de 26; Sprint 2, provisional: 26 de 45). El compromiso del Sprint 3 es de 41,8 puntos, **1,6 veces** esa velocidad. Opciones a decidir con el equipo: retirar HU-M06 (6 pts, ya es condicional), repartir el arrastre o aceptar un sprint con cola.

## 3. Detalle por historia de usuario

### HU-W03 · KAN-26 · Suscripción, cobro y emisión automática de póliza (Journey 2)

- **Plataforma:** Web · **Estado en Jira:** En curso · **Responsable:** f.taboada · **Épica:** KAN-19
- **Peso:** 13 puntos de historia, 15% de valor de negocio → **10,83 puntos en alcance** (25,9% del sprint)
- **Origen en el Sprint 3:** Arrastre del Sprint 2 (solo lo que sigue abierto)

| Clave | Pantalla | Subtarea | Rol | Estado (Jira) | Peso (pts) | % del sprint |
|---|---|---|---|---|---:|---:|
| KAN-152 | W03-01 | Revisar oferta aceptada | cliente | En revisión | 2,17 | 5,2% |
| KAN-153 | W03-02 | Firma electrónica | cliente | En revisión | 2,17 | 5,2% |
| KAN-172 | W03-02b | Confirmar firma y autorización de cobro | cliente | En revisión | 2,17 | 5,2% |
| KAN-154 | W03-03 | Firma confirmada | cliente | En revisión | 2,17 | 5,2% |
| KAN-155 | W03-04 | Póliza emitida | cliente | En revisión | 2,17 | 5,2% |
| KAN-157 | W03-05 | Documento de póliza (PDF) *(fuera: cerrada antes del sprint)* | cliente | Finalizada | 0,00 | 0,0% |

#### W03-01 · Revisar oferta aceptada (KAN-152)

**Historia de usuario (UX):** Como cliente, quiero revisar todas las condiciones antes de firmar, para aceptar con conocimiento.

**Criterios de aceptación UI:**

INTERFAZ
•  Resumen completo: prima, cobertura, plazo, score y entidad beneficiaria
•  Aviso legal de qué ocurre al continuar
•  Botón primario «Aceptar y continuar» y enlace a las fuentes

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Badge de oferta aprobada y lista para firma, con texto

ACCESIBILIDAD
•  El resumen es una lista de pares etiqueta–valor, legible por lector de pantalla

RESPONSIVE (Tailwind)
•  La tarjeta ocupa el contenedor de 880 px y se apila en una columna en móvil
•  Responsive con Tailwind — puntos de corte sm 640 / md 768 / lg 1024 / xl 1280. Contenedor de 880 px y márgenes de 80 px en el lienzo de 1280 px.

- **Navegación:** Entra desde: W01-03a · W01-03b
Sale a: W03-02 · W01-04
- **Componentes reutilizados:** Botón · Divisor · Badge de estado
- **Wireframe de referencia:** Adjuntar el wireframe W03-01 del archivo de baja fidelidad (Solventa-web, nodo 23:2). · [baja fidelidad](https://www.figma.com/design/2vfK0e8t62OvvDk4c7QnK5/Solventa-web?node-id=23-2) · [prototipo final](https://www.figma.com/design/yKWD8jrwERtJkuSj0mk0Gz/Solventa-web-Final?node-id=2001-1704)
- **Frente al plano v21:** Existía en v21 · sin cambios

#### W03-02 · Firma electrónica (KAN-153)

**Historia de usuario (UX):** Como cliente, quiero leer el contrato antes de firmarlo, para saber a qué me obligo.

**Criterios de aceptación UI:**

INTERFAZ
•  Contrato por cláusulas, con radicado y fecha de emisión
•  Cláusula de prima visible dentro del documento
•  Botón «Firmar» y casilla de lectura y aceptación
•  Nota de firma electrónica simulada

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Firmar no resuelve el trámite: lleva a la confirmación W03-02b

ACCESIBILIDAD
•  El documento es texto seleccionable, no imagen; permite lectura con zoom al 200 %

RESPONSIVE (Tailwind)
•  El bloque del contrato crece con su contenido y nunca recorta las cláusulas
•  Responsive con Tailwind — puntos de corte sm 640 / md 768 / lg 1024 / xl 1280. Contenedor de 880 px y márgenes de 80 px en el lienzo de 1280 px.

- **Navegación:** Entra desde: W03-01 · W03-02b
Sale a: W03-02b · G-02
- **Componentes reutilizados:** Botón · Plantilla de documento formal
- **Wireframe de referencia:** Adjuntar el wireframe W03-02 del archivo de baja fidelidad (Solventa-web, nodo 23:19). · [baja fidelidad](https://www.figma.com/design/2vfK0e8t62OvvDk4c7QnK5/Solventa-web?node-id=23-19) · [prototipo final](https://www.figma.com/design/yKWD8jrwERtJkuSj0mk0Gz/Solventa-web-Final?node-id=2001-1832)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v22

#### W03-02b · Confirmar firma y autorización de cobro (KAN-172)

**Historia de usuario (UX):** Como cliente, quiero que me digan cuánto y cada cuánto me van a cobrar antes de firmar, para no descubrirlo después en el extracto.

**Criterios de aceptación UI:**

INTERFAZ
•  Monto de la prima en primer plano, con periodicidad y duración
•  Lista de qué ocurre al confirmar: registro de firma, emisión y activación del cobro
•  Dos salidas: confirmar o volver al contrato

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Se declara que la acción no se puede deshacer desde la aplicación

ACCESIBILIDAD
•  El monto se lee como texto y está asociado a su etiqueta

RESPONSIVE (Tailwind)
•  La tarjeta ámbar mantiene el contraste AA en cualquier tamaño de pantalla
•  Responsive con Tailwind — puntos de corte sm 640 / md 768 / lg 1024 / xl 1280. Contenedor de 880 px y márgenes de 80 px en el lienzo de 1280 px.

- **Navegación:** Entra desde: W03-02
Sale a: W03-03 · W03-02 · G-02
- **Componentes reutilizados:** Botón · Botón secundario · Badge de alerta
- **Wireframe de referencia:** Sin wireframe propio: nace del hallazgo de la ronda v22 sobre W03-02 (wireframe 23:19). · [baja fidelidad](https://www.figma.com/design/2vfK0e8t62OvvDk4c7QnK5/Solventa-web?node-id=23-19) · [prototipo final](https://www.figma.com/design/yKWD8jrwERtJkuSj0mk0Gz/Solventa-web-Final?node-id=2020-32)
- **Frente al plano v21:** NUEVA — no existe en el plano v21. Es la «pantalla de resultado del pago» que el plan pedía crear (D-03); ronda v22.

#### W03-03 · Firma confirmada (KAN-154)

**Historia de usuario (UX):** Como cliente, quiero saber que mi firma quedó registrada y qué sigue, para no repetir la operación.

**Criterios de aceptación UI:**

INTERFAZ
•  Confirmación de firma registrada
•  Aclaración de que la emisión continúa en segundo plano
•  Sin acción obligatoria

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Auto-avance a W03-04 cuando la emisión termina

ACCESIBILIDAD
•  El cambio de pantalla se anuncia; no depende solo del movimiento visual

RESPONSIVE (Tailwind)
•  El bloque se mantiene centrado en cualquier ancho
•  Responsive con Tailwind — puntos de corte sm 640 / md 768 / lg 1024 / xl 1280. Contenedor de 880 px y márgenes de 80 px en el lienzo de 1280 px.

- **Navegación:** Entra desde: W03-02b
Sale a: W03-04 (auto) · G-02
- **Componentes reutilizados:** Ícono de estado · Botón secundario
- **Wireframe de referencia:** Adjuntar el wireframe W03-03 del archivo de baja fidelidad (Solventa-web, nodo 23:31). · [baja fidelidad](https://www.figma.com/design/2vfK0e8t62OvvDk4c7QnK5/Solventa-web?node-id=23-31) · [prototipo final](https://www.figma.com/design/yKWD8jrwERtJkuSj0mk0Gz/Solventa-web-Final?node-id=2001-1969)
- **Frente al plano v21:** Existía en v21 · sin cambios

#### W03-04 · Póliza emitida (KAN-155)

**Historia de usuario (UX):** Como cliente, quiero mi número de póliza y el documento, para tener prueba de lo que contraté.

**Criterios de aceptación UI:**

INTERFAZ
•  Número de póliza, fecha de emisión y vigencia
•  Botón «Descargar documento» y enlace a las fuentes consultadas
•  Nota de que la póliza también llega por correo

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Ícono de éxito con texto; el número de póliza es copiable

ACCESIBILIDAD
•  El número de póliza se lee carácter por carácter con lector de pantalla

RESPONSIVE (Tailwind)
•  Los dos botones se apilan por debajo de 640 px
•  Responsive con Tailwind — puntos de corte sm 640 / md 768 / lg 1024 / xl 1280. Contenedor de 880 px y márgenes de 80 px en el lienzo de 1280 px.

- **Navegación:** Entra desde: W03-03 · punto de entrada · W03-05
Sale a: W03-05 · W01-04 · G-02
- **Componentes reutilizados:** Botón · Ícono de estado
- **Wireframe de referencia:** Adjuntar el wireframe W03-04 del archivo de baja fidelidad (Solventa-web, nodo 23:42). · [baja fidelidad](https://www.figma.com/design/2vfK0e8t62OvvDk4c7QnK5/Solventa-web?node-id=23-42) · [prototipo final](https://www.figma.com/design/yKWD8jrwERtJkuSj0mk0Gz/Solventa-web-Final?node-id=2001-2057)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v24 (revisión final de UX)

#### W03-05 · Documento de póliza (PDF) (KAN-157)

**Historia de usuario (UX):** Como cliente, quiero ver el documento formal de mi póliza, para guardarlo o imprimirlo.

**Criterios de aceptación UI:**

INTERFAZ
•  Documento con membrete, datos del asegurado, del crédito y de la cobertura
•  Firma electrónica y nota legal
•  Salida de regreso

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  El documento indica su estado: emitido y vigente

ACCESIBILIDAD
•  El documento es texto, no una imagen escaneada

RESPONSIVE (Tailwind)
•  Se puede leer al 200 % de zoom sin desplazamiento horizontal
•  Responsive con Tailwind — puntos de corte sm 640 / md 768 / lg 1024 / xl 1280. Contenedor de 880 px y márgenes de 80 px en el lienzo de 1280 px.

- **Navegación:** Entra desde: W03-04 · G-02 · G-02a
Sale a: W03-04 · G-02
- **Componentes reutilizados:** Plantilla de documento formal
- **Wireframe de referencia:** Adjuntar el wireframe W03-05 del archivo de baja fidelidad (Solventa-web, nodo 39:2). · [baja fidelidad](https://www.figma.com/design/2vfK0e8t62OvvDk4c7QnK5/Solventa-web?node-id=39-2) · [prototipo final](https://www.figma.com/design/yKWD8jrwERtJkuSj0mk0Gz/Solventa-web-Final?node-id=2001-2128)
- **Frente al plano v21:** Existía en v21 · sin cambios

**Notas de la HU:** las subtareas de Jira son pantallas; el trabajo técnico que falta (flujo del CLIENTE en el front, «Mis pólizas», avisos en vivo, integración de punta a punta) está en la sección 5 como propuesta y no pesa en los gráficos.

### HU-W07 · KAN-30 · Alta de socios de distribución e integración (API-First — Journey 1)

- **Plataforma:** Web · **Estado en Jira:** Tareas por hacer · **Responsable:** Daniel Felipe Urrego · **Épica:** KAN-20
- **Peso:** 6 puntos de historia, 15% de valor de negocio → **6,00 puntos en alcance** (14,3% del sprint)
- **Origen en el Sprint 3:** Arrastre del Sprint 2

| Clave | Pantalla | Subtarea | Rol | Estado (Jira) | Peso (pts) | % del sprint |
|---|---|---|---|---|---:|---:|
| KAN-121 | W07-01 | Login del socio | socio de distribución | Tareas por hacer | 2,00 | 4,8% |
| KAN-122 | W07-02 | Dashboard de API | socio de distribución | Tareas por hacer | 2,00 | 4,8% |
| KAN-123 | W07-03 | Documentación / playground | socio de distribución | Tareas por hacer | 2,00 | 4,8% |

#### W07-01 · Login del socio (KAN-121)

**Historia de usuario (UX):** Como socio, quiero autenticarme con credenciales técnicas, para llegar a mi consumo de API sin pasar por el portal del cliente.

**Criterios de aceptación UI:**

INTERFAZ
•  Campos de ID de socio y clave de acceso, marcados como credenciales de socio
•  Nota de canal restringido y registro de auditoría
•  Enlace de regreso al inicio de sesión de Solventa

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Credenciales inválidas: mensaje bajo el campo

ACCESIBILIDAD
•  La clave permite mostrarse sin perder el foco

RESPONSIVE (Tailwind)
•  La tarjeta de acceso se mantiene centrada, máximo 400 px
•  Responsive con Tailwind — puntos de corte sm 640 / md 768 / lg 1024 / xl 1280. Contenedor de 880 px y márgenes de 80 px en el lienzo de 1280 px.

- **Navegación:** Entra desde: G-01 · W07-02
Sale a: W07-02 · G-01
- **Componentes reutilizados:** Campo de formulario · Botón
- **Wireframe de referencia:** Adjuntar el wireframe W07-01 del archivo de baja fidelidad (Solventa-web, nodo 30:2). · [baja fidelidad](https://www.figma.com/design/2vfK0e8t62OvvDk4c7QnK5/Solventa-web?node-id=30-2) · [prototipo final](https://www.figma.com/design/yKWD8jrwERtJkuSj0mk0Gz/Solventa-web-Final?node-id=2001-3340)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v24 (revisión final de UX)

#### W07-02 · Dashboard de API (KAN-122)

**Historia de usuario (UX):** Como socio, quiero ver mi clave, mi consumo y el estado del servicio, para operar mi integración sin pedir soporte.

**Criterios de aceptación UI:**

INTERFAZ
•  Clave enmascarada con acción de mostrar
•  Consumo de cuota con porcentaje y llamadas restantes
•  Estado del servicio con latencia y disponibilidad
•  Acción de regenerar con advertencia de consecuencia
•  Cerrar sesión en la barra superior

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Regenerar advierte que la clave vigente deja de funcionar de inmediato
•  El estado del servicio se dice con texto además del color

ACCESIBILIDAD
•  El porcentaje de cuota se expresa también en número, no solo en barra

RESPONSIVE (Tailwind)
•  Las tres tarjetas pasan a una columna por debajo de 1024 px
•  Responsive con Tailwind — puntos de corte sm 640 / md 768 / lg 1024 / xl 1280. Contenedor de 880 px y márgenes de 80 px en el lienzo de 1280 px.

- **Navegación:** Entra desde: W07-01 · W07-03
Sale a: W07-03 · W07-01
- **Componentes reutilizados:** Botón · Botón peligro · Badge de estado
- **Wireframe de referencia:** Adjuntar el wireframe W07-02 del archivo de baja fidelidad (Solventa-web, nodo 30:32). · [baja fidelidad](https://www.figma.com/design/2vfK0e8t62OvvDk4c7QnK5/Solventa-web?node-id=30-32) · [prototipo final](https://www.figma.com/design/yKWD8jrwERtJkuSj0mk0Gz/Solventa-web-Final?node-id=2001-3562)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v24 (revisión final de UX)

#### W07-03 · Documentación / playground (KAN-123)

**Historia de usuario (UX):** Como socio, quiero ver los endpoints y un ejemplo de llamada, para integrarme sin adivinar el contrato.

**Criterios de aceptación UI:**

INTERFAZ
•  Lista de endpoints con método, ruta y descripción
•  Ejemplo de petición y respuesta
•  Acción de copiar el ejemplo
•  Regreso al portal de socios

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  La acción de copiar confirma con un mensaje temporal

ACCESIBILIDAD
•  El código de ejemplo es texto seleccionable, con contraste AA

RESPONSIVE (Tailwind)
•  El bloque de código se desplaza horizontalmente sin romper la página
•  Responsive con Tailwind — puntos de corte sm 640 / md 768 / lg 1024 / xl 1280. Contenedor de 880 px y márgenes de 80 px en el lienzo de 1280 px.

- **Navegación:** Entra desde: W07-02
Sale a: W07-02
- **Componentes reutilizados:** Tabla operativa · Botón secundario
- **Wireframe de referencia:** Adjuntar el wireframe W07-03 del archivo de baja fidelidad (Solventa-web, nodo 30:66). · [baja fidelidad](https://www.figma.com/design/2vfK0e8t62OvvDk4c7QnK5/Solventa-web?node-id=30-66) · [prototipo final](https://www.figma.com/design/yKWD8jrwERtJkuSj0mk0Gz/Solventa-web-Final?node-id=2001-3434)
- **Frente al plano v21:** Existía en v21 · sin cambios

**Notas de la HU:** asignada a Daniel. El **backend ya está cerrado en código y en local** (plan de HU-W07, bloques 0 a 9, 29/10); en Jira la HU y sus tres subtareas —las pantallas del portal del socio— siguen en «Tareas por hacer», y esas pantallas son lo que pesa aquí. Si se cierra antes del 2/11, sale del alcance.

### HU-M02 · KAN-32 · Autenticación biométrica nativa (Huella / Rostro)

- **Plataforma:** Móvil · **Estado en Jira:** Tareas por hacer · **Responsable:** Sin asignar · **Épica:** KAN-21
- **Peso:** 6 puntos de historia, 3% de valor de negocio → **6,00 puntos en alcance** (14,3% del sprint)
- **Origen en el Sprint 3:** Pendiente en Jira (el Excel la ubicaba en el Sprint 2)

| Clave | Pantalla | Subtarea | Rol | Estado (Jira) | Peso (pts) | % del sprint |
|---|---|---|---|---|---:|---:|
| KAN-134 | M02-01 | Prompt de biometría nativa | cliente que regresa | Tareas por hacer | 1,20 | 2,9% |
| KAN-135 | M02-01a | Biometría no reconocida | cliente que regresa | Tareas por hacer | 1,20 | 2,9% |
| KAN-136 | M02-02 | Ingreso alterno con PIN | cliente que regresa | Tareas por hacer | 1,20 | 2,9% |
| KAN-174 | M02-02b | PIN ingresado | cliente que regresa | Tareas por hacer | 1,20 | 2,9% |
| KAN-137 | M02-03 | Acceso concedido | cliente que regresa | Tareas por hacer | 1,20 | 2,9% |

#### M02-01 · Prompt de biometría nativa (KAN-134)

**Historia de usuario (UX):** Como cliente que vuelve a la app, quiero entrar con mi huella o mi rostro, para no escribir una contraseña cada vez.

**Criterios de aceptación UI:**

INTERFAZ
•  Mockup del diálogo nativo del sistema (Face ID / huella)
•  Enlace visible «Usar PIN del teléfono en su lugar»
•  Disparador de demo para recorrer el camino de fallo

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  El fallo no cae directo al teclado numérico: pasa por M02-01a, que dice qué ocurrió
•  El éxito pasa por M02-03, para que el desenlace positivo sea visible

ACCESIBILIDAD
•  La alternativa al PIN es un control enfocable, no un texto pequeño al pie
•  El diálogo nativo respeta el tamaño de fuente del sistema

ADAPTATIVO (Material 3)
•  El diálogo se ancla al borde inferior, como lo hace el del sistema
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: G-03 (Cerrar sesión)
Sale a: M02-03 · M02-01a · M02-02
- **Componentes reutilizados:** Botón (Texto) · Disparador de demo
- **Wireframe de referencia:** Adjuntar el wireframe M02-01 del archivo de baja fidelidad (Solventa-movil, nodo 10:79). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=10-79) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-565)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v21

#### M02-01a · Biometría no reconocida (KAN-135)

**Historia de usuario (UX):** Como cliente al que la biometría no reconoció, quiero saber que ese fue el problema y elegir cómo seguir, para no pensar que perdí la cuenta.

**Criterios de aceptación UI:**

INTERFAZ
•  Ícono de error y mensaje «No te reconocimos»
•  Dos salidas explícitas: reintentar biometría o usar el PIN del teléfono
•  Aclaración de que el PIN es el del dispositivo, no uno de Solventa

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  El error nombra la causa probable y ofrece las dos vías, sin dejar a la persona sin opciones

ACCESIBILIDAD
•  El mensaje de error se anuncia al entrar a la pantalla
•  Ambas salidas son botones de 48 dp, no enlaces de texto pequeño

ADAPTATIVO (Material 3)
•  Las dos acciones se apilan verticalmente con separación de 12 dp en cualquier ancho
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M02-01
Sale a: M02-01 · M02-02
- **Componentes reutilizados:** Botón (Primario) · Botón (Tonal)
- **Wireframe de referencia:** Adjuntar el wireframe M02-01a del archivo de baja fidelidad (Solventa-movil, nodo 42:169). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=42-169) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-472)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v21

#### M02-02 · Ingreso alterno con PIN (KAN-136)

**Historia de usuario (UX):** Como cliente que no pudo usar la biometría, quiero entrar con el PIN de mi teléfono, para no quedarme fuera de mi cuenta.

**Criterios de aceptación UI:**

INTERFAZ
•  Teclado numérico centrado con teclas de al menos 48 dp
•  Cuatro indicadores de PIN, vacíos al entrar
•  Aclaración de que es el PIN del dispositivo
•  Botón «Ingresar» deshabilitado hasta que haya dígitos

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Tocar un dígito llena el indicador y habilita el ingreso (M02-02b)
•  Un PIN incorrecto se reporta sin borrar el contexto ni culpar a la persona

ACCESIBILIDAD
•  Cada tecla se anuncia por su número; los indicadores dicen cuántos dígitos van
•  El borrado es una tecla propia, no un gesto oculto

ADAPTATIVO (Material 3)
•  El teclado conserva su proporción y no se estira en pantallas altas
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M02-01 · M02-01a
Sale a: M02-02b
- **Componentes reutilizados:** Botón (Deshabilitado)
- **Wireframe de referencia:** Adjuntar el wireframe M02-02 del archivo de baja fidelidad (Solventa-movil, nodo 10:91). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=10-91) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-607)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v23

#### M02-02b · PIN ingresado (KAN-174)

**Historia de usuario (UX):** Como cliente que terminó de digitar el PIN, quiero ver que el ingreso ya está disponible, para no tocar un botón que no responde.

**Criterios de aceptación UI:**

INTERFAZ
•  Cuatro indicadores llenos
•  Botón «Ingresar» en estado primario habilitado

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  El paso de deshabilitado a habilitado es la confirmación de que el PIN quedó completo

ACCESIBILIDAD
•  El cambio de estado del botón se anuncia, además de verse

ADAPTATIVO (Material 3)
•  Idéntico a M02-02
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M02-02
Sale a: M02-03
- **Componentes reutilizados:** Botón (Primario)
- **Wireframe de referencia:** Usa el wireframe de M02-02 (10:91): es su estado habilitado, no una pantalla nueva del plano. · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=10-91) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2018-525)
- **Frente al plano v21:** NUEVA — no existe en el plano v21. Estado habilitado del PIN, ronda v23.

#### M02-03 · Acceso concedido (KAN-137)

**Historia de usuario (UX):** Como cliente que acaba de autenticarse, quiero una confirmación breve antes de entrar, para saber que la sesión se abrió a mi nombre.

**Criterios de aceptación UI:**

INTERFAZ
•  Ícono de acceso concedido y saludo con el nombre
•  Estado transitorio, sin acciones
•  Nota de que en producción la biometría abre la app sin esta pantalla

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Auto-avance de 1,5 s al home; el éxito se ve y luego cede el paso

ACCESIBILIDAD
•  El mensaje se anuncia una vez; el ícono es decorativo

ADAPTATIVO (Material 3)
•  Bloque centrado, sin dependencia del alto del dispositivo
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M02-01 · M02-02b
Sale a: G-03
- **Componentes reutilizados:** —
- **Wireframe de referencia:** Adjuntar el wireframe M02-03 del archivo de baja fidelidad (Solventa-movil, nodo 42:180). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=42-180) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-499)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v21

**Notas de la HU:** sin sprint en Jira; el Excel la planeaba para el Sprint 2. Incluye M02-02b (estado «PIN ingresado»), un estado de pantalla y no una pantalla nueva.

### HU-M04 · KAN-38 · Procesamiento de Siniestro Paramétrico Automático (Journey 4)

- **Plataforma:** Móvil · **Estado en Jira:** Tareas por hacer · **Responsable:** Sin asignar · **Épica:** KAN-21
- **Peso:** 13 puntos de historia, 8% de valor de negocio → **13,00 puntos en alcance** (31,1% del sprint)
- **Origen en el Sprint 3:** Plan del Excel (Sprint 3)

| Clave | Pantalla | Subtarea | Rol | Estado (Jira) | Peso (pts) | % del sprint |
|---|---|---|---|---|---:|---:|
| KAN-138 | M04-01 | Notificación push | cliente con póliza activa | Tareas por hacer | 4,33 | 10,4% |
| KAN-139 | M04-02 | Pantalla de celebración | cliente indemnizado | Tareas por hacer | 4,33 | 10,4% |
| KAN-146 | M04-03 | Detalle de la indemnización | cliente indemnizado | Tareas por hacer | 4,33 | 10,4% |

#### M04-01 · Notificación push (KAN-138)

**Historia de usuario (UX):** Como cliente afectado por un evento cubierto, quiero enterarme por una notificación de que ya me pagaron, para no tener que abrir la app a revisar.

**Criterios de aceptación UI:**

INTERFAZ
•  Mockup del banner de notificación del sistema Android
•  Texto de la notificación con el hecho y el monto
•  Entrada al detalle al tocar el banner

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  La notificación es el mensaje de éxito del pago paramétrico: llega sin que la persona la pida

ACCESIBILIDAD
•  El banner respeta el tamaño de fuente del sistema y se lee completo

ADAPTATIVO (Material 3)
•  El banner ocupa el ancho disponible con los márgenes del sistema
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: Punto de entrada de la demo
Sale a: M04-02
- **Componentes reutilizados:** —
- **Wireframe de referencia:** Adjuntar el wireframe M04-01 del archivo de baja fidelidad (Solventa-movil, nodo 10:136). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=10-136) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-286)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v23 · sin animación automática

#### M04-02 · Pantalla de celebración (KAN-139)

**Historia de usuario (UX):** Como cliente que recibió una indemnización, quiero ver cuánto me depositaron y dónde, para confirmar que el dinero está donde espero.

**Criterios de aceptación UI:**

INTERFAZ
•  Ícono de celebración y monto depositado, destacado
•  Cuenta de destino enmascarada
•  Botón para ver el detalle del evento

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Éxito comunicado con ícono, color y palabra
•  El monto se muestra con la moneda seleccionada en la barra superior

ACCESIBILIDAD
•  El monto se lee como cifra completa, no como texto decorativo
•  El ícono de celebración se excluye del recorrido de foco

ADAPTATIVO (Material 3)
•  El bloque del monto no se corta: el texto crece en alto si la cifra es larga
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: G-03 · M04-01
Sale a: M04-03
- **Componentes reutilizados:** Botón (Primario) · Barra superior (Con volver)
- **Wireframe de referencia:** Adjuntar el wireframe M04-02 del archivo de baja fidelidad (Solventa-movil, nodo 10:147). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=10-147) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-301)
- **Frente al plano v21:** Existía en v21 · sin cambios

#### M04-03 · Detalle de la indemnización (KAN-146)

**Historia de usuario (UX):** Como cliente indemnizado, quiero ver qué evento activó el pago y cómo se calculó, para confiar en que la regla se aplicó bien.

**Criterios de aceptación UI:**

INTERFAZ
•  Evento detectado con fecha, fuente y umbral superado
•  Desglose del cálculo del pago
•  Referencia del siniestro para soporte

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Cada dato dice de dónde salió; el cálculo no es una caja negra

ACCESIBILIDAD
•  La tabla de desglose se lee fila por fila con encabezado asociado

ADAPTATIVO (Material 3)
•  El desglose pasa a lista apilada cuando el ancho no alcanza para dos columnas
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M04-02
Sale a: M04-02
- **Componentes reutilizados:** Barra superior (Con volver)
- **Wireframe de referencia:** Adjuntar el wireframe M04-03 del archivo de baja fidelidad (Solventa-movil, nodo 10:160). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=10-160) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-217)
- **Frente al plano v21:** Existía en v21 · sin cambios

**Notas de la HU:** según su descripción en Jira, el backend cruza eventos externos con las pólizas activas y liquida el pago; la app recibe la notificación push y muestra la pantalla de celebración. Esas pantallas no se pueden probar de punta a punta sin ese backend.

### HU-M06 · KAN-36 · Geolocalización de prestadores y asistencia en sitio (Journey 3)

- **Plataforma:** Móvil · **Estado en Jira:** Tareas por hacer · **Responsable:** Sin asignar · **Épica:** KAN-22
- **Peso:** 6 puntos de historia, 3% de valor de negocio → **6,00 puntos en alcance** (14,3% del sprint)
- **Origen en el Sprint 3:** Plan del Excel (Sprint 3, condicional)

| Clave | Pantalla | Subtarea | Rol | Estado (Jira) | Peso (pts) | % del sprint |
|---|---|---|---|---|---:|---:|
| KAN-147 | M06-01 | Mapa de prestadores cercanos | cliente en carretera | Tareas por hacer | 0,75 | 1,8% |
| KAN-148 | M06-02 | Formulario de solicitud | cliente en carretera | Tareas por hacer | 0,75 | 1,8% |
| KAN-149 | M06-03 | Enviando solicitud | cliente en carretera | Tareas por hacer | 0,75 | 1,8% |
| KAN-150 | M06-03b | Reenviando solicitud | cliente en carretera | Tareas por hacer | 0,75 | 1,8% |
| KAN-151 | M06-04a | Asistencia confirmada | cliente en carretera | Tareas por hacer | 0,75 | 1,8% |
| KAN-156 | M06-04b | No pudimos confirmar | cliente en carretera | Tareas por hacer | 0,75 | 1,8% |
| KAN-158 | M06-05 | ¿Cancelar asistencia? (confirmación) | cliente en carretera | Tareas por hacer | 0,75 | 1,8% |
| KAN-159 | M06-06 | Asistencia cancelada | cliente en carretera | Tareas por hacer | 0,75 | 1,8% |

#### M06-01 · Mapa de prestadores cercanos (KAN-147)

**Historia de usuario (UX):** Como cliente varado, quiero ver qué prestadores tengo cerca y pedir ayuda desde ahí, para resolver sin llamar a un conmutador.

**Criterios de aceptación UI:**

INTERFAZ
•  Mapa con pines de los prestadores cercanos
•  Hoja inferior con tres prestadores: nombre, especialidad, distancia y tiempo
•  Acción de solicitar por fila
•  Barra de navegación inferior con Asistencia como destino activo

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Si no hay prestadores disponibles, la hoja lo dice en lugar de quedarse vacía

ACCESIBILIDAD
•  Cada fila es un objetivo táctil completo de al menos 48 dp
•  La distancia y el tiempo se leen como texto, no solo como posición en el mapa

ADAPTATIVO (Material 3)
•  La hoja inferior termina por encima de la NavigationBar: ninguna fila queda tapada
•  El mapa ocupa el alto restante sin fijar un valor en dp
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: G-03 · M06-04a · M06-04b · M06-06
Sale a: M06-02
- **Componentes reutilizados:** Fila de prestador · Barra de navegación inferior
- **Wireframe de referencia:** Adjuntar el wireframe M06-01 del archivo de baja fidelidad (Solventa-movil, nodo 10:184). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=10-184) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-2)
- **Frente al plano v21:** Existía en v21 · sin cambios

#### M06-02 · Formulario de solicitud (KAN-148)

**Historia de usuario (UX):** Como cliente que va a pedir asistencia, quiero decir qué me pasa y confirmar dónde estoy, para que llegue el prestador correcto al lugar correcto.

**Criterios de aceptación UI:**

INTERFAZ
•  Selector de tipo de asistencia
•  Campo de descripción breve, opcional
•  Ubicación detectada, editable
•  Botón «Enviar solicitud»

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Si falta el tipo de asistencia, el error aparece bajo ese campo y el foco se mueve allí
•  El envío confirma con la pantalla de progreso; nunca queda en silencio

ACCESIBILIDAD
•  Cada campo tiene etiqueta visible encima, no solo texto de marcador
•  La ubicación detectada se anuncia como valor actual y se puede corregir con teclado

ADAPTATIVO (Material 3)
•  Los campos ocupan el ancho con márgenes de 16 dp; el botón queda sobre el borde inferior seguro
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M06-01
Sale a: M06-03
- **Componentes reutilizados:** Botón (Primario) · Barra superior (Con volver)
- **Wireframe de referencia:** Adjuntar el wireframe M06-02 del archivo de baja fidelidad (Solventa-movil, nodo 19:136). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=19-136) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-140)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v18

#### M06-03 · Enviando solicitud (KAN-149)

**Historia de usuario (UX):** Como cliente que acaba de enviar la solicitud, quiero ver que está en curso, para no enviarla dos veces.

**Criterios de aceptación UI:**

INTERFAZ
•  Indicador de progreso indeterminado
•  Texto «confirmando disponibilidad»
•  Sin acciones: estado transitorio

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Auto-avance de 1,5 s al desenlace; el primer envío aterriza en el fallo, para que ese camino sea alcanzable

ACCESIBILIDAD
•  El estado de envío se anuncia una vez

ADAPTATIVO (Material 3)
•  Bloque centrado, independiente del alto del dispositivo
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M06-02
Sale a: M06-04b
- **Componentes reutilizados:** —
- **Wireframe de referencia:** Adjuntar el wireframe M06-03 del archivo de baja fidelidad (Solventa-movil, nodo 19:158). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=19-158) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-340)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v21

#### M06-03b · Reenviando solicitud (KAN-150)

**Historia de usuario (UX):** Como cliente que reintenta, quiero que se reenvíe lo que ya escribí, para no llenar el formulario otra vez.

**Criterios de aceptación UI:**

INTERFAZ
•  Gemela de M06-03 con texto de reenvío
•  Sin volver a pedir los datos de la solicitud

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Auto-avance de 1,5 s a la confirmación; el reintento tiene desenlace propio

ACCESIBILIDAD
•  Mismo anuncio único del estado de envío

ADAPTATIVO (Material 3)
•  Idéntico a M06-03
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M06-04b
Sale a: M06-04a
- **Componentes reutilizados:** —
- **Wireframe de referencia:** Adjuntar el wireframe M06-03b del archivo de baja fidelidad (Solventa-movil, nodo 19:158). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=19-158) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-671)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v21

#### M06-04a · Asistencia confirmada (KAN-151)

**Historia de usuario (UX):** Como cliente con la asistencia confirmada, quiero saber quién viene y en cuánto, para decidir si espero o cancelo.

**Criterios de aceptación UI:**

INTERFAZ
•  Prestador asignado con nombre y especialidad
•  Tiempo estimado de llegada
•  Acción de ver en el mapa
•  Acción de cancelar la asistencia, visualmente secundaria

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  Éxito con ícono, color y palabra
•  Cancelar no ejecuta de inmediato: pasa por confirmación

ACCESIBILIDAD
•  El tiempo estimado se lee como texto completo, no como abreviatura
•  La acción destructiva se distingue por forma y texto, no solo por color

ADAPTATIVO (Material 3)
•  Las dos acciones se apilan con separación de 12 dp
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M06-03b
Sale a: M06-01 · M06-05
- **Componentes reutilizados:** Botón (Tonal) · Botón (Destructivo)
- **Wireframe de referencia:** Adjuntar el wireframe M06-04a del archivo de baja fidelidad (Solventa-movil, nodo 19:169). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=19-169) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-371)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v18

#### M06-04b · No pudimos confirmar (KAN-156)

**Historia de usuario (UX):** Como cliente cuya solicitud no se pudo confirmar, quiero entender qué pasó y tener dos caminos, para no quedarme varado sin opciones.

**Criterios de aceptación UI:**

INTERFAZ
•  Ícono de fallo y mensaje de que no se pudo confirmar
•  Acción de elegir otro prestador
•  Acción de reintentar, que reenvía los mismos datos

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  El error explica la causa y ofrece ambas salidas
•  Reintentar no devuelve al formulario: reenvía lo ya escrito

ACCESIBILIDAD
•  El mensaje de fallo se anuncia al entrar
•  Ambas acciones son botones de 48 dp

ADAPTATIVO (Material 3)
•  Las dos acciones se apilan verticalmente con separación de 12 dp
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M06-03
Sale a: M06-01 · M06-03b
- **Componentes reutilizados:** Botón (Primario) · Botón (Tonal)
- **Wireframe de referencia:** Adjuntar el wireframe M06-04b del archivo de baja fidelidad (Solventa-movil, nodo 19:183). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=19-183) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-404)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v18

#### M06-05 · ¿Cancelar asistencia? (confirmación) (KAN-158)

**Historia de usuario (UX):** Como cliente que va a cancelar, quiero que me adviertan de la consecuencia antes de hacerlo, para no cancelar por error a quien ya viene en camino.

**Criterios de aceptación UI:**

INTERFAZ
•  Diálogo de confirmación con la consecuencia enunciada
•  Dos acciones diferenciadas: «Sí, cancelar» (destructiva) y «No, mantener»
•  La acción segura queda como opción por defecto

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  La advertencia dice qué se pierde, no solo pregunta si está seguro
•  Mismo criterio que la regeneración de la clave API en el portal web

ACCESIBILIDAD
•  El diálogo toma el foco al abrirse y lo devuelve al cerrarse
•  La acción destructiva lleva palabra, no solo color rojo

ADAPTATIVO (Material 3)
•  El diálogo conserva márgenes de 24 dp respecto a los bordes
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M06-04a
Sale a: M06-06 · M06-04a
- **Componentes reutilizados:** Botón (Destructivo) · Botón (Tonal)
- **Wireframe de referencia:** Adjuntar el wireframe M06-05 del archivo de baja fidelidad (Solventa-movil, nodo 19:238). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=19-238) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-437)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v18

#### M06-06 · Asistencia cancelada (KAN-159)

**Historia de usuario (UX):** Como cliente que canceló, quiero confirmación de que quedó cancelada y una forma de volver a pedirla, para retomar si cambio de idea.

**Criterios de aceptación UI:**

INTERFAZ
•  Mensaje de cancelación confirmada
•  Acción de volver al inicio
•  Acción de solicitar asistencia de nuevo

RETROALIMENTACIÓN — ÉXITO Y ERROR
•  La cancelación se confirma explícitamente; no se deduce del regreso al mapa

ACCESIBILIDAD
•  El mensaje se anuncia al entrar; las dos salidas son botones de 48 dp

ADAPTATIVO (Material 3)
•  Las acciones se apilan con separación de 12 dp
•  Adaptativo (no responsive) — un solo ancho de teléfono con márgenes de 16 dp, barra superior de 56 dp y barra de navegación de 80 dp.

- **Navegación:** Entra desde: M06-05
Sale a: G-03 · M06-01
- **Componentes reutilizados:** Botón (Primario) · Botón (Tonal)
- **Wireframe de referencia:** Adjuntar el wireframe M06-06 del archivo de baja fidelidad (Solventa-movil, nodo 19:252). · [baja fidelidad](https://www.figma.com/design/bGHlYWiJyg4mXIqnpcTAFd/Solventa-movil?node-id=19-252) · [prototipo final](https://www.figma.com/design/2ahgOpwsejNnxZl57v4o9D/Solventa-movil-Final?node-id=2005-529)
- **Frente al plano v21:** Existía en v21 · ajustada en la ronda v18

**Notas de la HU:** condicional según el Excel: se confirma o se retira al arrancar el sprint. Depende de la geolocalización y de un proveedor de mapas.

## 4. Datos para los gráficos de cierre

Seis gráficos, todos en la hoja «Gráficos» del libro y alimentados por fórmulas:

| Gráfico | Qué muestra | Datos |
|---|---|---|
| Burndown | Puntos restantes: línea ideal (recta de 41,8 a 0 en 21 días) contra la real | hoja «Burndown», columnas C y D |
| Burnup | Puntos completados contra el alcance total | «Burndown», columnas E y F |
| Velocidad | Puntos comprometidos contra completados en los sprints 1, 2 y 3 | «Velocidad» |
| Avance por HU | Puntos hechos y pendientes de cada HU | «Alcance HU», columnas N y O |
| Subtareas por estado | Cuántas subtareas hay por estado de Jira | «Avance» |
| Peso por HU | Cuánto aporta cada HU al sprint | «Alcance HU», columna M |

**Cómo se genera el cierre (≈5 minutos):**

1. `python3 exportar_jira_sprint3.py` lee Jira con `acli` (solo lectura) y escribe `jira_export.csv`: clave, estado, fecha de cierre, horas y responsable de cada subtarea.
2. Pegar las filas (sin encabezado) en la hoja «Jira (export)» desde A5.
3. En «Parámetros», la fecha de corte (celda amarilla): hoy durante el sprint, o el 22/11 para el cierre. Los días posteriores al corte muestran `#N/A` a propósito, para que la línea real no caiga a cero.
4. Abrir «Gráficos» y copiar cada gráfico como imagen para el informe.

**Qué verifica el libro por sí mismo:** `Alerta` marca una subtarea *Finalizada* sin fecha de cierre (no restaría del burndown) o fuera de la exportación; «Avance» compara el total de subtareas con «Alcance HU».

### Velocidad de referencia (Jira, 2026-10-31)

| Sprint | Fechas | Comprometido | Completado | Nota |
|---|---|---:|---:|---|
| KAN Sprint 1 | 5–19 oct | 26 | 26 | HU-W01 (20) y HU-W05 (6), finalizadas. Cerrado. |
| KAN Sprint 2 | 19 oct–2 nov | 45 | 26 | HU-W04 (13) y HU-M01 (13) hechas; HU-W03 (13) en curso y HU-W07 (6) con el backend cerrado pero las pantallas sin hacer. **Provisional hasta el cierre.** |
| KAN Sprint 3 | 2–22 nov | 41,8 | 0 | Compromiso calculado desde las subtareas. |

## 5. Trabajo técnico pendiente de HU-W03 (propuesta, sin peso)

Las subtareas de Jira de estas HU son pantallas. Lo siguiente es trabajo real de HU-W03 que **no existe como issue** y no suma a los pesos; se propone para decidir si se crea. Tamaño relativo S/M/L (no hay horas: Jira no tiene estimaciones de tiempo en estas HU).

| ID | Tarea | Tamaño | Estado al 2026-10-31 |
|---|---|:-:|---|
| F-F3 | Flujo del CLIENTE con sesión en el front (rutas, «Firmar», retorno de la pasarela con el id en la ruta) | M | Pendiente |
| F-F3b | Cédula de solo lectura en W01-01 (W03-34) | S | Pendiente |
| F-F4 | W03-06 «Mis pólizas» / «Pólizas emitidas» para los tres roles | L | Pendiente |
| F-F5 | Avisos en vivo (SSE) en «Mis pólizas» | M | Pendiente |
| F-F6 | Integración del front con el backend real (quitar mocks del modo normal, Cypress contra el compose) | M | Pendiente |
| F-F7 | Cierre documental del front | S | Pendiente |
| F-B10 | Integración de punta a punta, ASESOR y CLIENTE | M | Pendiente |
| OPS-1 | Integrar el release del front (#93; publica a producción) | S | PR abierto |
| OPS-2 | Reintegraciones main→develop y tags sprint-2 / sprint-3 | S | Pendiente |
| OPS-3 | Correo real con Resend (necesita un dominio verificado; la llave ya está cargada) | S | Bloqueado |
| OPS-4 | Medición en staging (Newman carpeta 6, EC005, EC007) | M | Pendiente |

## 6. Estado de refinamiento (¿están listas para comprometerse?)

**Respuesta corta: solo en parte.** No hay una «definición de listo» escrita en los repos; se evaluó con una lista mínima: (a) criterios de aceptación funcionales, (b) criterios de UI por pantalla, (c) estimación, (d) responsable, (e) plan técnico y dependencias, (f) diseño enlazado y (g) sin bloqueantes. Fuentes revisadas el 2026-10-31: Jira (descripciones, subtareas e historial), Confluence (sin páginas), los repos y el Excel de prototipado v24.

| HU | (a) Criterios funcionales | (b) Criterios UI | (c) Estimación | (d) Responsable | (e) Plan técnico y dependencias | Veredicto |
|---|---|---|---|---|---|---|
| HU-W03 · KAN-26 | **Sí**: CA-W03-01 a 09 del documento «Detalle de historias del Sprint 2» (ese documento no está en los repos ni en Confluence; `plan-completar-hu-w03.md` y los PR los citan) | Sí (6 pantallas, v24) | 13 pts; sin horas | f.taboada | **Sí**: plan de HU-W03 (F-B/F-F); el trabajo técnico restante no está en Jira (sección 5) | Refinada en lo funcional; falta reflejarla en Jira |
| HU-W07 · KAN-30 | **Parcial**: la descripción de Jira trae el flujo, pero el 21/10 se reemplazaron los criterios originales (p95 ≤ 250 ms, autoescalado, integración en ≤ 1 semana) por el flujo; el plan dice «criterios no cambian» con una propuesta opcional | Sí (3 pantallas) | 6 pts (bajó de 13 el 25/10); sin horas | Daniel | **Sí**: `plan-completar-hu-w07.md` v3; backend cerrado en local; pendientes: staging y el portal | Lista para el front; confirmar los criterios con Felipe |
| HU-M02 · KAN-32 | **Mínimos**: un solo criterio en Jira («inicia sesión validando la biometría… Keystore / Keychain») | Sí (5 pantallas) | 6 pts; sin horas | **Sin asignar** | **No**: no hay plan; la historia de seguridad relacionada (HA-SEG-005, KAN-115) está sin refinar | **No refinada** |
| HU-M04 · KAN-38 | **No**: solo narrativa; sin criterios de aceptación ni umbral paramétrico | Sí (3 pantallas) | 13 pts; sin horas | **Sin asignar** | **No**: depende de la ingesta de eventos paramétricos (HA-ESC-002, KAN-55), que no existe; sin plan | **No refinada** |
| HU-M06 · KAN-36 | **No**: solo narrativa | Sí (8 pantallas) | 6 pts; sin horas | **Sin asignar** | **No**: no hay plan ni backend de geolocalización de prestadores; ya es condicional | **No refinada** |

**Lo que sí está para todas:** los criterios de **UI** de las 24 subtareas (INTERFAZ, RETROALIMENTACIÓN, ACCESIBILIDAD, RESPONSIVE o ADAPTATIVO), la navegación, los componentes y los enlaces al wireframe y al prototipo final (sección 3). Pesos en puntos de historia y valor de negocio en las cinco HU.

**Brechas comunes que hay que cerrar antes de comprometer el sprint:**

1. **Las subtareas de Jira no tienen los criterios.** Su descripción es la línea corta de la versión anterior del Excel (v23) y no tienen adjuntos; los criterios de la v24 viven solo en el Excel y en este documento. Habría que pegarlos en cada subtarea (el Excel ya trae la plantilla).
2. **Criterios funcionales de M02, M04 y M06** (y los de W07 a confirmar): no existe ningún `CA-M*` ni `CA-W07` en los repos. Alguien con rol de producto tiene que escribirlos.
3. **Sin responsable** M02, M04 y M06.
4. **Sin horas:** Jira no tiene estimación de tiempo en estas HU; el Excel solo trae horas para 5 filas. Los gráficos se basan en puntos.
5. **Dependencias de backend sin plan** para M04 (eventos paramétricos) y M06 (prestadores y geolocalización).
6. **El Sprint 3 no existe en Jira** (el Sprint 2 cierra el 2/11).

> `docs/evidencias/release-burndown.md` (19/10) todavía lista a HU-W07 con 13 puntos y a HU-W03 con 6; en Jira hoy son 6 y 13 (cambio del 25/10). Este documento usa los de Jira.

## 7. Supuestos y límites

- Los pesos son los de Jira al 2026-10-31; si cambian, se actualizan en la hoja «Alcance HU».
- El reparto de puntos entre subtareas es igual por omisión; es una convención de este documento, no un dato de Jira.
- La línea ideal del burndown usa días calendario, sin descontar fines de semana ni festivos.
- La velocidad del Sprint 2 es provisional hasta su cierre.
- El libro no se abrió en Excel ni en LibreOffice al construirlo (no estaban disponibles en el equipo): las fórmulas se evaluaron con un evaluador independiente y con una simulación de cierre parcial, y los gráficos no se vieron renderizados. Se recalculan al abrirlo.

## 8. Archivos de apoyo

El libro de trabajo (`Solventa_Sprint3_Cierre.xlsx`), el script de exportación de Jira (`exportar_jira_sprint3.py`) y los que regeneran el libro y este
documento **no están versionados en este repo**: viven en la carpeta `sprint-3/` de quien armó la planeación (Frans, 2026-10-31). Este documento tiene todo lo
necesario para recrearlos: las fuentes (secciones 2 y 4), el cálculo de los pesos y los pasos del cierre. Si el equipo quiere versionarlos, el lugar natural es
`scripts/` (el exportador, que solo usa `acli`) y `docs/evidencias/` (el libro de cada cierre).
