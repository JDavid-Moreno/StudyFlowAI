"""Persistencia con SQLite (esquema, plan y horario, orden y decisión pendientes). Ver StudyFlow_Persistencia.ipynb."""
import sqlite3
import uuid
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from studyflow.asignacion import ResultadoAsignacion, Sesion
from studyflow.interprete import Orden
from studyflow.models import PlanEntrada
from studyflow.ordenes import Decision

ESQUEMA = """
CREATE TABLE IF NOT EXISTS estudiantes (
    id        TEXT PRIMARY KEY,
    creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS planes (
    estudiante_id   TEXT PRIMARY KEY REFERENCES estudiantes(id) ON DELETE CASCADE,
    fecha_inicio    TEXT NOT NULL,
    duracion_sesion INTEGER NOT NULL CHECK (duracion_sesion BETWEEN 30 AND 240 AND duracion_sesion % 30 = 0),
    max_horas_dia   REAL CHECK (max_horas_dia IS NULL OR (max_horas_dia > 0 AND max_horas_dia <= 8)),
    actualizado_en  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actividades (
    estudiante_id   TEXT NOT NULL REFERENCES planes(estudiante_id) ON DELETE CASCADE,
    posicion        INTEGER NOT NULL,
    nombre          TEXT NOT NULL,
    prioridad       TEXT NOT NULL CHECK (prioridad IN ('baja', 'media', 'alta')),
    dificultad      TEXT NOT NULL CHECK (dificultad IN ('baja', 'media', 'alta')),
    fecha_limite    TEXT NOT NULL,
    horas_estimadas REAL NOT NULL CHECK (horas_estimadas > 0 AND horas_estimadas <= 100),
    PRIMARY KEY (estudiante_id, posicion)
);

CREATE TABLE IF NOT EXISTS franjas (
    estudiante_id TEXT NOT NULL REFERENCES planes(estudiante_id) ON DELETE CASCADE,
    dia           TEXT NOT NULL,
    inicio        TEXT NOT NULL,
    fin           TEXT NOT NULL CHECK (fin > inicio),
    PRIMARY KEY (estudiante_id, dia, inicio)
);

CREATE TABLE IF NOT EXISTS sesiones (
    estudiante_id TEXT NOT NULL REFERENCES planes(estudiante_id) ON DELETE CASCADE,
    dia           TEXT NOT NULL,
    inicio        TEXT NOT NULL,
    fin           TEXT NOT NULL CHECK (fin > inicio),
    actividad     TEXT NOT NULL,
    fijada        INTEGER NOT NULL DEFAULT 0 CHECK (fijada IN (0, 1)),
    PRIMARY KEY (estudiante_id, dia, inicio)
);

CREATE TABLE IF NOT EXISTS resumen (
    estudiante_id     TEXT NOT NULL REFERENCES planes(estudiante_id) ON DELETE CASCADE,
    posicion          INTEGER NOT NULL,
    nombre            TEXT NOT NULL,
    prioridad         REAL NOT NULL CHECK (prioridad BETWEEN 0 AND 1),
    horas_solicitadas REAL NOT NULL,
    horas_asignadas   REAL NOT NULL,
    horas_faltantes   REAL NOT NULL,
    estado            TEXT NOT NULL CHECK (estado IN ('asignada', 'parcial', 'no_asignada')),
    PRIMARY KEY (estudiante_id, posicion)
);

CREATE TABLE IF NOT EXISTS orden_pendiente (
    estudiante_id TEXT PRIMARY KEY REFERENCES estudiantes(id) ON DELETE CASCADE,
    orden         TEXT NOT NULL,
    creada_en     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_pendiente (
    estudiante_id TEXT PRIMARY KEY REFERENCES estudiantes(id) ON DELETE CASCADE,
    decision      TEXT NOT NULL,
    creada_en     TEXT NOT NULL
);
"""


def conectar(ruta: str = "studyflow.db") -> sqlite3.Connection:
    """Abre la base (la crea si no existe), activa las llaves foráneas y crea las tablas que falten."""
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(ESQUEMA)
    return con


def _ahora() -> str:
    """Fecha y hora actuales en texto ISO, sin microsegundos."""
    return datetime.now().isoformat(timespec="seconds")


# --- Estudiantes, plan y horario (paso 3) ---------------------------------------------------------

class PlanGuardado(BaseModel):
    """Lo que se recupera de un estudiante: su plan, la fecha desde la que se planificó, el horario vigente y las sesiones fijadas."""

    plan: PlanEntrada
    fecha_inicio: date
    horario: ResultadoAsignacion
    fijadas: list[Sesion]
    actualizado_en: datetime


def nuevo_estudiante(con: sqlite3.Connection) -> str:
    """Registra un estudiante anónimo y devuelve su identificador."""
    estudiante_id = uuid.uuid4().hex
    with con:
        con.execute("INSERT INTO estudiantes VALUES (?, ?)", (estudiante_id, _ahora()))
    return estudiante_id


def existe_estudiante(con: sqlite3.Connection, estudiante_id: str) -> bool:
    """Indica si el identificador pertenece a un estudiante registrado."""
    return con.execute("SELECT 1 FROM estudiantes WHERE id = ?", (estudiante_id,)).fetchone() is not None


def guardar_plan(con: sqlite3.Connection, estudiante_id: str, plan: PlanEntrada, fecha_inicio: date,
                 horario: ResultadoAsignacion, fijadas: list[Sesion] | None = None) -> None:
    """Reemplaza el plan, el horario y las fijadas del estudiante en una sola transacción: se guarda todo o nada."""
    e, fijadas = estudiante_id, fijadas or []
    if any(s not in horario.sesiones for s in fijadas):
        raise ValueError("Una sesión fijada no está en el horario.")
    with con:
        con.execute("DELETE FROM planes WHERE estudiante_id = ?", (e,))
        con.execute("DELETE FROM decision_pendiente WHERE estudiante_id = ?", (e,))
        con.execute("INSERT INTO planes VALUES (?, ?, ?, ?, ?)",
                    (e, fecha_inicio.isoformat(), int(plan.duracion_sesion), plan.max_horas_dia, _ahora()))
        con.executemany("INSERT INTO actividades VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(e, i, a.nombre, a.prioridad.value, a.dificultad.value, a.fecha_limite.isoformat(), a.horas_estimadas)
                         for i, a in enumerate(plan.actividades)])
        con.executemany("INSERT INTO franjas VALUES (?, ?, ?, ?)",
                        [(e, d.dia.isoformat(), fr.inicio.isoformat(), fr.fin.isoformat()) for d in plan.disponibilidad for fr in d.franjas])
        con.executemany("INSERT INTO sesiones VALUES (?, ?, ?, ?, ?, ?)",
                        [(e, s.dia.isoformat(), s.inicio.isoformat(), s.fin.isoformat(), s.actividad, int(s in fijadas))
                         for s in horario.sesiones])
        con.executemany("INSERT INTO resumen VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [(e, i, a.nombre, a.prioridad, a.horas_solicitadas, a.horas_asignadas, a.horas_faltantes, a.estado.value)
                         for i, a in enumerate(horario.actividades)])


def _filas(con: sqlite3.Connection, consulta: str, estudiante_id: str) -> list[dict]:
    """Ejecuta una consulta filtrada por estudiante y devuelve las filas como diccionarios."""
    return [dict(fila) for fila in con.execute(consulta, (estudiante_id,))]


def cargar_plan(con: sqlite3.Connection, estudiante_id: str) -> PlanGuardado | None:
    """Recupera el plan, el horario y las fijadas vigentes del estudiante, o None si todavía no tiene plan."""
    filas = _filas(con, "SELECT * FROM planes WHERE estudiante_id = ?", estudiante_id)
    if not filas:
        return None
    p = filas[0]
    dias_con_franjas = {}
    for fr in _filas(con, "SELECT dia, inicio, fin FROM franjas WHERE estudiante_id = ? ORDER BY dia, inicio", estudiante_id):
        dias_con_franjas.setdefault(fr.pop("dia"), []).append(fr)
    plan = {"actividades": _filas(con, "SELECT nombre, prioridad, dificultad, fecha_limite, horas_estimadas FROM actividades "
                                       "WHERE estudiante_id = ? ORDER BY posicion", estudiante_id),
            "disponibilidad": [{"dia": d, "franjas": frs} for d, frs in dias_con_franjas.items()],
            "duracion_sesion": p["duracion_sesion"], "max_horas_dia": p["max_horas_dia"]}
    sesiones = _filas(con, "SELECT actividad, dia, inicio, fin, fijada FROM sesiones WHERE estudiante_id = ? ORDER BY dia, inicio",
                      estudiante_id)
    horario = {"sesiones": [{k: v for k, v in s.items() if k != "fijada"} for s in sesiones],
               "actividades": _filas(con, "SELECT nombre, prioridad, horas_solicitadas, horas_asignadas, horas_faltantes, estado "
                                          "FROM resumen WHERE estudiante_id = ? ORDER BY posicion", estudiante_id)}
    fijadas = [{k: v for k, v in s.items() if k != "fijada"} for s in sesiones if s["fijada"]]
    return PlanGuardado(plan=plan, fecha_inicio=p["fecha_inicio"], horario=horario, fijadas=fijadas, actualizado_en=p["actualizado_en"])


# --- Orden y decisión pendientes (paso 4) ---------------------------------------------------------

VIGENCIA_ORDEN = timedelta(minutes=30)
VIGENCIA_DECISION = timedelta(minutes=30)


def guardar_orden_pendiente(con: sqlite3.Connection, estudiante_id: str, orden: Orden) -> None:
    """Guarda la orden que espera confirmación; si ya había otra, la reemplaza."""
    with con:
        con.execute("INSERT OR REPLACE INTO orden_pendiente VALUES (?, ?, ?)", (estudiante_id, orden.model_dump_json(), _ahora()))


def tomar_orden_pendiente(con: sqlite3.Connection, estudiante_id: str) -> Orden | None:
    """Saca la orden pendiente para aplicarla; devuelve None si no había o si ya venció."""
    with con:
        fila = con.execute("SELECT orden, creada_en FROM orden_pendiente WHERE estudiante_id = ?", (estudiante_id,)).fetchone()
        con.execute("DELETE FROM orden_pendiente WHERE estudiante_id = ?", (estudiante_id,))
    if fila is None or datetime.now() - datetime.fromisoformat(fila["creada_en"]) > VIGENCIA_ORDEN:
        return None
    return Orden.model_validate_json(fila["orden"])


def descartar_orden_pendiente(con: sqlite3.Connection, estudiante_id: str) -> None:
    """Borra la orden pendiente porque el estudiante no la confirmó."""
    with con:
        con.execute("DELETE FROM orden_pendiente WHERE estudiante_id = ?", (estudiante_id,))


def guardar_decision_pendiente(con: sqlite3.Connection, estudiante_id: str, decision: Decision) -> None:
    """Guarda la decisión que espera la respuesta del estudiante; si ya había otra, la reemplaza y reinicia el vencimiento."""
    with con:
        con.execute("INSERT OR REPLACE INTO decision_pendiente VALUES (?, ?, ?)", (estudiante_id, decision.model_dump_json(), _ahora()))


def leer_decision_pendiente(con: sqlite3.Connection, estudiante_id: str) -> Decision | None:
    """Devuelve la decisión pendiente sin borrarla; None si no hay o si ya venció."""
    fila = con.execute("SELECT decision, creada_en FROM decision_pendiente WHERE estudiante_id = ?", (estudiante_id,)).fetchone()
    if fila is None or datetime.now() - datetime.fromisoformat(fila["creada_en"]) > VIGENCIA_DECISION:
        return None
    return Decision.model_validate_json(fila["decision"])


def descartar_decision_pendiente(con: sqlite3.Connection, estudiante_id: str) -> None:
    """Borra la decisión pendiente porque el estudiante canceló el cambio."""
    with con:
        con.execute("DELETE FROM decision_pendiente WHERE estudiante_id = ?", (estudiante_id,))
