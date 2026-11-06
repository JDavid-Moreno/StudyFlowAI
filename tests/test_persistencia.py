"""Pruebas de la persistencia. Ver StudyFlow_Persistencia.ipynb, pasos 2.1, 3.2, 4.1 y 6."""
import random
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from conftest import INICIO, act, dias, f, franjas, plan
from studyflow.asignacion import Prediccion, ResultadoAsignacion, asignar
from studyflow.interprete import Orden
from studyflow.models import PlanEntrada
from studyflow.persistencia import (cargar_plan, conectar, descartar_orden_pendiente, existe_estudiante, guardar_orden_pendiente,
                                    guardar_plan, nuevo_estudiante, tomar_orden_pendiente)
from studyflow.validador import Estado, validar


# --- Esquema --------------------------------------------------------------------------------------
def base() -> sqlite3.Connection:
    """Base nueva en memoria para una prueba."""
    return conectar(":memory:")


def contar(con: sqlite3.Connection, tabla: str) -> int:
    """Número de filas de una tabla."""
    return con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]


def falla_integridad(fn, fragmento: str):
    """Comprueba que la base rechace fn con un error que contiene el fragmento."""
    try:
        fn()
    except sqlite3.IntegrityError as e:
        assert fragmento in str(e), str(e)
        return
    raise AssertionError(f"la base debía rechazarlo ({fragmento})")


def test_crea_todas_las_tablas():
    """conectar crea las siete tablas del esquema."""
    tablas = {fila["name"] for fila in base().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tablas == {"estudiantes", "planes", "actividades", "franjas", "sesiones", "resumen", "orden_pendiente"}


def test_reconectar_no_borra_nada():
    """Abrir otra vez el mismo archivo conserva los datos, porque las tablas solo se crean si faltan."""
    ruta = str(Path(tempfile.mkdtemp()) / "prueba.db")
    con = conectar(ruta)
    con.execute("INSERT INTO estudiantes VALUES ('e1', '2026-10-25T10:00:00')")
    con.commit()
    con.close()
    assert conectar(ruta).execute("SELECT id FROM estudiantes").fetchone()["id"] == "e1"


def test_llaves_foraneas_activas():
    """La base no acepta un plan de un estudiante que no existe."""
    con = base()
    falla_integridad(lambda: con.execute("INSERT INTO planes VALUES ('nadie', '2026-10-25', 60, NULL, '2026-10-25T10:00:00')"), "FOREIGN KEY")


def test_checks_rechazan_valores_invalidos():
    """Los CHECK del esquema frenan una duración, un nivel y una franja inválidos."""
    con = base()
    con.execute("INSERT INTO estudiantes VALUES ('e1', '2026-10-25T10:00:00')")
    falla_integridad(lambda: con.execute("INSERT INTO planes VALUES ('e1', '2026-10-25', 45, NULL, '2026-10-25T10:00:00')"), "CHECK")
    con.execute("INSERT INTO planes VALUES ('e1', '2026-10-25', 60, NULL, '2026-10-25T10:00:00')")
    falla_integridad(lambda: con.execute("INSERT INTO actividades VALUES ('e1', 0, 'Taller', 'urgente', 'media', '2026-10-30', 3)"), "CHECK")
    falla_integridad(lambda: con.execute("INSERT INTO franjas VALUES ('e1', '2026-10-26', '21:00:00', '18:00:00')"), "CHECK")


def test_borrar_el_plan_borra_todo_lo_suyo():
    """Con ON DELETE CASCADE, al borrar un plan se van sus actividades y franjas."""
    con = base()
    con.execute("INSERT INTO estudiantes VALUES ('e1', '2026-10-25T10:00:00')")
    con.execute("INSERT INTO planes VALUES ('e1', '2026-10-25', 60, NULL, '2026-10-25T10:00:00')")
    con.execute("INSERT INTO actividades VALUES ('e1', 0, 'Taller', 'media', 'media', '2026-10-30', 3)")
    con.execute("INSERT INTO franjas VALUES ('e1', '2026-10-26', '18:00:00', '21:00:00')")
    con.execute("DELETE FROM planes WHERE estudiante_id = 'e1'")
    assert contar(con, "actividades") == contar(con, "franjas") == 0 and contar(con, "estudiantes") == 1


# --- Datos de prueba ------------------------------------------------------------------------------
PREDS_EJEMPLO = [Prediccion(prioridad=0.87, perfil=[0.45, 0.35, 0.20]), Prediccion(prioridad=0.55, perfil=[0.30, 0.40, 0.30])]
UNIF = [1 / 3] * 3


def ejemplo() -> tuple[PlanEntrada, ResultadoAsignacion]:
    """Plan del paso 4 de la asignación (Parcial de IA y Taller) con su horario."""
    p = PlanEntrada(**plan([act("Parcial de IA", 6, 8, "alta", "alta"), act("Taller", 5, 4)], dias(0, 10)))
    return p, asignar(p, PREDS_EJEMPLO, INICIO)


def normalizar(p: PlanEntrada) -> PlanEntrada:
    """El plan como vuelve de la base: sin días vacíos y con días y franjas en orden."""
    disp = [d.model_copy(update={"franjas": sorted(d.franjas, key=lambda fr: fr.inicio)}) for d in p.disponibilidad if d.franjas]
    return p.model_copy(update={"disponibilidad": sorted(disp, key=lambda d: d.dia)})


# --- Guardar y cargar -----------------------------------------------------------------------------
def test_estudiante_nuevo_sin_plan():
    """Un estudiante recién creado existe, tiene un identificador de 32 caracteres y aún no tiene plan."""
    con = base()
    e1, e2 = nuevo_estudiante(con), nuevo_estudiante(con)
    assert e1 != e2 and len(e1) == 32
    assert existe_estudiante(con, e1) and not existe_estudiante(con, "no-existe")
    assert cargar_plan(con, e1) is None


def test_guardar_y_cargar_devuelve_lo_mismo():
    """Lo que se carga es igual a lo que se guardó: plan, fecha de inicio y horario."""
    con = base()
    e = nuevo_estudiante(con)
    p, r = ejemplo()
    guardar_plan(con, e, p, INICIO, r)
    g = cargar_plan(con, e)
    assert g.plan == p and g.fecha_inicio == INICIO and g.horario == r


def test_guardar_otra_vez_reemplaza():
    """Guardar un plan nuevo reemplaza al anterior y no deja filas viejas."""
    con = base()
    e = nuevo_estudiante(con)
    p, r = ejemplo()
    guardar_plan(con, e, p, INICIO, r)
    p2 = PlanEntrada(**plan([act("Lectura", 4, 2, "baja", "baja")], dias(1, 5), sesion=30))
    r2 = asignar(p2, [Prediccion(prioridad=0.3, perfil=UNIF)], INICIO + timedelta(days=1))
    guardar_plan(con, e, p2, INICIO + timedelta(days=1), r2)
    g = cargar_plan(con, e)
    assert g.plan == p2 and g.horario == r2 and g.fecha_inicio == INICIO + timedelta(days=1)
    assert [contar(con, t) for t in ("planes", "actividades", "franjas", "resumen", "sesiones")] == [1, 1, 4, 1, len(r2.sesiones)]


def test_si_algo_falla_queda_el_plan_anterior():
    """Si el guardado falla a mitad de camino, la transacción se deshace y el plan anterior sigue completo."""
    con = base()
    e = nuevo_estudiante(con)
    p, r = ejemplo()
    guardar_plan(con, e, p, INICIO, r)
    roto = r.model_copy(update={"sesiones": r.sesiones + [r.sesiones[0]]})  # sesión repetida: viola la llave primaria
    falla_integridad(lambda: guardar_plan(con, e, p, INICIO + timedelta(days=1), roto), "UNIQUE")
    g = cargar_plan(con, e)
    assert g.plan == p and g.horario == r and g.fecha_inicio == INICIO


def test_estudiante_inexistente_no_guarda_nada():
    """No se puede guardar un plan para un identificador que no está registrado."""
    con = base()
    p, r = ejemplo()
    falla_integridad(lambda: guardar_plan(con, "no-existe", p, INICIO, r), "FOREIGN KEY")
    assert contar(con, "planes") == contar(con, "actividades") == 0


def test_estudiantes_no_se_mezclan():
    """Cada estudiante recupera solo su propio plan."""
    con = base()
    e1, e2 = nuevo_estudiante(con), nuevo_estudiante(con)
    p, r = ejemplo()
    p2 = PlanEntrada(**plan([act("Lectura", 4, 2)], dias(0, 5)))
    r2 = asignar(p2, [Prediccion(prioridad=0.5, perfil=UNIF)], INICIO)
    guardar_plan(con, e1, p, INICIO, r)
    guardar_plan(con, e2, p2, INICIO, r2)
    assert cargar_plan(con, e1).plan == p and cargar_plan(con, e2).plan == p2


def test_nombres_repetidos_conservan_su_posicion():
    """Dos actividades con el mismo nombre vuelven en el mismo orden, para que las predicciones sigan alineadas."""
    con = base()
    e = nuevo_estudiante(con)
    p = PlanEntrada(**plan([act("Lectura", 6, 3, "alta"), act("Lectura", 8, 1, "baja")], dias(0, 10)))
    r = asignar(p, [Prediccion(prioridad=0.8, perfil=UNIF), Prediccion(prioridad=0.2, perfil=UNIF)], INICIO)
    guardar_plan(con, e, p, INICIO, r)
    assert [(a.horas_estimadas, a.prioridad.value) for a in cargar_plan(con, e).plan.actividades] == [(3, "alta"), (1, "baja")]


def test_dias_sin_franjas_no_cambian_el_resultado():
    """Un día bloqueado (sin franjas) no se guarda, y eso no cambia ni la validación ni la asignación."""
    con = base()
    e = nuevo_estudiante(con)
    disp = dias(0, 10)
    disp[2]["franjas"] = []  # el estudiante bloqueó ese día
    p = PlanEntrada(**plan([act("Parcial de IA", 6, 8, "alta", "alta"), act("Taller", 5, 4)], disp))
    r = asignar(p, PREDS_EJEMPLO, INICIO)
    guardar_plan(con, e, p, INICIO, r)
    cargado = cargar_plan(con, e).plan
    assert len(cargado.disponibilidad) == 9 and cargado == normalizar(p)
    assert validar(cargado, INICIO) == validar(p, INICIO)
    assert asignar(cargado, PREDS_EJEMPLO, INICIO) == r


# --- Orden pendiente ------------------------------------------------------------------------------
ORDEN_CREAR = Orden(accion="crear_plan", actividades=[act("Parcial de IA", 6, 8, "alta", "alta"), act("Taller", 5, 4)])
ORDEN_MOVER = Orden(accion="mover_sesion", actividad="Parcial de IA", dia_origen=f(1), dia_destino=f(6), hora_inicio="09:00")


def test_sin_orden_pendiente():
    """Si no hay orden pendiente, tomar devuelve None y descartar no falla."""
    con = base()
    e = nuevo_estudiante(con)
    assert tomar_orden_pendiente(con, e) is None
    descartar_orden_pendiente(con, e)


def test_tomar_devuelve_la_orden_y_la_saca():
    """La orden vuelve igual a como se guardó (con sus actividades) y no se puede tomar dos veces."""
    con = base()
    e = nuevo_estudiante(con)
    guardar_orden_pendiente(con, e, ORDEN_CREAR)
    assert tomar_orden_pendiente(con, e) == ORDEN_CREAR
    assert tomar_orden_pendiente(con, e) is None


def test_orden_nueva_reemplaza_a_la_anterior():
    """Si el estudiante escribe otra orden antes de confirmar, la que espera es la última."""
    con = base()
    e = nuevo_estudiante(con)
    guardar_orden_pendiente(con, e, ORDEN_CREAR)
    guardar_orden_pendiente(con, e, ORDEN_MOVER)
    assert contar(con, "orden_pendiente") == 1
    assert tomar_orden_pendiente(con, e) == ORDEN_MOVER


def test_descartar_borra_la_orden():
    """Cuando el estudiante no confirma, la orden se borra y ya no se puede aplicar."""
    con = base()
    e = nuevo_estudiante(con)
    guardar_orden_pendiente(con, e, ORDEN_MOVER)
    descartar_orden_pendiente(con, e)
    assert tomar_orden_pendiente(con, e) is None


def test_orden_vencida_no_se_aplica():
    """Una orden de hace más de 30 minutos no se aplica (sus fechas pudieron quedar viejas) y se borra."""
    con = base()
    e = nuevo_estudiante(con)
    guardar_orden_pendiente(con, e, ORDEN_MOVER)
    hace_31_min = (datetime.now() - timedelta(minutes=31)).isoformat(timespec="seconds")
    with con:
        con.execute("UPDATE orden_pendiente SET creada_en = ?", (hace_31_min,))
    assert tomar_orden_pendiente(con, e) is None and contar(con, "orden_pendiente") == 0


def test_orden_de_otro_estudiante_no_se_ve():
    """La orden pendiente de un estudiante no la puede tomar ni descartar otro."""
    con = base()
    e1, e2 = nuevo_estudiante(con), nuevo_estudiante(con)
    guardar_orden_pendiente(con, e1, ORDEN_MOVER)
    descartar_orden_pendiente(con, e2)
    assert tomar_orden_pendiente(con, e2) is None
    assert tomar_orden_pendiente(con, e1) == ORDEN_MOVER


# --- Planes aleatorios ----------------------------------------------------------------------------
def test_planes_aleatorios_se_guardan_y_recuperan_sin_diferencias():
    """874 planes al azar entre 5 estudiantes: cada uno recupera su último plan y no quedan filas de los anteriores."""
    rng = random.Random(7)
    niveles = ["baja", "media", "alta"]
    opciones = [(("18:00", "21:00"),), (("08:00", "10:00"), ("14:00", "16:30")), (("19:00", "20:30"),), (("16:00", "17:50"), ("19:00", "21:00"))]

    def plan_aleatorio() -> PlanEntrada:
        actividades = [act(f"A{i}", rng.randint(3, 14), rng.choice([1, 1.5, 2, 3, 4, 5, 6]), rng.choice(niveles), rng.choice(niveles))
                       for i in range(rng.randint(1, 5))]
        disponibilidad = [{"dia": f(n), "franjas": franjas(*rng.choice(opciones)) if rng.random() < 0.85 else []} for n in range(16)]
        return PlanEntrada(**plan(actividades, disponibilidad, rng.choice([60, 90, 120]), rng.choice([None, 3, 4])))

    def prediccion_aleatoria() -> Prediccion:
        w = [rng.random() for _ in range(3)]
        return Prediccion(prioridad=rng.random(), perfil=[x / sum(w) for x in w])

    con = base()
    estudiantes = [nuevo_estudiante(con) for _ in range(5)]
    ultimo, guardados = {}, 0
    for _ in range(1000):
        p = plan_aleatorio()
        if validar(p, INICIO).estado == Estado.INVIABLE:
            continue
        r = asignar(p, [prediccion_aleatoria() for _ in p.actividades], INICIO)
        e = rng.choice(estudiantes)
        guardar_plan(con, e, p, INICIO, r)
        g = cargar_plan(con, e)
        assert g.plan == normalizar(p) and g.horario == r
        ultimo[e], guardados = (p, r), guardados + 1
    for e, (p, r) in ultimo.items():
        g = cargar_plan(con, e)
        assert g.plan == normalizar(p) and g.horario == r
    assert guardados == 874
    assert contar(con, "planes") == len(ultimo)
    assert contar(con, "actividades") == sum(len(p.actividades) for p, _ in ultimo.values())
    assert contar(con, "sesiones") == sum(len(r.sesiones) for _, r in ultimo.values())
