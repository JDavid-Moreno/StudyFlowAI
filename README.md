# StudyFlow AI

StudyFlow AI es un generador adaptativo de horarios de estudio. Combina un modelo de lenguaje (LLM) que procesa las instrucciones del estudiante, con una red neuronal (MLP) que prioriza y distribuye el trabajo. Finalmente, una capa determinista asigna las sesiones respetando fechas límite y disponibilidad.

Proyecto del curso PTIA.

## Flujo del sistema

```
Mensaje del estudiante -> LLM intérprete -> Confirmación -> Capa de órdenes -> Validador -> MLP -> Capa de asignación -> Validador de salida + LLM redactor -> Horario
```

El LLM aparece en dos puntos (entender el mensaje y contar el horario) y en ninguno decide el horario: lo calculan el MLP y la capa de asignación, que son deterministas una vez entrenada la red. La capa de órdenes también es determinista: aplica la orden confirmada al plan vigente del estudiante y hace pasar por el validador, el MLP y la asignación solo lo que falta por planificar.

| Componente                  | Qué hace                                                                                                  | Estado                                      |
|-----------------------------|-----------------------------------------------------------------------------------------------------------|---------------------------------------------|
| LLM intérprete              | Convierte el mensaje del estudiante en una orden de un menú cerrado de 13 acciones                        | **Hecho** (`studyflow/`, probado con OpenAI) |
| Capa de órdenes             | Aplica cada orden confirmada al plan vigente y recalcula solo lo pendiente; si no cabe, abre una decisión | **Hecho** (`studyflow/ordenes.py`)            |
| Validador                   | Revisa la forma de los datos (Pydantic) y que las horas quepan antes de cada fecha límite                 | **Hecho** (`studyflow/`)                    |
| **MLP**                     | Una red, dos salidas: prioridad de cada actividad y reparto del trabajo en inicio, medio y final          | **Hecho**, entrenado en `modelos/`; se usa desde `studyflow/mlp.py` |
| Capa de asignación          | Coloca las sesiones en el calendario de forma determinista                                                | **Hecho** (`studyflow/`)                    |
| Validador de salida + LLM redactor | Redacta el horario en lenguaje natural y comprueba que el texto no invente nada                    | **Hecho** (`studyflow/`, probado con OpenAI) |
| Persistencia (SQLite)       | Guarda por estudiante su plan, el horario vigente (con las sesiones que fijó), la orden que espera confirmación y la decisión pendiente; es una capa aparte del flujo | **Hecho** (`studyflow/persistencia.py`) |
| Interfaz                    | Página web sin login (prototipo en Figma)                                                                 | Prototipo aparte; el front irá en TypeScript |

Cada capa se desarrolló y se probó por completo en su propio notebook, y su código ya está mudado al paquete `studyflow/` con sus pruebas en `tests/` (incluidas las del flujo completo, de punta a punta). Falta una API (FastAPI) y el front.

Los notebooks se conservan a propósito: ahí está la explicación de cada decisión y sirven para experimentar. El código de `studyflow/` es la misma lógica con documentación mínima, así que el código está duplicado entre notebook y `.py`.

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

- **Intérprete:** convierte el mensaje en una de 13 acciones: `crear_plan`, `agregar_actividad`, `quitar_actividad`, `cambiar_fecha_limite`, `cambiar_horas`, `cambiar_nivel` (prioridad y/o dificultad), `bloquear_dia` (uno o varios días, completos o un rango de horas), `abrir_tiempo` (agregar disponibilidad, siempre con hora de inicio y de fin), `mover_sesion`, `cambiar_limites` (máximo de horas por día y duración de sesión), `responder_decision`, `consultar_horario` y `fuera_de_alcance`. En `mover_sesion`, si el estudiante nombra una actividad se mueve solo esa, y sin actividad se mueven todas las sesiones de ese día. El LLM responde con un esquema JSON estricto y el código lo vuelve a validar. Las fechas las calcula el código, no el LLM. Si falta información, el sistema pregunta en vez de adivinar (por ejemplo, *ya puedo el jueves* sin horas pregunta de qué hora a qué hora), y toda orden que cambia algo se confirma antes de aplicarse.
- **Varios días:** en `bloquear_dia` y `abrir_tiempo`, `fecha` es el primer día y `fecha_fin` el último, hasta un máximo de 30 días. El sistema no recuerda cómo era la disponibilidad antes de un bloqueo, por eso abrir tiempo exige siempre las horas.
- **Decisión pendiente:** cuando lo pedido ya no cabe en el tiempo disponible, el estudiante puede abrir más tiempo, recortar (manteniendo intactas las actividades que elija) o cancelar. Las opciones ofrecidas llegan al intérprete en el contexto, puestas por el código, y `responder_decision` solo se acepta si hay una decisión pendiente y la opción es una de ellas. `recortar` se confirma; `abrir_tiempo` y `cancelar` no cambian nada por sí solas, así que no piden confirmación.
- **Seguridad:** el mensaje viaja como dato dentro de `<mensaje_usuario>` (máximo 500 caracteres); el LLM no tiene herramientas ni memoria; lo que no es del horario (saludos, código, otros temas, intentos de engaño) termina en `fuera_de_alcance`, igual que un comentario sobre su situación que no pide ningún cambio. El motivo de un bloqueo (enfermedad, viaje, trabajo) no se copia a ningún campo ni se guarda. Al LLM solo viajan el mensaje, los nombres de las actividades y las fechas y horas del horario.
- **Redactor:** cuenta el horario ya calculado en español. Un validador de salida revisa que fechas, horas, duraciones y actividades coincidan con el horario real; si el texto inventa algo, se usa una plantilla de respaldo.
- **Proveedor:** OpenAI, modelo `gpt-6-luna`. El cliente (`llamar_llm`) reintenta ante fallas pasajeras y no reintenta si se acabó el saldo.
- **Pruebas:** con un LLM simulado (sin internet) se cubren las 13 acciones con 34 casos (rangos de varios días, abrir tiempo, límites, niveles y decisiones pendientes), los mensajes vacíos o malformados y las inyecciones. Con el LLM real, 24 de 24 frases interpretadas como se esperaba, sin que ninguna frase ambigua o boba produjera una orden, y los 2 textos del redactor aprobados por el validador de salida.

### Persistencia (`StudyFlow_Persistencia.ipynb`)

Es una capa **aparte del flujo**: ninguna capa la importa. La API lee de la base antes de llamar al flujo y guarda el resultado después. Usa `sqlite3` de la librería estándar, sin ORM y sin dependencias nuevas.

- **Qué guarda:** el estudiante (con un identificador anónimo, porque la página no tiene login), su plan vigente (actividades, franjas de disponibilidad, duración de sesión, máximo diario), el horario vigente con la prioridad del MLP y las sesiones que el estudiante fijó, la orden que espera confirmación y la decisión pendiente. No guarda el texto del chat.
- **Ocho tablas:** `estudiantes`, `planes`, `actividades`, `franjas`, `sesiones`, `resumen`, `orden_pendiente` y `decision_pendiente`. Con `ON DELETE CASCADE`, al borrar un plan se borra todo lo suyo.
- **Guardar es reemplazar, en una sola transacción:** se guarda todo o nada. Si un cambio deja el plan inviable no se guarda y el estudiante conserva el que tenía. Crear un plan nuevo con el mismo identificador reemplaza al anterior.
- **Sesiones fijadas:** son sesiones del horario con una marca (`fijada` en `sesiones`), no una tabla aparte. `guardar_plan` las recibe y rechaza (`ValueError`) una que no esté en el horario; `cargar_plan` las devuelve en `PlanGuardado.fijadas`, que es lo que la capa de órdenes recibe en `PlanVigente.fijadas`. Las sesiones pasadas no necesitan marca: la capa de órdenes las toma como hechas por su fecha y hora.
- **Al cargar, todo vuelve a pasar por Pydantic**, y los `CHECK` del esquema son una segunda barrera para los valores básicos.
- **Orden pendiente:** una por estudiante (la nueva reemplaza a la anterior) y vence a los 30 minutos, para no aplicar fechas que el intérprete resolvió otro día.
- **Decisión pendiente:** cuando un cambio no cabe, la `Decision` de la capa de órdenes (plan propuesto con su horario y fijadas, opciones, horas que faltan y fecha crítica) se guarda completa como JSON. Es una por estudiante y vence a los 30 minutos, como la orden. A diferencia de la orden, **se lee sin borrarla** (el estudiante puede escribir otras cosas antes de resolverla), **coexiste con la orden pendiente** (responder a una decisión es una orden que también se confirma) y la borra `guardar_plan` en su misma transacción, porque hablaba del plan que había.
- **Pruebas:** 35, con una base en memoria. Cubren el esquema, guardar y cargar, el reemplazo, que un fallo a mitad de camino conserve el plan anterior, que los estudiantes no se mezclen, las sesiones fijadas, las órdenes y las decisiones pendientes, y 874 planes aleatorios (con fijadas al azar) que se guardan y se recuperan sin diferencias.
- Las bases `*.db` están en el `.gitignore`: contienen datos de estudiantes y no deben subirse al repositorio.

### Capa de órdenes (`StudyFlow_CapaOrdenes.ipynb`)

El intérprete convierte el mensaje en una orden, pero una orden por sí sola no cambia nada. Esta capa la **aplica**: recibe el plan vigente del estudiante (plan, horario completo y sesiones fijadas) y una orden ya confirmada, y devuelve el plan nuevo. No usa el LLM ni la base de datos: la API guarda el resultado (persistencia) y lo cuenta (redactor). Al ser determinista, el mismo plan y la misma orden dan siempre el mismo resultado.

- **Funciones públicas:** `crear_plan` (el primer horario, con el formulario completo), `aplicar` (toda orden confirmada), `marcar_no_hecha` (el botón *no la hice*, que no pasa por el LLM), y `proponer_recorte` y `texto_recorte` (para mostrar el recorte exacto antes de confirmarlo).
- **Reglas comunes:**
  - Lo que ya empezó es historial: no se toca y cuenta como hecho.
  - Todo o nada: si algo no se puede, el plan vigente queda igual.
  - Lo que el estudiante mueve queda fijado y ninguna orden posterior lo deshace; solo se libera, con un aviso, si deja de caber (un bloqueo, una fecha límite nueva, un límite más bajo o una actividad quitada).
  - Una sesión pasada marcada como no hecha sale del historial y sus horas vuelven a lo pendiente.
  - Hoy solo se planifica desde la hora actual, redondeada al siguiente bloque de 30 min.
- **Qué recalcula:** solo las horas pendientes (total − hecho − fijado). Al validador y al MLP les llega la capacidad sin el tiempo ya ocupado, y la asignación respeta lo fijado.
- **Cada orden** (`consultar_horario` solo devuelve la vista pedida y `responder_decision` resuelve la decisión pendiente; `fuera_de_alcance` no llega a esta capa):
  - `cambiar_horas`, `cambiar_fecha_limite` y `cambiar_nivel` cambian la actividad y replanifican.
  - `agregar_actividad` replanifica todo lo no fijado.
  - `quitar_actividad` quita la actividad y sus sesiones, **sin mover las demás**.
  - `bloquear_dia` y `abrir_tiempo` quitan o suman disponibilidad en uno o varios días y replanifican.
  - `cambiar_limites` cambia el máximo diario y/o la duración de sesión.
  - `mover_sesion` fija las sesiones del día de origen en el destino, a la hora pedida o en el primer hueco, y replanifica el resto; el día de origen queda abierto para todas las actividades.
  - Se rechaza, con un mensaje claro, lo que no tiene sentido (un nombre repetido, bloquear donde no había tiempo, mover a un día sin espacio o a una fecha posterior a la entrega) y entonces el plan queda igual.
- **Reintento antes de declarar que no alcanza:** la asignación atiende las actividades en el orden de prioridad del MLP, y una prioritaria puede ocupar los primeros días y dejar sin espacio a otra que vence antes, aunque el validador diga que todo cabe. Antes de molestar al estudiante, la capa adelanta la actividad incompleta y reintenta; como último recurso ordena por fecha límite. En 283 planes aleatorios que el validador acepta, el orden del MLP solo dejaba algo incompleto en 5 (cerca del 2 %), y el reintento resolvió los 5. El resumen sigue mostrando la prioridad que dio el MLP.
- **Decisión pendiente:** si lo pedido no cabe, la capa no fuerza nada y devuelve una `Decision` con el plan propuesto, cuánto falta y antes de qué fecha.
  - Un cambio que el estudiante pidió (horas, fecha, actividad nueva, límites, mover) se puede **cancelar**. Un bloqueo o una sesión no hecha ya ocurrieron, así que solo se ofrece abrir tiempo o recortar.
  - **Abrir tiempo:** la capa pregunta qué días y horas agregar. Mientras la decisión esté pendiente, toda orden se aplica sobre el plan propuesto, de modo que *el domingo puedo de 9 a 13* se suma al bloqueo.
  - **Recortar:** quita horas, en bloques de 30 min, a las actividades **no protegidas** que vencen hasta la fecha crítica, empezando por la de menor prioridad del MLP. Antes de confirmar, la API muestra el recorte exacto.
  - La decisión vence a los 30 minutos, igual que la orden pendiente, y la API la guarda en la tabla `decision_pendiente`.
- **Qué devuelve:** un `ResultadoOrden` con su tipo (`aplicada`, `decision`, `pregunta`, `cancelada`, `rechazada` o `consulta`), el mensaje, el plan nuevo, el horario y los avisos. Todos los textos los escribe el código, nunca el LLM.
- **Pruebas:** 40 en `tests/test_ordenes.py`, todas con el MLP real (39 salen de los pasos del notebook y una comprueba que el reintento resuelve siempre lo que deja incompleto el orden del MLP). Cubren las utilidades, la creación del plan, cada orden, la decisión pendiente y 60 secuencias aleatorias de 8 órdenes con el reloj avanzando (464 órdenes: 338 aplicadas, 115 rechazadas, 10 decisiones y 1 cancelación). Después de cada una se comprueba que el plan cumple todas las reglas y que el historial no se perdió, y que un rechazo no cambia nada. El notebook incluye un ejemplo completo paso a paso: bloquear de martes a viernes, no alcanzar, recortar protegiendo el parcial y mover sus sesiones al domingo.

## La red neuronal (MLP)

Se entrena con un dataset sintético: 3000 escenarios de 3 a 8 actividades con plazos de 3 a 30 días, filtrados con el validador y etiquetados por un "profesor" heurístico no lineal. La división 70/15/15 se hace por escenario para que no haya filtración entre conjuntos.

Arquitectura (Keras, API funcional, 820 parámetros):

```
Entrada (6) -> Dense 32 (ReLU) -> Dense 16 (ReLU) -> Dense 1 (sigmoide) -> prioridad
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

**Uso desde el código (`studyflow/mlp.py`):** `PredictorMLP` carga la red y `escalado.json` y, dado un plan, calcula las 6 entradas de cada actividad con la misma capacidad efectiva del validador (solo cuentan los días con capacidad). Cada valor se recorta al rango con el que se entrenó (3 a 30 días, 1 a 20 h, 6 a 240 h de ventana), así que un plazo más largo se trata como de 30 días. Devuelve una `Prediccion` por actividad para la capa de asignación.

## Estructura

```
├── studyflow/                           Código del sistema (documentación mínima: la explicación está en los notebooks)
│   ├── models.py                        Modelos Pydantic de entrada
│   ├── disponibilidad.py                Capacidad efectiva por día
│   ├── validador.py                     Validador de coherencia (R1 a R7)
│   ├── asignacion.py                    Modelos y algoritmo de asignación
│   ├── llm.py                           Cliente de OpenAI y utilidades de texto
│   ├── interprete.py                    Intérprete de órdenes
│   ├── redactor.py                      Redactor y validador de salida
│   ├── ordenes.py                       Aplicación de cada orden al plan vigente y decisión pendiente
│   ├── persistencia.py                  Base SQLite: estudiantes, plan, horario con fijadas, orden y decisión pendientes
│   └── mlp.py                           Entradas del MLP y predicción con la red guardada
├── tests/                               Pruebas con pytest (ver la sección Pruebas)
├── notebooks/
│   ├── StudyFlow_MLP.ipynb              Datos, decisiones, red y conclusiones
│   ├── StudyFlow_CapaValidacion.ipynb   Pydantic, capacidad efectiva y validador de coherencia
│   ├── StudyFlow_CapaAsignacion.ipynb   Algoritmo de asignación de sesiones
│   ├── StudyFlow_CapaLLM.ipynb          Intérprete, redactor, validador de salida y pruebas con el LLM real
│   ├── StudyFlow_Persistencia.ipynb     Esquema SQLite, guardar y cargar el plan y las fijadas, orden y decisión pendientes
│   └── StudyFlow_CapaOrdenes.ipynb      Aplicación de cada orden, decisión pendiente, ejemplo completo y pruebas
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

Cada notebook trae el código de su capa, las decisiones que se tomaron y sus pruebas. Los de validación, asignación y LLM son independientes entre sí (el código que necesitan de otra capa va copiado al inicio) y también de `studyflow/`: no lo importan. Los de persistencia y órdenes sí importan el paquete `studyflow/`, porque usan varias capas a la vez.

La v1 se conserva a propósito: muestra que con la primera regla la regresión lineal ya resolvía el problema, y por eso se creó la v2 (explicado en la sección 2.6 del notebook).

## Cómo ejecutarlo

Requiere [uv](https://docs.astral.sh/uv/) y Python 3.11 o superior.

```bash
uv sync
uv run jupyter lab
```

Abrir `notebooks/StudyFlow_MLP.ipynb` y ejecutar todas las celdas (Run All). Al final (paso 6) se guarda la red entrenada en `modelos/`; el resto del sistema solo la carga, nunca entrena. Se entrenan varias redes (comparación de pesos y semillas), así que la ejecución completa tarda unos minutos en CPU. Los datasets ya están incluidos en `data/`.

Los notebooks `StudyFlow_CapaValidacion`, `StudyFlow_CapaAsignacion`, `StudyFlow_CapaLLM`, `StudyFlow_Persistencia` y `StudyFlow_CapaOrdenes` también se ejecutan con Run All y no necesitan entrenar nada. Los de persistencia y órdenes importan el paquete `studyflow/`, así que necesitan el entorno del proyecto (`uv sync`) como kernel; el de órdenes, además, carga la red entrenada de `modelos/` y no usa el LLM ni internet. El de la capa del LLM funciona sin internet con un LLM simulado; sus pasos 5 y 6 usan el LLM real (26 llamadas) y se omiten solos si no hay clave.

Las pruebas del código en `studyflow/` se ejecutan con pytest (siguiente sección).

### Configuración del LLM (opcional)

Solo hace falta para los pasos 5 y 6 del notebook de la capa del LLM y para las pruebas con el LLM real (ver la sección Pruebas).

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

El archivo `.env` está en `.gitignore`: la clave nunca se sube. Debe estar en la raíz del proyecto y los comandos se ejecutan desde esa carpeta, porque ahí lo busca el código.

Para regenerar los datasets (son deterministas, `SEED = 42`):

```bash
uv run python generar_dataset_v2.py   # escribe data/dataset_studyflow_v2.csv
uv run python generar_dataset.py      # escribe dataset_studyflow.csv en la carpeta actual; moverlo a data/
```

## Pruebas

Se ejecutan desde la raíz del proyecto, después de `uv sync` (instala el paquete `studyflow` y pytest):

```bash
uv run pytest                              # las pruebas normales: no usan internet ni gastan saldo
uv run pytest -v                           # lo mismo, una línea por prueba
uv run pytest tests/test_flujo.py -v       # solo el flujo completo
```

El resultado esperado es `278 passed, 28 deselected`. Los 28 `deselected` no son un error: son las pruebas con el **LLM real**, que se apartan por defecto porque necesitan la clave de OpenAI, gastan saldo y el LLM no siempre responde igual.

| Archivo                     | Qué prueba                                                                                           |
|-----------------------------|------------------------------------------------------------------------------------------------------|
| `test_models.py`            | Forma de los datos (Pydantic)                                                                        |
| `test_disponibilidad.py`    | Capacidad efectiva por día                                                                           |
| `test_validador.py`         | Las reglas de coherencia, con 20 casos                                                               |
| `test_asignacion.py`        | Funciones auxiliares, 17 casos y 1732 planes aleatorios que no violan ninguna restricción            |
| `test_llm.py`, `test_interprete.py`, `test_redactor.py` | Cliente, intérprete (34 casos que cubren las 13 acciones, rangos de varios días, decisiones pendientes y puertas de seguridad) y redactor, todo con un LLM simulado |
| `test_mlp.py`               | Entradas del MLP calculadas a mano y predicciones con la red guardada                                |
| `test_persistencia.py`      | Esquema, guardar y cargar el plan, reemplazo, sesiones fijadas, transacción que se deshace si algo falla, órdenes y decisiones pendientes y 874 planes aleatorios (con fijadas) que se guardan y recuperan sin diferencias |
| `test_ordenes.py`           | Capa de órdenes con el MLP real: utilidades, crear el plan, cada orden, decisión pendiente (recortar, abrir tiempo, cancelar), el reintento de la asignación y 60 secuencias aleatorias de 8 órdenes que no violan ninguna regla ni pierden historial |
| `test_flujo.py`             | **Flujo completo**: mensaje -> intérprete -> validador -> MLP -> asignación -> redactor -> JSON para el front. Crear un plan, mover una sesión y replanificar con el MLP (ver abajo), planes inviables que deben detenerse en el validador y 150 planes aleatorios |

### El flujo completo, paso a paso (`test_flujo.py`)

Estas pruebas recorren el sistema como lo hará la API: entra un mensaje del estudiante y sale el JSON que recibirá el front. Se hacen con datos normales (tres actividades, disponibilidad de 18:00 a 21:00 entre semana, sábado de 9:00 a 12:00 y de 14:00 a 17:00, y domingo libre). El LLM va simulado, así que las pruebas son rápidas y siempre dan el mismo resultado. Cubren dos escenarios principales:

**1. Crear el plan.** `tengo parcial de IA el lunes, 8 horas...` pasa por el intérprete, el estudiante confirma, y el plan viaja por validador, MLP, asignación y redactor. Se comprueba que todas las actividades quedan asignadas antes de su fecha límite, que el MLP ordena por prioridad y que el texto final no inventa nada (si el LLM inventa, se usa la plantilla). También se prueban los casos que **no** deben avanzar: un plan inviable o con un plazo menor a 3 días se detiene en el validador y nunca llega al MLP ni al redactor.

**2. Mover una sesión y replanificar** (`test_mover_una_sesion_y_replanificar_con_el_mlp`). Es la prueba de que el estudiante puede corregir el horario con una frase y que el sistema lo recalcula:

| Paso | Qué ocurre | Quién lo hace |
|------|------------|---------------|
| 1 | Se crea el plan original (escenario 1) | validador -> MLP -> asignación |
| 2 | El estudiante escribe: *no me gustó la sesión del Parcial de IA del 2 de noviembre, pásala al sábado a las 9 de la mañana* | intérprete (LLM) |
| 3 | El LLM no calcula nada: solo devuelve la orden `mover_sesion` con actividad, día de origen, día de destino y hora. El código resuelve las fechas y pide confirmación (*Entendí: mover las sesiones de Parcial de IA del lunes 2 de noviembre al sábado 7 de noviembre, empezando a las 09:00. ¿Confirmas?*) | intérprete (código) |
| 4 | Las sesiones de ese día se fijan en el destino, a partir de la hora pedida, y cuentan como horas ya ocupadas | capa de órdenes (`aplicar`) |
| 5 | Las horas que quedan de cada actividad vuelven a pasar por el validador, **se le consulta otra vez al MLP** y se reasignan alrededor de lo fijado | capa de órdenes: validador -> MLP -> asignación |
| 6 | Se une lo fijado con lo replanificado en el horario completo y el redactor lo cuenta | capa de órdenes y redactor |

Al final la prueba comprueba que:

- La orden se interpretó bien: actividad, día de origen, día de destino y hora, y que pide confirmación antes de aplicarse.
- La sesión movida quedó en el día y a la hora pedidos.
- El horario completo cumple todas las restricciones: dentro de la disponibilidad, antes de la fecha límite, sin traslapes, con la duración de sesión permitida y con el resumen por actividad coherente con las sesiones.
- Cada actividad conserva sus horas: lo que se movió más lo replanificado suma lo mismo que antes.
- El MLP se consultó **dos veces** (plan original y replanificación), con un contador que envuelve al predictor.
- El texto del redactor es fiel al horario final.

Qué tener presente:

- Los pasos 4 a 6 los hace la capa de órdenes (`aplicar`, en `studyflow/ordenes.py`); la prueba solo le entrega el plan vigente y la orden, y comprueba el resultado.
- Al replanificar, las horas restantes pueden volver a caer en el día de origen: la capa de órdenes lo deja abierto para todas las actividades, también para la que se movió.
- Al validador y al MLP les llega la capacidad sin el tiempo ya fijado en el destino, de modo que un plan muy apretado no se da por *factible* cuando la asignación quedaría parcial. Si aun así una actividad queda incompleta, la capa reintenta la asignación antes de declarar que no alcanza.
- Hay una versión con el LLM real (`test_mover_una_sesion_con_llm_real`, ver la tabla de abajo). Usa fechas relativas a hoy en lugar de las fijas, y comprueba lo mismo, pero la orden la interpreta el LLM de verdad.

### Pruebas con el LLM real (las 28 que no corren por defecto)

Son las que marca `@pytest.mark.llm_real`:

| Prueba                                | Cantidad | Qué comprueba                                                                                 |
|---------------------------------------|----------|-----------------------------------------------------------------------------------------------|
| `test_interprete.py::test_llm_real`   | 24       | 24 frases (válidas, ambiguas, bobas y maliciosas; algunas con una decisión pendiente en el contexto). Condición dura: nada ambiguo, tonto o malicioso se vuelve una orden |
| `test_redactor.py::test_redactor_llm_real` | 2   | El texto del LLM sobre el horario completo y sobre una consulta pasa el validador de salida y no cae en la plantilla |
| `test_flujo.py::test_flujo_completo_con_llm_real` | 1 | Un mensaje real recorre todo el flujo hasta el JSON final                              |
| `test_flujo.py::test_mover_una_sesion_con_llm_real` | 1 | Crea un plan, el LLM real interpreta *no me gustó esta sesión, pásala al sábado a las 9* y el horario se recalcula con el MLP |

Para que funcionen:

1. Tener la clave en el `.env` (`cp .env.example .env` y completar `OPENAI_API_KEY`; ver Configuración del LLM).
2. Ejecutar solo esas pruebas con `-m llm_real`:

```bash
uv run pytest -m llm_real -v -s      # -s muestra los textos que escribe el LLM
```

Para correr **todo junto** (las 278 normales y las 28 reales): `uv run pytest -m ""`.

Qué tener presente:

- **Sin clave no fallan: se omiten** (`skipped`). Si ves `28 skipped`, falta la clave en el `.env` o el comando no se ejecutó desde la raíz del proyecto.
- **Gastan saldo, muy poco:** son 31 llamadas (24 + 2 + 2 del flujo + 3 de mover una sesión, sin contar reintentos). Con `gpt-6-luna` cuestan centavos, pero conviene revisar el saldo antes de una demo.
- **El LLM no es determinista.** Si una frase falla, no siempre es un error del sistema: puede ser un caso donde el LLM respondió distinto a lo esperado. La condición que sí debe cumplirse siempre es que ningún mensaje ambiguo, tonto o malicioso produzca una orden. Se repite la prueba y se mira el mensaje del fallo (con `-s`) antes de tocar el código.
- Si el saldo se agota, las llamadas fallan con `insufficient_quota`; el cliente no reintenta en ese caso.

## Tecnologías

Python · Keras / TensorFlow · Pydantic · OpenAI API · SQLite · NumPy · pandas · SciPy · Matplotlib · Jupyter · uv

Previstas para los siguientes pasos: FastAPI y TypeScript (front).
