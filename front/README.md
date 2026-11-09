# StudyFlow AI: front

Interfaz web de StudyFlow AI hecha con React, TypeScript y Vite. Es la versión final del diseño (`docs/prototipo/studyflow_prototipo_v2.html`).

**Estado actual: conectado a la API.** Por defecto habla con FastAPI en `http://localhost:8000` (`src/api/real.ts`). Con `VITE_API_MODO=simulada` usa el cliente simulado (`src/api/simulada.ts`), útil para trabajar solo el diseño sin levantar el back.

## Requisitos

- Node.js 18 o superior y npm.

## Comandos

```bash
cd front
npm install
npm run dev        # servidor de desarrollo en http://localhost:5173
npm run build      # revisa los tipos y genera dist/
npm run preview    # sirve el build
```

El puerto 5173 es el que la API acepta por defecto en `STUDYFLOW_CORS`.

## Cómo correrlo con la API

En una terminal, desde la raíz del repo (el primer arranque tarda por TensorFlow):

```bash
uv run uvicorn studyflow.api:app --reload
```

En otra, desde `front/`:

```bash
npm install
npm run dev
```

Abre `http://localhost:5173`. Para cambiar la dirección de la API o usar el modo simulado, copia `.env.example` como `.env.local`.

## Estructura

```
front/
├── index.html
├── public/logo.png               # ícono de la pestaña
└── src/
    ├── main.tsx                  # punto de entrada
    ├── App.tsx                   # estado de la página y llamadas a la API
    ├── styles.css                # estilos del prototipo
    ├── assets/logo.png           # logo del proyecto
    ├── api/
    │   ├── tipos.ts              # tipos que reflejan los modelos de la API y el contrato ClienteApi
    │   ├── real.ts               # cliente real: fetch, X-Estudiante-Id y errores 404, 422 y 429
    │   ├── simulada.ts           # cliente simulado (solo diseño)
    │   └── index.ts              # elige el cliente según VITE_API_MODO
    ├── components/
    │   ├── PasoDisponibilidad.tsx  # paso 1: rango de fechas, días y franjas
    │   ├── PasoActividades.tsx     # paso 2: actividades y botón de generar
    │   ├── PasoHorario.tsx         # paso 3: avisos, indicadores, calendario y menú de cada sesión
    │   ├── Asistente.tsx           # chat a la derecha y botón flotante
    │   └── Avisos.tsx              # avisos flotantes y ventana de confirmación
    └── utils/
        ├── fechas.ts             # fechas y horas
        ├── formulario.ts         # validación del formulario y conversión desde y hacia PlanEntrada
        ├── pendiente.ts          # confirmación pendiente guardada en el navegador
        └── horario.ts            # huecos libres y reglas del calendario (la API valida de nuevo)
```

No se usan carpetas llamadas `lib`, `build` ni `dist` dentro de `src`, porque el `.gitignore` de la raíz las ignora.

## Correspondencia con la API

| Acción en la interfaz | Ruta |
|---|---|
| Al abrir la página (si hay identificador guardado) | `GET /estado` |
| Generar mi horario | `POST /estudiantes` (solo la primera vez) y `POST /plan`; si responde `confirmar_reemplazo` se pide confirmación y se reenvía con `reemplazar: true` |
| Mensaje en el chat u opción de una decisión | `POST /mensaje` |
| Aplicar cambios o Descartar | `POST /confirmacion` |
| Arrastrar a otro día o mover desde el menú | `POST /sesiones/mover` (con `hora_origen`; sin `hora_inicio` al arrastrar) |
| Soltar una sesión encima de otra | `POST /sesiones/intercambiar` |
| Botón "✕ No" o "No la hice" | `POST /sesiones/no-hecha`, una llamada por cada sesión del bloque que ya empezó |

## Cosas que la API define y la interfaz respeta

- **Identificador:** se crea la primera vez que se genera un plan y se guarda en `localStorage`. Un 404 lo borra y muestra un aviso.
- **Horas:** la API devuelve `HH:MM:SS`; `real.ts` las recorta a `HH:MM` al recibirlas.
- **No la hice:** la API solo lo acepta para sesiones que ya empezaron, así que el botón solo aparece en esas.
- **Primer plan que no cabe:** `/estado` no trae plan, solo la decisión. El asistente se abre con las opciones y el horario aparece al resolverla.
- **Confirmación pendiente:** `/estado` no la expone; el texto se guarda en el navegador por 30 minutos y se recupera al recargar.
- **`texto_horario`:** el horario narrado por el redactor se muestra en el chat al consultar, crear el plan o aplicar un cambio del chat. Se apaga con `MOSTRAR_TEXTO_HORARIO` en `App.tsx`.
- **Límites:** 10 cambios por minuto y por estudiante (429) y 20 identificadores nuevos por IP y hora.
