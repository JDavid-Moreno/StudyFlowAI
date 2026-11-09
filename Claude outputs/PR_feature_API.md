# API de StudyFlow AI, mover un bloque, intercambiar sesiones y corrección de la persistencia

## Qué hace este PR

Agrega la **API (FastAPI)** que junta todas las capas y deja el backend listo para que el front lo consuma. Además corrige la capa de persistencia y amplía la capa de órdenes con dos acciones nuevas sobre el calendario.

## 1. Corrección de la capa de persistencia

La persistencia no guardaba todo lo que la capa de órdenes necesita para continuar entre peticiones.

- **Sesiones fijadas:** nueva columna `fijada` en `sesiones`. `guardar_plan` las recibe y rechaza (`ValueError`) una que no esté en el horario; `cargar_plan` las devuelve en `PlanGuardado.fijadas`, que es lo que recibe `PlanVigente.fijadas`.
- **Decisión pendiente:** nueva tabla `decision_pendiente` (ahora son 8 tablas). Guarda la `Decision` completa como JSON, una por estudiante, con vigencia de 30 minutos. Se lee sin borrarla, coexiste con la orden pendiente y la borra `guardar_plan` en su misma transacción.
- Notebook `StudyFlow_Persistencia.ipynb` reescrito y ejecutado, código mudado a `studyflow/persistencia.py`.
- Pruebas: 35 (antes menos), incluidas 874 planes aleatorios con sesiones fijadas al azar.

## 2. Capa de órdenes: mover un solo bloque e intercambiar

Un **bloque** es el conjunto de sesiones seguidas de la misma actividad en un mismo día, como las muestra el calendario.

- **Mover un solo bloque:** `mover_sesion` acepta `hora_origen`. Con ella se mueve solo el bloque que incluye esa hora; sin ella todo funciona como antes. El intérprete la toma de frases como *mueve la sesión del parcial del martes de las 6 de la tarde al jueves*.
- **Intercambiar bloques** (`intercambiar_sesiones`, solo desde el calendario; el LLM nunca la produce): cada bloque empieza donde empezaba el otro y conserva su duración. Si duran distinto, el más largo solo cabe si el tiempo de más está libre. Se rechaza si alguno ya empezó, si son de la misma actividad, si pasa la fecha límite, si se sale de la disponibilidad o del máximo diario, o si hay una decisión pendiente. Los dos bloques quedan fijados y nada más se mueve.
- `mover_sesion` ahora rechaza un destino que ya pasó y el mismo día sin hora.
- Pruebas: `test_ordenes.py` pasa a 44. Notebooks `StudyFlow_CapaOrdenes.ipynb` y `StudyFlow_CapaLLM.ipynb` actualizados (la capa del LLM re-ejecutada hasta el paso 4).

## 3. API (`studyflow/servicio.py` y `studyflow/api.py`)

| Ruta | Qué hace |
|---|---|
| `GET /salud` | Comprueba que la API está arriba |
| `POST /estudiantes` | Crea un estudiante anónimo |
| `GET /estado` | Plan, horario, bloques, fijadas y decisión pendiente |
| `POST /plan` | Crea el plan desde el formulario; pide confirmar el reemplazo si ya hay uno |
| `POST /mensaje` | Chat: interpreta la frase y devuelve confirmación, pregunta o consulta |
| `POST /confirmacion` | Acepta o rechaza la orden pendiente |
| `POST /sesiones/mover` | Mueve sesiones o un bloque (`hora_origen`) |
| `POST /sesiones/intercambiar` | Intercambia dos bloques |
| `POST /sesiones/no-hecha` | Marca una sesión como no hecha |

Decisiones de diseño:

- **Sin login:** identificador anónimo en el encabezado `X-Estudiante-Id` (404 si no existe).
- **El primer horario sale solo del formulario;** el chat modifica uno existente.
- **Chat se confirma, calendario se aplica de una vez** (el front ya pidió la confirmación).
- Si el primer plan no cabe no se guarda: queda una decisión pendiente.
- Reloj del servidor en `America/Bogota` (`STUDYFLOW_TZ`).
- Límites en memoria: 10 peticiones por minuto por estudiante, 20 estudiantes nuevos por hora por IP, 500 llamadas al LLM por día. Por eso la API corre con **un solo proceso**.
- Un cambio a la vez por estudiante (candado) y una conexión SQLite por petición. CORS solo para `STUDYFLOW_CORS`.
- Sin notebook para esta capa: el contrato queda en `/docs` y las pruebas hacen de ejemplo.

## Pruebas

- `uv run pytest` -> `309 passed, 30 deselected` (nuevas: 16 del servicio y 9 de las rutas; los 30 deseleccionados usan el LLM real).
- Verificado a mano con uvicorn y el MLP real: crear plan, reemplazo, mover bloque, intercambiar, 422 con hora fuera de la rejilla y 404 con identificador desconocido.

## Pendiente antes de mezclar

- [ ] Correr `uv run pytest -m llm_real -s` con la clave (incluye la frase nueva del bloque y `test_flujo_con_el_llm_real`).
- [ ] Re-ejecutar los pasos 5 y 6 de `StudyFlow_CapaLLM.ipynb` con la clave.
- [ ] Sacar `notebooks/.vscode/settings.json` del PR.

## Notas para quien revise

- Tras traer la rama hay que correr `uv sync` (nuevas dependencias: `fastapi`, `uvicorn`; de desarrollo, `httpx`).
- Riesgo conocido: en horarios muy largos el texto del redactor puede superar el tope de 2500 caracteres del validador de salida y caer a la plantilla. Se tratará aparte.
- `/estado` no expone la orden que espera confirmación; el front puede guardar su texto.
