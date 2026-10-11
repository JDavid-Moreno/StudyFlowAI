# StudyFlow AI

StudyFlow AI es un generador adaptativo de horarios de estudio. Combina un modelo de lenguaje (LLM) que procesa las instrucciones del estudiante, con una red neuronal (MLP) que prioriza y distribuye el trabajo. Finalmente, una capa determinista asigna las sesiones respetando fechas límite y disponibilidad.

Proyecto del curso PTIA.

## Flujo del sistema

```
Formulario / prompt -> LLM (JSON validado) -> Validador de factibilidad -> MLP -> Capa de asignación → Horario
```

| Componente                | Qué hace                                                                                         | Estado                                 |
|---------------------------|--------------------------------------------------------------------------------------------------|----------------------------------------|
| LLM                       | Traduce el formulario y los cambios pedidos a datos estructurados                                | Pendiente                              |
| Validador de factibilidad | Revisa por fecha límite acumulada que las horas quepan en el tiempo disponible                   | Hecho (se usa para generar el dataset) |
| **MLP**                   | Una red, dos salidas: prioridad de cada actividad y reparto del trabajo en inicio, medio y final | **Hecho** (este repositorio)           |
| Capa de asignación        | Coloca las sesiones en el calendario de forma determinista                                       | Pendiente                              |
| Interfaz                  | Prototipo de interfaz                                                                            | Aparte                                 |

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
├── notebooks/StudyFlow_MLP.ipynb   Todo el desarrollo: datos, decisiones, red y conclusiones
├── data/
│   ├── dataset_studyflow.csv       Dataset v1 (secciones 2.1 a 2.5 del notebook)
│   └── dataset_studyflow_v2.csv    Dataset v2, el final: con este se entrena la red
├── generar_dataset.py              Generador de la v1
├── generar_dataset_v2.py           Generador de la v2
├── docs/                           Documentación
├── pyproject.toml / uv.lock        Dependencias
└── Proyecto PTIA.pdf               Documento del proyecto
```

La v1 se conserva a propósito: muestra que con la primera regla la regresión lineal ya resolvía el problema, y por eso se creó la v2 (explicado en la sección 2.6 del notebook).

## Cómo ejecutarlo

Requiere [uv](https://docs.astral.sh/uv/) y Python 3.11 o superior.

```bash
uv sync
uv run jupyter lab
```

Abrir `notebooks/StudyFlow_MLP.ipynb` y ejecutar todas las celdas (Run All). Se entrenan varias redes (comparación de pesos y semillas), así que la ejecución completa tarda unos minutos en CPU. Los datasets ya están incluidos en `data/`.

Para regenerar los datasets (son deterministas, `SEED = 42`):

```bash
uv run python generar_dataset_v2.py   # escribe data/dataset_studyflow_v2.csv
uv run python generar_dataset.py      # escribe dataset_studyflow.csv en la carpeta actual; moverlo a data/
```

## Tecnologías

Python · Keras / TensorFlow · NumPy · pandas · SciPy · Matplotlib · Jupyter · uv
