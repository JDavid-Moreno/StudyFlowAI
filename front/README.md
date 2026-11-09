# StudyFlow AI: front

Interfaz web de StudyFlow AI hecha con React, TypeScript y Vite. Es la versión final del diseño (`docs/prototipo/studyflow_prototipo_v2.html`).

**Estado actual: solo diseño.** Todavía no llama a la API: usa un cliente simulado (`src/api/simulada.ts`) que cumple el mismo contrato que tendrá el cliente real. Ese cliente arma un horario sencillo con lo que se ingresa en el formulario y responde al chat con unos casos fijos (bloquear un día, consultar un día, abrir tiempo o recortar en una decisión, fuera de alcance). En la fase de conexión se cambia una línea en `src/api/index.ts`.

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
    │   ├── simulada.ts           # cliente simulado (solo diseño)
    │   └── index.ts              # cliente en uso
    ├── components/
    │   ├── PasoDisponibilidad.tsx  # paso 1: rango de fechas, días y franjas
    │   ├── PasoActividades.tsx     # paso 2: actividades y botón de generar
    │   ├── PasoHorario.tsx         # paso 3: avisos, indicadores, calendario y menú de cada sesión
    │   ├── Asistente.tsx           # chat a la derecha y botón flotante
    │   └── Avisos.tsx              # avisos flotantes y ventana de confirmación
    └── utils/
        ├── fechas.ts             # fechas y horas
        ├── formulario.ts         # validación del formulario y conversión a PlanEntrada
        └── horario.ts            # huecos libres y reglas del calendario (la API valida de nuevo)
```

No se usan carpetas llamadas `lib`, `build` ni `dist` dentro de `src`, porque el `.gitignore` de la raíz las ignora.

## Correspondencia con la API

| Acción en la interfaz | Ruta |
|---|---|
| Generar mi horario | `POST /plan` (si responde `confirmar_reemplazo`, se pide confirmación y se reenvía con `reemplazar: true`) |
| Mensaje en el chat | `POST /mensaje` |
| Aplicar cambios o Descartar | `POST /confirmacion` |
| Arrastrar a otro día o mover desde el menú | `POST /sesiones/mover` (con `hora_origen`; sin `hora_inicio` al arrastrar) |
| Soltar una sesión encima de otra | `POST /sesiones/intercambiar` |
| Botón "✕ No" o "No la hice" | `POST /sesiones/no-hecha` |
| Opciones de una decisión | `POST /mensaje` con el texto de la opción |

Los errores 404, 422 y 429 ya tienen su manejo en `App.tsx`.

## Pendiente para la fase de conexión

- Cliente real (`src/api/real.ts`) con `fetch`, el encabezado `X-Estudiante-Id` y los errores como `ErrorApi`.
- Crear y guardar el identificador en el navegador (`POST /estudiantes`) y cargar `GET /estado` al abrir la página.
- Guardar en el navegador el texto de la confirmación pendiente, porque `/estado` no la expone.
- Leer la URL de la API desde una variable de entorno de Vite (`VITE_API_URL`).
