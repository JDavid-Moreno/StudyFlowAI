"""Cliente del LLM (OpenAI) y utilidades de texto. Ver StudyFlow_CapaLLM.ipynb, paso 2."""
import os
import time as _tiempo
from datetime import date
from functools import cache

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv(find_dotenv(usecwd=True))

MODELO_POR_DEFECTO = "gpt-6-luna"
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_texto(d: date) -> str:
    return f"{DIAS_SEMANA[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def duracion_texto(minutos: int) -> str:
    h, m = divmod(int(minutos), 60)
    return " ".join(p for p in (f"{h} h" if h else "", f"{m} min" if m else "") if p) or "0 min"


@cache
def _cliente():
    from openai import OpenAI
    # max_retries=0: los reintentos los controla llamar_llm; el plazo evita esperar sin fin
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=float(os.environ.get("LLM_TIMEOUT_SEGUNDOS", 90)), max_retries=0)


def llamar_llm(sistema: str, contenido: str, esquema: type[BaseModel]) -> str:
    """Pide al LLM una respuesta JSON que cumpla `esquema` y devuelve ese JSON como texto ("" si no hubo respuesta usable)."""
    from openai import APIConnectionError, APIStatusError, ContentFilterFinishReasonError, LengthFinishReasonError, RateLimitError
    if os.environ.get("LLM_PROVIDER", "openai") != "openai":
        raise ValueError("LLM_PROVIDER debe ser 'openai': es el único proveedor implementado")
    peticion = {"model": os.environ.get("OPENAI_MODEL", MODELO_POR_DEFECTO),
                "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": contenido}],
                "response_format": esquema,
                "max_completion_tokens": int(os.environ.get("OPENAI_MAX_TOKENS_SALIDA", 4000))}   # incluye los tokens de razonamiento
    esfuerzo = os.environ.get("OPENAI_REASONING_EFFORT", "low")   # vacío = no enviarlo
    if esfuerzo:
        peticion["reasoning_effort"] = esfuerzo
    for intento in range(3):
        try:
            return _cliente().chat.completions.parse(**peticion).choices[0].message.content or ""   # None si el modelo se negó
        except (LengthFinishReasonError, ContentFilterFinishReasonError, ValidationError):
            return ""      # respuesta cortada, bloqueada o fuera del esquema: el llamador la trata como inválida
        except Exception as e:
            sin_credito = getattr(e, "code", None) == "insufficient_quota"   # créditos agotados: esperar no lo arregla
            tope_diario = isinstance(e, RateLimitError) and "per day" in str(e).lower()
            pasajero = isinstance(e, (RateLimitError, APIConnectionError)) or (isinstance(e, APIStatusError) and e.status_code >= 500)
            if intento == 2 or not pasajero or sin_credito or tope_diario:
                raise
            print(f"Falla pasajera del LLM; reintento en {5 * (intento + 1)} s...")
            _tiempo.sleep(5 * (intento + 1))
