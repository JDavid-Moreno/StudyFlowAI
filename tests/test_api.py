"""Pruebas de las rutas de la API con TestClient, un MLP fijo y un LLM simulado. Ver README, sección API."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from conftest import LLMGuion, PredictorFijo, act, dias, f, plan
from studyflow.api import Config, crear_app

HOY = datetime.now(ZoneInfo("America/Bogota")).date()      # la API usa la hora real del servidor, en Bogotá


def plan_json() -> dict:
    """Un plan que cabe, con fechas desde hoy."""
    return plan([act("Parcial de IA", 6, 6, "alta", "alta", inicio=HOY), act("Taller", 5, 3, inicio=HOY)], dias(0, 10, pares=(("06:00", "09:00"), ("18:00", "21:00")), inicio=HOY))


def cliente(tmp_path, *ordenes, **config) -> tuple[TestClient, LLMGuion]:
    """Un cliente con su propia base, el MLP fijo y un LLM que responde el guion."""
    llm = LLMGuion(*ordenes)
    app = crear_app(Config(ruta_db=str(tmp_path / "api.db"), **config), mlp=PredictorFijo(), llamar=llm)
    return TestClient(app), llm


def nuevo(c: TestClient) -> dict:
    """Encabezado de un estudiante recién creado."""
    r = c.post("/estudiantes")
    assert r.status_code == 201
    return {"X-Estudiante-Id": r.json()["estudiante_id"]}


def test_salud_usa_la_hora_de_bogota(tmp_path):
    """/salud responde y su hora es la de Bogotá, no la del reloj del servidor en UTC."""
    c, _ = cliente(tmp_path)
    with c:
        hora = datetime.fromisoformat(c.get("/salud").json()["hora"])
    assert abs(hora - datetime.now(ZoneInfo("America/Bogota")).replace(tzinfo=None)) < timedelta(minutes=1)


def test_identificador_desconocido_o_ausente(tmp_path):
    """Un identificador que no existe da 404 (el front pide otro) y sin encabezado la petición es inválida (422)."""
    c, _ = cliente(tmp_path)
    with c:
        assert c.get("/estado", headers={"X-Estudiante-Id": "no-existe"}).status_code == 404
        assert c.get("/estado").status_code == 422
        h = nuevo(c)
        assert c.get("/estado", headers=h).json() == {"plan": None, "horario": None, "bloques": [], "fijadas": [], "decision": None}


def test_flujo_completo_por_http(tmp_path):
    """Crear el plan, pedir un cambio por chat, confirmarlo y verlo en /estado."""
    c, llm = cliente(tmp_path, {"accion": "bloquear_dia", "fecha": {"tipo": "fecha_exacta", "fecha": f(1, HOY)}})
    with c:
        h = nuevo(c)
        creado = c.post("/plan", json={"plan": plan_json()}, headers=h).json()
        assert creado["tipo"] == "aplicada" and creado["texto_horario"]
        assert c.post("/plan", json={"plan": plan_json()}, headers=h).json()["tipo"] == "confirmar_reemplazo"
        pedido = c.post("/mensaje", json={"texto": "mañana no puedo"}, headers=h).json()
        assert pedido["tipo"] == "confirmacion"
        hecho = c.post("/confirmacion", json={"aceptar": True}, headers=h).json()
        assert hecho["tipo"] == "aplicada"
        estado = c.get("/estado", headers=h).json()
        assert estado == hecho["estado"] and not any(s["dia"] == f(1, HOY) for s in estado["horario"]["sesiones"])
    assert llm.llamadas == ["Redaccion", "OrdenLLM", "Redaccion"]


def test_mover_y_no_hecha_por_http(tmp_path):
    """Mover desde el calendario fija la sesión; una hora fuera de la rejilla de 30 min se rechaza (422)."""
    c, _ = cliente(tmp_path)
    with c:
        h = nuevo(c)
        sesiones = c.post("/plan", json={"plan": plan_json()}, headers=h).json()["estado"]["horario"]["sesiones"]
        taller = next(s for s in sesiones if s["actividad"] == "Taller" and s["dia"] > HOY.isoformat())
        cuerpo = {"actividad": "Taller", "dia_origen": taller["dia"], "dia_destino": f(4, HOY), "hora_inicio": "18:00"}
        r = c.post("/sesiones/mover", json=cuerpo, headers=h).json()
        assert r["tipo"] == "aplicada" and r["estado"]["fijadas"]
        assert c.post("/sesiones/mover", json={**cuerpo, "hora_inicio": "18:15"}, headers=h).status_code == 422
        a, b = r["estado"]["bloques"][0], next(x for x in r["estado"]["bloques"] if x["actividad"] != r["estado"]["bloques"][0]["actividad"])
        cambio = c.post("/sesiones/intercambiar", json={"a": {"dia": a["dia"], "hora": a["inicio"]}, "b": {"dia": b["dia"], "hora": b["inicio"]}}, headers=h)
        assert cambio.status_code == 200 and cambio.json()["tipo"] in ("aplicada", "rechazada") and cambio.json()["mensaje"]
        futura = {"actividad": "Taller", "dia": f(4, HOY), "inicio": "18:00:00", "fin": "19:00:00"}
        assert c.post("/sesiones/no-hecha", json=futura, headers=h).json()["tipo"] == "rechazada"     # todavía no ha pasado


def test_mensaje_vacio_o_largo_se_rechaza_sin_llamar_al_llm(tmp_path):
    """Los 500 caracteres se validan en la entrada: ni se llama al LLM."""
    c, llm = cliente(tmp_path)
    with c:
        h = nuevo(c)
        assert c.post("/mensaje", json={"texto": "x" * 501}, headers=h).status_code == 422
        assert c.post("/mensaje", json={"texto": ""}, headers=h).status_code == 422
    assert llm.llamadas == []


def test_limite_por_estudiante(tmp_path):
    """Pasado el límite por minuto se responde 429; consultar /estado no cuenta."""
    c, _ = cliente(tmp_path, *[{"accion": "fuera_de_alcance", "motivo": "saludo"}] * 3, peticiones_min=2)
    with c:
        h = nuevo(c)
        codigos = [c.post("/mensaje", json={"texto": "hola"}, headers=h).status_code for _ in range(3)]
        assert codigos == [200, 200, 429] and c.get("/estado", headers=h).status_code == 200
        otro = nuevo(c)
        assert c.post("/mensaje", json={"texto": "hola"}, headers=otro).status_code == 200      # el límite es por estudiante


def test_limite_de_estudiantes_por_ip(tmp_path):
    """No se pueden crear identificadores sin fin desde la misma conexión."""
    c, _ = cliente(tmp_path, estudiantes_ip_hora=2)
    with c:
        assert [c.post("/estudiantes").status_code for _ in range(3)] == [201, 201, 429]


def test_tope_diario_del_llm(tmp_path):
    """Al llegar al tope del día no se llama más al LLM: el intérprete avisa y el redactor usa su plantilla."""
    c, llm = cliente(tmp_path, {"accion": "fuera_de_alcance", "motivo": "saludo"}, llm_dia=1)
    with c:
        h = nuevo(c)
        creado = c.post("/plan", json={"plan": plan_json()}, headers=h).json()       # gasta la única llamada (redactor)
        assert creado["tipo"] == "aplicada"
        r = c.post("/mensaje", json={"texto": "hola"}, headers=h).json()
        assert r["tipo"] == "rechazada" and "Inténtalo de nuevo" in r["mensaje"]
        c.post("/plan", json={"plan": plan_json(), "reemplazar": True}, headers=h)
    assert llm.llamadas == ["Redaccion"]


def test_cors_solo_para_el_front(tmp_path):
    """El navegador solo deja llamar a la API desde el origen del front."""
    c, _ = cliente(tmp_path, origenes=["http://localhost:5173"])
    with c:
        bien = c.options("/estado", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
        mal = c.options("/estado", headers={"Origin": "http://otro.com", "Access-Control-Request-Method": "GET"})
    assert bien.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in mal.headers


@pytest.mark.llm_real
def test_flujo_con_el_llm_real(tmp_path):
    """Crear el plan, pedir un cambio por chat y confirmarlo con el LLM y el MLP reales (3 llamadas al LLM)."""
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        pytest.skip("sin OPENAI_API_KEY en el .env")
    pytest.importorskip("keras")
    from studyflow.llm import llamar_llm
    from studyflow.mlp import PredictorMLP
    with TestClient(crear_app(Config(ruta_db=str(tmp_path / "real.db")), mlp=PredictorMLP(), llamar=llamar_llm)) as c:
        h = nuevo(c)
        creado = c.post("/plan", json={"plan": plan_json()}, headers=h).json()
        assert creado["tipo"] == "aplicada" and creado["texto_horario"]
        print("\nRedactor:", creado["texto_horario"])
        pedido = c.post("/mensaje", json={"texto": "pasado mañana no puedo estudiar"}, headers=h).json()
        assert pedido["tipo"] == "confirmacion", pedido
        print("Confirmación:", pedido["mensaje"])
        hecho = c.post("/confirmacion", json={"aceptar": True}, headers=h).json()
        assert hecho["tipo"] == "aplicada", hecho
        assert not any(s["dia"] == f(2, HOY) for s in hecho["estado"]["horario"]["sesiones"])
        print("Después del cambio:", hecho["texto_horario"])
