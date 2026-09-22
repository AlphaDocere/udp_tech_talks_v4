"""
Cliente para Jev (typesafe-ai/jev) vía Vercel AI Gateway.

Jev NO es un modelo generativo: evalúa un "estado" contra preguntas tipadas
(boolean, choice, score) y devuelve probabilidades/elecciones, no texto libre.
Por eso este cliente se usa solo para dos cosas puntuales:

1. elegir_charla(): un "choice" más robusto que el matching por palabras clave,
   para decidir qué charla recomendar (el texto de razonamiento/pregunta lo
   sigue generando AgentClient, con OpenRouter o en modo local).
2. moderar_texto(): un "boolean" para filtrar respuestas antes de mostrarlas
   en la pantalla pública de la Estación Estudiantes.

Es completamente opcional: si no hay AI_GATEWAY_API_KEY configurada, o si la
llamada falla, cada método devuelve None / True (fail-open) para no bloquear
la demo ni depender de esta pieza para que la app funcione.
"""

import os
from typing import Dict, Any, Optional, List

import requests

from src.models import Asistente, Charla

AI_GATEWAY_EVALUATE_URL = "https://ai-gateway.vercel.sh/v1/evaluate"
JEV_MODEL = "typesafe-ai/jev"


class JevClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("AI_GATEWAY_API_KEY", "")
        self.activo = bool(self.api_key)

    def _evaluate(self, state: Any, questions: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            return None
        try:
            resp = requests.post(
                AI_GATEWAY_EVALUATE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": JEV_MODEL, "state": state, "questions": questions},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("answers")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 1) Elegir charla (choice) — usado como primer paso del matchmaking
    # ------------------------------------------------------------------
    def elegir_charla(self, asistente: Asistente, charlas: List[Charla]) -> Optional[str]:
        """
        Devuelve el id de la charla elegida por Jev, o None si Jev no está
        disponible/activo (en ese caso, AgentClient sigue con su propia lógica).
        """
        if not self.activo or not charlas:
            return None

        criterios = {c.id: f"{c.titulo} — temas: {', '.join(c.tema_tags)}" for c in charlas}
        estado = {
            "motivo_visita": asistente.motivo_visita,
            "curiosidad": asistente.curiosidad,
            "rol_interes": asistente.rol_interes,
        }
        respuestas = self._evaluate(
            estado,
            {
                "charla": {
                    "type": "choice",
                    "instructions": "Elige la charla que mejor calza con los intereses de este asistente.",
                    "criteria": criterios,
                }
            },
        )
        if respuestas and "charla" in respuestas:
            return respuestas["charla"].get("choice")
        return None

    # ------------------------------------------------------------------
    # 2) Moderar texto (boolean) — usado en la Estación Estudiantes
    # ------------------------------------------------------------------
    def moderar_texto(self, texto: str) -> bool:
        """
        True si el texto es apropiado para mostrarse en una pantalla pública.
        Fail-open: si Jev no está activo o la llamada falla, deja pasar el
        texto (no queremos que un problema de red bloquee la estación).
        """
        if not self.activo or not texto.strip():
            return True

        respuestas = self._evaluate(
            texto,
            {
                "apropiado": {
                    "type": "boolean",
                    "instructions": (
                        "¿Es este texto apropiado y respetuoso para mostrarse en una "
                        "pantalla pública durante un evento universitario?"
                    ),
                    "criteria": {
                        "true": "respetuoso, en tema, sin insultos ni contenido ofensivo",
                        "false": "contiene insultos, odio, spam, datos sensibles o contenido inapropiado",
                    },
                }
            },
        )
        if respuestas and "apropiado" in respuestas:
            return respuestas["apropiado"].get("probability", 1.0) >= 0.5
        return True
