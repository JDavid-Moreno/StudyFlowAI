# StudyFlow AI

StudyFlow AI es un generador adaptativo de horarios de estudio. Combina un modelo de lenguaje (LLM) que procesa las instrucciones del estudiante, con una red neuronal (MLP) que prioriza y distribuye el trabajo. Finalmente, una capa determinista asigna las sesiones respetando fechas límite y disponibilidad.

Proyecto del curso PTIA.

## Flujo del sistema

```
Mensaje del estudiante -> LLM intérprete -> Validador -> MLP -> Capa de asignación -> Validador de salida + LLM redactor -> Horario
```

El LLM aparece en dos puntos (entender el mensaje y contar el horario) y en ninguno decide el horario: lo calculan el MLP y la capa de asignación, que son deterministas una vez entrenada la red.

| Componente                  | Qué hace                                                                                                  | Estado                                      |
|-----------------------------|-----------------------------------------------------------------------------------------------------------|---------------------------------------------|
| LLM intérprete              | Convierte el mensaje del estudiante en una orden de un menú cerrado de 9 acciones                         | **Hecho** en notebook (probado con OpenAI)  |
| Validador                   | Revisa la forma de los datos (Pydantic) y que las horas quepan antes de cada fecha límite                 | **Hecho** en notebook                       |
| **MLP**                     | Una red, dos salidas: prioridad de cada actividad y reparto del trabajo en inicio, medio y final          | **Hecho**, entrenado y guardado en `modelos/` |
| Capa de asignación          | Coloca las sesiones en el calendario de forma determinista                                                | **Hecho** en notebook                       |
| Validador de salida + LLM redactor | Redacta el horario en lenguaje natural y comprueba que el texto no invente nada                    | **Hecho** en notebook (probado con OpenAI)  |
| Interfaz                    | Página web sin login (prototipo en Figma)                                                                 | Prototipo aparte; el front irá en TypeScript |

Cada capa se desarrolla y se prueba por completo en su propio notebook. Falta mudar el código a archivos `.py`, un notebook que pruebe el flujo completo, la persistencia (SQLite), una API (FastAPI) y el front.

## Las capas

### Validador (`StudyFlow_CapaValidacion.ipynb`)

Tres piezas, de la más simple a la más estricta:

1. **Forma de los datos (Pydantic):** nombres, niveles de prioridad y dificultad, fechas, horas estimadas (hasta 100), franjas horarias sin traslapes y duración de sesión (30 a 240 min, en saltos de 30).
2. **Capacidad efectiva:** cuántas horas reales de estudio ofrece cada día, contadas en bloques de 30 min y con un máximo diario opcional (hasta 8 h). También alimenta dos entradas del MLP.
3. **Coherencia:** revisa por fecha límite acumulada que las horas quepan (margen 0) y exige una ventana mínima de 3 días con disponibilidad. En actividades nuevas es un error; en una replanificación, una advertencia.

### Capa de asignación (`StudyFlow_CapaAsignacion.ipynb`)

Recibe el plan validado y las dos salidas del MLP y devuelve sesiones `(actividad, día, hora de inicio, hora de fin)` más un resumen por actividad.

- Atiende las actividades por prioridad y reparte las horas en tres tramos (inicio, medio y final del plazo), redondeadas a bloques de 30 min.
- Si un tramo no tiene espacio, el excedente pasa al tramo vecino más cercano.
- Dentro de un tramo el reparto es parejo entre los días y respeta lo ocupado por actividades de mayor prioridad.
- Lo que no cabe se asigna parcialmente y se reporta como faltante; nunca se fuerza.
- En una replanificación, lo cumplido o fijado cuenta como ocupado y solo se reparten las horas pendientes.
- Se prueba con casos escritos a mano y con 1732 planes aleatorios factibles, en los que ninguna sesión viola la disponibilidad ni la fecha límite.

### Capa del LLM (`StudyFlow_CapaLLM.ipynb`)

- **Intérprete:** convierte el mensaje en una de 9 acciones (`crear_plan`, `agregar_actividad`, `quitar_actividad`, `cambiar_fecha_limite`, `cambiar_horas`, `bloquear_dia`, `mover_sesion`, `consultar_horario`, `fuera_de_alcance`). El LLM responde con un esquema JSON estricto y el código lo vuelve a validar. Las fechas las calcula el código, no el LLM. Si falta información, el sistema pregunta en vez de adivinar, y toda orden que cambia algo se confirma antes de aplicarse.
- **Seguridad:** el mensaje viaja como dato dentro de `<mensaje_usuario>` (máximo 500 caracteres); el LLM no tiene herramientas ni memoria; lo que no es del horario (saludos, código, otros temas, intentos de engaño) termina en `fuera_de_alcance`. Al LLM solo viajan el mensaje, los nombres de las actividades y las fechas y horas del horario.
- **Redactor:** cuenta el horario ya calculado en español. Un validador de salida revisa que fechas, horas, duraciones y actividades coincidan con el horario real; si el texto inventa algo, se usa una plantilla de respaldo.
- **Proveedor:** OpenAI, modelo `gpt-6-luna`. El cliente (`llamar_llm`) reintenta ante fallas pasajeras y no reintenta si se acabó el saldo.
- **Pruebas:** con un LLM simulado (sin internet) se cubren las 9 acciones, los mensajes vacíos o malformados y las inyecciones. Con el LLM real, 15 de 15 frases interpretadas como se esperaba y los 2 textos del redactor aprobados por el validador de salida.

## La red neuronal (MLP)

Se entrena con un dataset sintético: 3000 escenarios de 3 a 8 actividades con plazos de 3 a 30 días, filtrados con el validador y etiquetados por un "profesor" heurístico no lineal. La división 70/15/15 se hace por escenario para que no haya filtración entre conjuntos.

Arquitectura (Keras, API funcional, 820 parámetros):

```
Entrada (6) → Dense 32 (ReLU) → Dense 16 (ReLU) -> Dense 1 (sigmoide) -> prioridad
                                                -> Dense 3 (softmax)  -> reparto
```

Pérdida: MSE para la prioridad + entropía cruzada para el reparto.

Resultados en el conjunto de prueba:

|                  | Spearman prioridad | R² prioridad | MAE reparto (inicio / medio / final) |
|------------------|--------------------|--------------|--------------------------------------|
| Regresión lineal | 0.968              | 0.954        | 0.0029 / 0.0006 / 0.0023             |
| **Red neuronal** | **0.981**          | **0.997**    | **0.0017 / 0.0009 / 0.0013**         |
| Objetivo         | ≥ 0.90             | n/a          | ≤ 0.05                               |

Los objetivos se cumplen con margen, pero la regresión lineal también los cumple. Lo que justifica la red es la prioridad: en las actividades más urgentes el error baja de 0.060 a 0.0075.

**Límite:** la red aprende la regla del profesor heurístico, no preferencias reales de estudiantes.

## Estructura

```
├── notebooks/
│   ├── StudyFlow_MLP.ipynb              Datos, decisiones, red y conclusiones
│   ├── StudyFlow_CapaValidacion.ipynb   Pydantic, capacidad efectiva y validador de coherencia
│   ├── StudyFlow_CapaAsignacion.ipynb   Algoritmo de asignación de sesiones
│   └── StudyFlow_CapaLLM.ipynb          Intérprete, redactor, validador de salida y pruebas con el LLM real
├── data/
│   ├── dataset_studyflow.csv            Dataset v1 (secciones 2.1 a 2.5 del notebook del MLP)
│   └── dataset_studyflow_v2.csv         Dataset v2, el final: con este se entrena la red
├── modelos/
│   ├── studyflow_mlp.keras              Red entrenada (la genera el paso 6 del notebook del MLP)
│   └── escalado.json                    Rangos de escalado de las entradas, deben usarse junto con la red
├── generar_dataset.py                   Generador de la v1
├── generar_dataset_v2.py                Generador de la v2
├── .env.example                         Plantilla de configuración del LLM (copiar como .env)
├── docs/                                Documentación
├── pyproject.toml / uv.lock             Dependencias
└── Proyecto PTIA.pdf                    Documento del proyecto
```

Cada notebook trae el código de su capa (listo para mudarse a `.py`), las decisiones que se tomaron y sus pruebas. Los notebooks de las capas son independientes entre sí: el código que necesitan de otra capa va copiado al inicio.

La v1 se conserva a propósito: muestra que con la primera regla la regresión lineal ya resolvía el problema, y por eso se creó la v2 (explicado en la sección 2.6 del notebook).

## Cómo ejecutarlo

Requiere [uv](https://docs.astral.sh/uv/) y Python 3.11 o superior.

```bash
uv sync
uv run jupyter lab
```

Abrir `notebooks/StudyFlow_MLP.ipynb` y ejecutar todas las celdas (Run All). Al final (paso 6) se guarda la red entrenada en `modelos/`; el resto del sistema solo la carga, nunca entrena. Se entrenan varias redes (comparación de pesos y semillas), así que la ejecución completa tarda unos minutos en CPU. Los datasets ya están incluidos en `data/`.

Los notebooks `StudyFlow_CapaValidacion`, `StudyFlow_CapaAsignacion` y `StudyFlow_CapaLLM` también se ejecutan con Run All y no necesitan entrenar nada. El de la capa del LLM funciona sin internet con un LLM simulado; sus pasos 5 y 6 usan el LLM real y se omiten solos si no hay clave.

### Configuración del LLM (opcional)

Solo hace falta para los pasos 5 y 6 del notebook de la capa del LLM.

```bash
cp .env.example .env    # y completar OPENAI_API_KEY
```

| Variable                   | Para qué sirve                                                                   |
|----------------------------|----------------------------------------------------------------------------------|
| `LLM_PROVIDER`             | Proveedor del LLM; por ahora solo `openai`                                       |
| `OPENAI_API_KEY`           | Clave de la API de OpenAI                                                        |
| `OPENAI_MODEL`             | Modelo; por defecto `gpt-6-luna`                                                 |
| `OPENAI_REASONING_EFFORT`  | Esfuerzo de razonamiento (por defecto `low`; vacío = no se envía)                |
| `OPENAI_MAX_TOKENS_SALIDA` | Tope de tokens de salida, incluye el razonamiento (por defecto 4000)             |
| `LLM_PAUSA_SEGUNDOS`       | Pausa entre llamadas en las pruebas reales (por defecto 0)                       |
| `LLM_TIMEOUT_SEGUNDOS`     | Plazo máximo por llamada (por defecto 90)                                        |

El archivo `.env` está en `.gitignore`: la clave nunca se sube. Las pruebas con el LLM real hacen 17 llamadas y gastan saldo de la cuenta, aunque muy poco.

Para regenerar los datasets (son deterministas, `SEED = 42`):

```bash
uv run python generar_dataset_v2.py   # escribe data/dataset_studyflow_v2.csv
uv run python generar_dataset.py      # escribe dataset_studyflow.csv en la carpeta actual; moverlo a data/
```

## Tecnologías

Python · Keras / TensorFlow · Pydantic · OpenAI API · NumPy · pandas · SciPy · Matplotlib · Jupyter · uv

Previstas para los siguientes pasos: FastAPI, SQLite y TypeScript (front).
