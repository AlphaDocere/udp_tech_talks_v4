"""
Capa de persistencia. Todo lo que toca disco vive acá:
- leer event_data.json
- parsear el CSV exportado de Luma
- leer/escribir el registrants.json unificado (Luma + check-ins en vivo)
- leer/escribir respuestas_estudiantes.json (pregunta ancla)
- leer/escribir posibilidades.json (última salida del agente) y resumen_evento.json
"""

import csv
import json
import os
from typing import List, Dict, Any

from src.models import Asistente, Charla, timestamp_ahora

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

EVENT_DATA_PATH = os.path.join(DATA_DIR, "event_data.json")
LUMA_CSV_PATH = os.path.join(DATA_DIR, "luma_registrants.csv")
REGISTRANTS_PATH = os.path.join(DATA_DIR, "registrants.json")
RESPUESTAS_ESTUDIANTES_PATH = os.path.join(DATA_DIR, "respuestas_estudiantes.json")
POSIBILIDADES_PATH = os.path.join(DATA_DIR, "posibilidades.json")
RESUMEN_EVENTO_PATH = os.path.join(DATA_DIR, "resumen_evento.json")
ENCUENTROS_PATH = os.path.join(DATA_DIR, "encuentros.json")
MAPA_CHARLISTAS_PATH = os.path.join(DATA_DIR, "mapa_charlistas.json")
PREGUNTAS_PANEL_PATH = os.path.join(DATA_DIR, "preguntas_panel.json")


def _leer_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def _escribir_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Evento / charlas
# ---------------------------------------------------------------------------

def cargar_evento() -> Dict[str, Any]:
    """Devuelve el dict completo de event_data.json (info del evento + charlas)."""
    return _leer_json(EVENT_DATA_PATH, {"evento": {}, "charlas": []})


def cargar_charlas() -> List[Charla]:
    data = cargar_evento()
    return [Charla.from_dict(c) for c in data.get("charlas", [])]


# ---------------------------------------------------------------------------
# Luma CSV -> Asistentes
# ---------------------------------------------------------------------------

def parsear_luma_csv(path: str = LUMA_CSV_PATH) -> List[Asistente]:
    """
    Lee el CSV exportado de Luma y lo transforma en una lista de Asistente.
    Es tolerante a columnas faltantes: si el CSV real trae más o menos
    columnas que el ejemplo, igual intenta extraer lo esencial.
    """
    if not os.path.exists(path):
        return []

    asistentes = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            asistentes.append(
                Asistente(
                    guest_id=row.get("guest_id", "").strip(),
                    nombre=row.get("name", "").strip(),
                    email=row.get("email", "").strip(),
                    comuna=row.get("Comuna", "").strip(),
                    rol_interes=row.get("¿Vienes como...?", "").strip(),
                    origen="luma",
                )
            )
    return asistentes


# ---------------------------------------------------------------------------
# registrants.json unificado
# ---------------------------------------------------------------------------

def cargar_registrants() -> List[Asistente]:
    """
    Devuelve la lista unificada de asistentes.
    Si registrants.json no existe todavía, lo construye a partir del CSV de
    Luma (primera vez que se corre la app) y lo persiste.
    """
    data = _leer_json(REGISTRANTS_PATH, None)
    if data is None:
        asistentes = parsear_luma_csv()
        guardar_registrants(asistentes)
        return asistentes
    return [Asistente.from_dict(a) for a in data.get("asistentes", [])]


def guardar_registrants(asistentes: List[Asistente]) -> None:
    _escribir_json(REGISTRANTS_PATH, {"asistentes": [a.to_dict() for a in asistentes]})


def registrar_checkin(
    asistentes: List[Asistente],
    nombre: str,
    energia_llegada: int,
    motivo_visita: str,
    curiosidad: str,
    email: str = "",
    comuna: str = "",
    rol_interes: str = "",
    guest_id: str = "",
    estado_animo: str = "",
) -> List[Asistente]:
    """
    Registra el check-in en la puerta con las 3 preguntas rápidas.
    - Si guest_id coincide con alguien ya cargado desde Luma, actualiza ese registro.
    - Si no, crea un asistente nuevo con origen="live".
    Devuelve la lista actualizada (y la persiste en disco).
    """
    ahora = timestamp_ahora()
    encontrado = None
    if guest_id:
        encontrado = next((a for a in asistentes if a.guest_id == guest_id), None)

    if encontrado:
        encontrado.energia_llegada = energia_llegada
        encontrado.estado_animo = estado_animo
        encontrado.motivo_visita = motivo_visita
        encontrado.curiosidad = curiosidad
        encontrado.checked_in_at = ahora
    else:
        nuevo_id = guest_id or f"live-{len(asistentes) + 1:04d}"
        asistentes.append(
            Asistente(
                guest_id=nuevo_id,
                nombre=nombre,
                email=email,
                comuna=comuna,
                rol_interes=rol_interes,
                origen="live",
                estado_animo=estado_animo,
                energia_llegada=energia_llegada,
                motivo_visita=motivo_visita,
                curiosidad=curiosidad,
                checked_in_at=ahora,
            )
        )

    guardar_registrants(asistentes)
    return asistentes


# ---------------------------------------------------------------------------
# Estación de estudiantes (pregunta ancla)
# ---------------------------------------------------------------------------

def cargar_respuestas_estudiantes() -> Dict[str, Any]:
    evento = cargar_evento().get("evento", {})
    default = {
        "pregunta_ancla": evento.get(
            "pregunta_ancla_estudiantes",
            "¿Cómo se educan las comunidades de desarrolladores en el 2027?",
        ),
        "respuestas": [],
    }
    return _leer_json(RESPUESTAS_ESTUDIANTES_PATH, default)


def agregar_respuesta_estudiante(nombre: str, respuesta: str) -> Dict[str, Any]:
    data = cargar_respuestas_estudiantes()
    data["respuestas"].append(
        {"nombre": nombre or "Anónimo", "respuesta": respuesta, "timestamp": timestamp_ahora()}
    )
    _escribir_json(RESPUESTAS_ESTUDIANTES_PATH, data)
    return data


# ---------------------------------------------------------------------------
# Salidas del agente: posibilidades.json y resumen_evento.json
# ---------------------------------------------------------------------------

def guardar_posibilidades(payload: Dict[str, Any]) -> None:
    _escribir_json(POSIBILIDADES_PATH, payload)


def cargar_posibilidades() -> Dict[str, Any]:
    return _leer_json(POSIBILIDADES_PATH, {})


def guardar_resumen_evento(payload: Dict[str, Any]) -> None:
    _escribir_json(RESUMEN_EVENTO_PATH, payload)


def cargar_resumen_evento() -> Dict[str, Any]:
    return _leer_json(RESUMEN_EVENTO_PATH, {})


# ---------------------------------------------------------------------------
# Health check / diagnóstico (Panel de Diagnóstico)
# ---------------------------------------------------------------------------

def verificar_integridad() -> Dict[str, Dict[str, Any]]:
    """
    Revisa que los archivos de datos clave existan y tengan una forma válida.
    Pensado para el botón "Verificar archivos" del Panel de Health Check.
    No lanza excepciones: cada resultado trae ok=True/False y un detalle legible.
    """
    resultados: Dict[str, Dict[str, Any]] = {}

    # event_data.json
    try:
        data = _leer_json(EVENT_DATA_PATH, None)
        if data is None:
            resultados["event_data.json"] = {"ok": False, "detalle": "Archivo no encontrado."}
        elif not isinstance(data.get("charlas"), list) or not data["charlas"]:
            resultados["event_data.json"] = {"ok": False, "detalle": "Falta la lista de charlas o está vacía."}
        else:
            resultados["event_data.json"] = {
                "ok": True,
                "detalle": f"{len(data['charlas'])} charlas cargadas correctamente.",
            }
    except Exception as e:
        resultados["event_data.json"] = {"ok": False, "detalle": f"Error al leer: {e}"}

    # registrants.json
    try:
        data = _leer_json(REGISTRANTS_PATH, None)
        if data is None:
            resultados["registrants.json"] = {
                "ok": True,
                "detalle": "Aún no existe (se genera al iniciar la app desde el CSV de Luma).",
            }
        elif not isinstance(data.get("asistentes"), list):
            resultados["registrants.json"] = {"ok": False, "detalle": "Formato inválido: falta la lista de asistentes."}
        else:
            resultados["registrants.json"] = {
                "ok": True,
                "detalle": f"{len(data['asistentes'])} asistentes registrados.",
            }
    except Exception as e:
        resultados["registrants.json"] = {"ok": False, "detalle": f"Error al leer: {e}"}

    # luma_registrants.csv (fuente original, útil detectar si falta antes del evento)
    resultados["luma_registrants.csv"] = (
        {"ok": True, "detalle": "Archivo presente."}
        if os.path.exists(LUMA_CSV_PATH)
        else {"ok": False, "detalle": "No se encontró el CSV de Luma en data/."}
    )

    # encuentros.json
    try:
        data = _leer_json(ENCUENTROS_PATH, None)
        if data is None:
            resultados["encuentros.json"] = {
                "ok": True,
                "detalle": "Aún no existe (se genera al pedir sugerencias de encuentro).",
            }
        else:
            resultados["encuentros.json"] = {
                "ok": True,
                "detalle": f"{len(data.get('grupos', []))} grupos sugeridos.",
            }
    except Exception as e:
        resultados["encuentros.json"] = {"ok": False, "detalle": f"Error al leer: {e}"}

    # mapa_charlistas.json
    try:
        data = _leer_json(MAPA_CHARLISTAS_PATH, None)
        if data is None:
            resultados["mapa_charlistas.json"] = {
                "ok": True,
                "detalle": "Aún no existe (se genera al pedir la vista por charlista).",
            }
        else:
            resultados["mapa_charlistas.json"] = {
                "ok": True,
                "detalle": f"{len(data.get('charlas', {}))} charlas con asistentes recomendados.",
            }
    except Exception as e:
        resultados["mapa_charlistas.json"] = {"ok": False, "detalle": f"Error al leer: {e}"}

    # preguntas_panel.json
    try:
        data = _leer_json(PREGUNTAS_PANEL_PATH, None)
        if data is None:
            resultados["preguntas_panel.json"] = {
                "ok": True,
                "detalle": "Aún no existe (se genera cuando alguien pregunta desde la vista pública).",
            }
        else:
            resultados["preguntas_panel.json"] = {
                "ok": True,
                "detalle": f"{len(data.get('preguntas', []))} preguntas recibidas.",
            }
    except Exception as e:
        resultados["preguntas_panel.json"] = {"ok": False, "detalle": f"Error al leer: {e}"}

    return resultados


# ---------------------------------------------------------------------------
# Encuentros entre asistentes (matchmaking persona-a-persona, espacio ASUS)
# ---------------------------------------------------------------------------

def guardar_encuentros(payload: Dict[str, Any]) -> None:
    _escribir_json(ENCUENTROS_PATH, payload)


def cargar_encuentros() -> Dict[str, Any]:
    return _leer_json(ENCUENTROS_PATH, {"generado_en": None, "grupos": []})


def guardar_mapa_charlistas(payload: Dict[str, Any]) -> None:
    _escribir_json(MAPA_CHARLISTAS_PATH, payload)


def cargar_mapa_charlistas() -> Dict[str, Any]:
    return _leer_json(MAPA_CHARLISTAS_PATH, {"generado_en": None, "charlas": {}})


# ---------------------------------------------------------------------------
# Preguntas para el panel (vista pública) — con triage presentación vs. panel
# ---------------------------------------------------------------------------

def cargar_preguntas_panel() -> Dict[str, Any]:
    return _leer_json(PREGUNTAS_PANEL_PATH, {"preguntas": []})


def agregar_pregunta_panel(nombre: str, pregunta: str, triage: Dict[str, Any]) -> Dict[str, Any]:
    """
    triage: el resultado de AgentClient.triage_pregunta_panel(), con al menos
    las claves 'resultado' ('posible_en_presentacion' | 'prioridad_panel') y
    'charla_relacionada' (id de charla o None).
    """
    data = cargar_preguntas_panel()
    data["preguntas"].append(
        {
            "nombre": nombre or "Anónimo",
            "pregunta": pregunta,
            "resultado": triage.get("resultado", "prioridad_panel"),
            "charla_relacionada_id": triage.get("charla_relacionada_id"),
            "charla_relacionada_titulo": triage.get("charla_relacionada_titulo", ""),
            "timestamp": timestamp_ahora(),
        }
    )
    _escribir_json(PREGUNTAS_PANEL_PATH, data)
    return data
