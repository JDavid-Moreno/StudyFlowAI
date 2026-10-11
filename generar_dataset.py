"""
StudyFlow AI - Generador del dataset sintético para el MLP (Hito 2).

Cada escenario representa a un estudiante con 3 a 8 actividades y una
disponibilidad diaria de 2 a 8 horas durante 30 días. Solo se conservan los
escenarios que superan el validador de factibilidad (revisión por fecha límite
acumulada). Cada actividad se etiqueta con el profesor heurístico.

Salida: dataset_studyflow.csv (una fila por actividad).
"""
import numpy as np
import pandas as pd

SEED = 42
N_ESCENARIOS = 3000
MIN_ACT, MAX_ACT = 3, 8
DIAS_MAX = 30                 # horizonte de planificación
DIAS_MIN = 1
HORAS_MIN, HORAS_MAX = 1.0, 20.0
DISP_MIN, DISP_MAX = 2.0, 8.0  # horas disponibles por día
MARGEN = 0.0                   # margen del validador (pendiente de decidir)
RUIDO_PRIORIDAD = 0.02
SPLIT = (0.70, 0.15, 0.15)

# Rango fijo para normalizar las horas disponibles en la ventana
VENTANA_MIN = DISP_MIN * DIAS_MIN   # 2 h
VENTANA_MAX = DISP_MAX * DIAS_MAX   # 240 h

NIVELES = ["baja", "media", "alta"]
NIVEL_A_TANH = {"baja": -1.0, "media": 0.0, "alta": 1.0}   # entrada del MLP
NIVEL_A_01 = {"baja": 0.0, "media": 0.5, "alta": 1.0}      # uso del profesor

rng = np.random.default_rng(SEED)


def escalar(x, lo, hi):
    """Escala lineal de [lo, hi] a [-1, 1]."""
    return 2.0 * (x - lo) / (hi - lo) - 1.0


def generar_escenario():
    n = rng.integers(MIN_ACT, MAX_ACT + 1)
    # Disponibilidad por día, en pasos de 0.5 h
    disp = rng.integers(int(DISP_MIN * 2), int(DISP_MAX * 2) + 1, size=DIAS_MAX) / 2.0
    disp_acum = np.cumsum(disp)  # horas disponibles hasta el día d (índice d-1)
    dias = rng.integers(DIAS_MIN, DIAS_MAX + 1, size=n)
    horas = rng.integers(int(HORAS_MIN * 2), int(HORAS_MAX * 2) + 1, size=n) / 2.0
    prioridad = rng.choice(NIVELES, size=n)
    dificultad = rng.choice(NIVELES, size=n)
    return dias, horas, prioridad, dificultad, disp_acum


def es_factible(dias, horas, disp_acum):
    """Para cada fecha límite d: horas que vencen hasta d <= horas disponibles hasta d."""
    for d in np.unique(dias):
        carga = horas[dias <= d].sum()
        if carga > disp_acum[d - 1] * (1.0 + MARGEN):
            return False
    return True


def profesor(dias, horas, prioridad, dificultad, ventana, carga):
    """Etiquetas del profesor heurístico."""
    urgencia = 1.0 - dias / DIAS_MAX
    p01 = np.array([NIVEL_A_01[v] for v in prioridad])
    d01 = np.array([NIVEL_A_01[v] for v in dificultad])
    carga01 = np.clip(carga, 0.0, 1.0)
    horas01 = (horas - HORAS_MIN) / (HORAS_MAX - HORAS_MIN)

    # Salida A: prioridad
    prio_limpia = 0.40 * urgencia + 0.25 * p01 + 0.20 * d01 + 0.15 * carga01
    prio = np.clip(prio_limpia + rng.normal(0.0, RUIDO_PRIORIDAD, size=len(dias)), 0.0, 1.0)

    # Salida B: perfil de reparto (inicio, medio, final)
    s = 0.5 * d01 + 0.3 * carga01 + 0.2 * horas01
    logits = np.stack([2.5 * s, np.full_like(s, 0.5), np.zeros_like(s)], axis=1)
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    reparto = e / e.sum(axis=1, keepdims=True)
    return prio_limpia, prio, reparto


filas = []
intentos = 0
esc_id = 0
while esc_id < N_ESCENARIOS:
    intentos += 1
    dias, horas, prioridad, dificultad, disp_acum = generar_escenario()
    if not es_factible(dias, horas, disp_acum):
        continue

    ventana = disp_acum[dias - 1]
    # Carga relativa: horas que vencen hasta la fecha de la actividad / horas disponibles hasta esa fecha
    carga = np.array([horas[dias <= d].sum() for d in dias]) / ventana
    prio_limpia, prio, reparto = profesor(dias, horas, prioridad, dificultad, ventana, carga)

    for i in range(len(dias)):
        filas.append({
            "escenario_id": esc_id,
            "actividad_id": i,
            # Características originales
            "dias_restantes": int(dias[i]),
            "prioridad": prioridad[i],
            "dificultad": dificultad[i],
            "horas_estimadas": float(horas[i]),
            "horas_disponibles_ventana": float(ventana[i]),
            "carga_relativa": round(float(carga[i]), 4),
            # Entradas normalizadas del MLP ([-1, 1])
            "x_dias": round(escalar(dias[i], DIAS_MIN, DIAS_MAX), 4),
            "x_prioridad": NIVEL_A_TANH[prioridad[i]],
            "x_dificultad": NIVEL_A_TANH[dificultad[i]],
            "x_horas": round(escalar(horas[i], HORAS_MIN, HORAS_MAX), 4),
            "x_ventana": round(escalar(ventana[i], VENTANA_MIN, VENTANA_MAX), 4),
            "x_carga": round(escalar(min(carga[i], 1.0), 0.0, 1.0), 4),
            # Etiquetas del profesor
            "y_prioridad": round(float(prio[i]), 4),
            "y_prioridad_sin_ruido": round(float(prio_limpia[i]), 4),
            "y_reparto_inicio": round(float(reparto[i, 0]), 4),
            "y_reparto_medio": round(float(reparto[i, 1]), 4),
            "y_reparto_final": round(float(reparto[i, 2]), 4),
        })
    esc_id += 1

df = pd.DataFrame(filas)

# Partición 70/15/15 por escenario (no por actividad) para evitar filtración
ids = rng.permutation(N_ESCENARIOS)
n_tr = int(SPLIT[0] * N_ESCENARIOS)
n_va = int(SPLIT[1] * N_ESCENARIOS)
conjunto = {}
for k in ids[:n_tr]:
    conjunto[k] = "train"
for k in ids[n_tr:n_tr + n_va]:
    conjunto[k] = "val"
for k in ids[n_tr + n_va:]:
    conjunto[k] = "test"
df.insert(2, "conjunto", df["escenario_id"].map(conjunto))

# Ajuste de redondeo: que el reparto sume exactamente 1
df["y_reparto_final"] = (1.0 - df["y_reparto_inicio"] - df["y_reparto_medio"]).round(4)

df.to_csv("dataset_studyflow.csv", index=False)
print(f"Escenarios: {N_ESCENARIOS} | intentos: {intentos} | aceptación: {N_ESCENARIOS / intentos:.1%}")
print(f"Filas: {len(df)}")
print(df["conjunto"].value_counts())
