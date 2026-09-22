"""
Cliente agéntico ("Curador Inteligente del Evento") — V3.

Novedades respecto a V2:
1. Cascada de modelos gratuitos de OpenRouter: prioriza NVIDIA Nemotron y, si un
   modelo responde 429 (saturado) o falla por cualquier motivo, prueba el
   siguiente automáticamente. Si TODA la cascada falla, cae al modo local (mock).
   IDs verificados en openrouter.ai al momento de escribir esto (pueden cambiar
   con el tiempo; ver Health Check para confirmar cuál responde hoy).
2. Pre-análisis por lotes (preanalizar_todos): procesa a todos los asistentes
   de una sola pasada y devuelve un dict {guest_id: mapa} listo para guardarse
   en data/posibilidades.json. Así, durante el evento, seleccionar a alguien en
   la Demo Agéntica es una lectura instantánea, no una llamada a la API en vivo.

Marco de gobernanza incorporado en el system prompt: el agente no reemplaza el
criterio humano, lo acompaña. Su recomendación siempre debe ser explicable y
cuestionable — "el problema real no es la IA, es delegar criterio sin notarlo".

Capacidades:
1. recomendar_charla(asistente, charlas)                     -> matchmaking individual
2. generar_mapa_posibilidades(asistente, charlas, recomendacion) -> grafo JSON
3. preanalizar_todos(asistentes, charlas, jev_client, progreso_callback) -> lote completo
4. sugerir_encuentros(asistentes, posibilidades, charlas, progreso_callback) -> matchmaking persona-a-persona
5. generar_resumen_evento(asistentes, respuestas_estudiantes, historial_matches) -> noticia de cierre
6. probar_conexion()                                          -> Panel de Health Check
"""

import json
import os
import re
from collections import defaultdict
from typing import List, Dict, Any, Optional, Callable

import requests

from src.models import Asistente, Charla

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Cascada de modelos gratuitos, en orden de preferencia. Se prueba el primero;
# si responde 429 o falla, se prueba el siguiente; si todos fallan, se usa el
# modo local (mock). "openrouter/free" al final es el router automático de
# OpenRouter: elige cualquier modelo gratuito disponible en ese momento, así
# que funciona como red de seguridad final antes del modo local.
MODELOS_CASCADA_DEFECTO = [
    "nvidia/nemotron-3-super-120b-a12b:free",  # NVIDIA Nemotron 3 Super — prioridad 1
    "nvidia/nemotron-3-ultra-550b-a55b:free",  # NVIDIA Nemotron 3 Ultra — respaldo NVIDIA
    "qwen/qwen3-235b-a22b:free",  # Qwen — respaldo de otro proveedor
    "deepseek/deepseek-chat-v3.1:free",  # DeepSeek — segundo respaldo de otro proveedor
    "openrouter/free",  # Router automático de OpenRouter — red de seguridad final
]

MARCO_GOBERNANZA = (
    "Marco de gobernanza que debes respetar siempre: el problema real no es la IA, "
    "es delegar criterio humano sin notarlo. Nunca entregues una recomendación como "
    "verdad cerrada: preséntala como una hipótesis razonada, explica en qué datos te "
    "basas, y deja explícito que la persona asistente puede (y debería) cuestionarla. "
    "Evita la automatización ciega del juicio: tu rol es ampliar el criterio de la "
    "persona, no reemplazarlo."
)

# Palabras demasiado genéricas para contar como "interés en común" entre dos personas
# (conectores, verbos de relleno, palabras del propio formulario de check-in).
PALABRAS_VACIAS = {
    "quiero", "interesa", "interesan", "interesado", "interesada", "conocer",
    "curiosidad", "motivo", "tambien", "también", "sobre", "como", "para", "desde",
    "este", "esta", "estos", "estas", "tema", "cosas", "poder", "tener", "hacer",
    "evento", "aplicada", "aplicados", "aplicado", "pega", "trabajo", "gente",
    "algo", "todo", "todos", "todas", "mucho", "mucha", "gustar", "gusta",
}


def _construir_cascada(modelo_preferido: Optional[str]) -> List[str]:
    """
    Si OPENROUTER_MODEL trae un modelo específico, se antepone a la cascada por
    defecto (sin duplicarlo si ya estuviera en la lista).
    """
    cascada = list(MODELOS_CASCADA_DEFECTO)
    if modelo_preferido and modelo_preferido not in cascada:
        cascada.insert(0, modelo_preferido)
    return cascada


class AgentClient:
    def __init__(self, api_key: Optional[str] = None, modelos: Optional[List[str]] = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.modelos = modelos or _construir_cascada(os.environ.get("OPENROUTER_MODEL"))
        self.model = self.modelos[0]  # modelo "principal" mostrado en la UI (compatibilidad)
        self.modo_mock = not bool(self.api_key)
        self.ultimo_modelo_usado: Optional[str] = None

    # ------------------------------------------------------------------
    # Llamada base al modelo, recorriendo la cascada con fallback automático
    # ------------------------------------------------------------------
    def _chat(self, system_prompt: str, user_prompt: str, esperar_json: bool = True) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            return None

        for modelo in self.modelos:
            try:
                resp = requests.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": modelo,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.7,
                    },
                    timeout=20,
                )
                if resp.status_code == 429:
                    # Modelo saturado/rate-limited: seguir con el siguiente de la cascada.
                    continue
                resp.raise_for_status()
                contenido = resp.json()["choices"][0]["message"]["content"]
                if esperar_json:
                    contenido_limpio = re.sub(
                        r"^```json|```$", "", contenido.strip(), flags=re.MULTILINE
                    ).strip()
                    resultado = json.loads(contenido_limpio)
                else:
                    resultado = {"texto": contenido}
                self.ultimo_modelo_usado = modelo
                return resultado
            except Exception:
                # Error de red, timeout, JSON mal formado, modelo no disponible, etc.
                # -> se prueba el siguiente modelo de la cascada.
                continue

        self.ultimo_modelo_usado = None
        return None

    # ------------------------------------------------------------------
    # 1) Matchmaking asistente <-> charla (individual)
    # ------------------------------------------------------------------
    def recomendar_charla(
        self, asistente: Asistente, charlas: List[Charla], charla_id_sugerida: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        charla_id_sugerida: si viene (ej. desde JevClient.elegir_charla), se le pide
        al modelo generativo que respete esa elección y solo redacte el razonamiento,
        la recomendación de valor y la pregunta reflexiva alrededor de ella.
        """
        sugerencia_texto = (
            f" Ya se determinó mediante un modelo de clasificación que la charla más "
            f"adecuada es '{charla_id_sugerida}'; usa ese charla_id y concéntrate en "
            f"redactar bien el razonamiento, la recomendación de valor y la pregunta."
            if charla_id_sugerida
            else ""
        )
        system_prompt = (
            "Eres el Curador Inteligente del evento 'UDP Tech Talks Vol. 1: Mujeres "
            "construyendo el futuro de la tecnología'. Recibes el perfil de una persona "
            "asistente y la lista de charlas disponibles. " + MARCO_GOBERNANZA + sugerencia_texto + " "
            "Elige la charla que mejor calce con su motivo de visita y curiosidad, explica "
            "tu razonamiento en 2-3 frases, añade una recomendación de valor concreta (qué "
            "se llevaría de esa charla, no solo el título), y redacta una pregunta *reflexiva* "
            "que esa persona podría hacerle a la charlista, que invite a pensar y no solo a "
            "confirmar lo obvio. Responde SOLO con un JSON válido con las claves: charla_id, "
            "razonamiento, recomendacion_valor, pregunta_sugerida."
        )
        user_prompt = json.dumps(
            {
                "asistente": {
                    "nombre": asistente.nombre,
                    "motivo_visita": asistente.motivo_visita,
                    "curiosidad": asistente.curiosidad,
                    "rol_interes": asistente.rol_interes,
                    "energia_llegada": asistente.energia_llegada,
                },
                "charlas": [c.to_dict() for c in charlas],
            },
            ensure_ascii=False,
        )

        resultado = self._chat(system_prompt, user_prompt, esperar_json=True)
        if resultado and "charla_id" in resultado:
            charla_id_final = charla_id_sugerida or resultado["charla_id"]
            charla = next((c for c in charlas if c.id == charla_id_final), charlas[0] if charlas else None)
            return {
                "charla": charla,
                "razonamiento": resultado.get("razonamiento", ""),
                "recomendacion_valor": resultado.get("recomendacion_valor", ""),
                "pregunta_sugerida": resultado.get("pregunta_sugerida", ""),
                "fuente": f"openrouter:{self.ultimo_modelo_usado}" + (" + jev" if charla_id_sugerida else ""),
            }

        recomendacion_mock = self._recomendar_charla_mock(asistente, charlas)
        if charla_id_sugerida:
            charla_jev = next((c for c in charlas if c.id == charla_id_sugerida), None)
            if charla_jev:
                recomendacion_mock["charla"] = charla_jev
                recomendacion_mock["fuente"] = "jev + mock_local"
        return recomendacion_mock

    def _recomendar_charla_mock(self, asistente: Asistente, charlas: List[Charla]) -> Dict[str, Any]:
        """Matchmaking local por coincidencia de palabras clave, sin depender de internet."""
        texto_asistente = f"{asistente.motivo_visita} {asistente.curiosidad} {asistente.rol_interes}".lower()
        mejor_charla = None
        mejor_score = -1
        for charla in charlas:
            score = sum(1 for tag in charla.tema_tags if tag in texto_asistente)
            score += sum(1 for palabra in charla.titulo.lower().split() if palabra in texto_asistente)
            if score > mejor_score:
                mejor_score = score
                mejor_charla = charla

        if mejor_charla is None and charlas:
            mejor_charla = charlas[0]

        razonamiento = (
            f"Basado en tus palabras clave ('{asistente.motivo_visita}', '{asistente.curiosidad}'), "
            f"la charla de {mejor_charla.charlista} sobre '{mejor_charla.titulo}' es la que más se "
            f"conecta con tu interés (modo local, sin conexión al modelo). Tómalo como punto de "
            f"partida, no como respuesta cerrada: tú decides si te hace sentido."
            if mejor_charla
            else "No hay charlas cargadas para recomendar."
        )
        recomendacion_valor = (
            f"De esta charla te puedes llevar una forma concreta de aplicar '{mejor_charla.titulo}' "
            f"a lo que mencionaste como motivo de visita."
            if mejor_charla
            else ""
        )
        pregunta = (
            f"¿Qué señal usarías para saber que '{mejor_charla.titulo}' realmente funciona en la práctica, "
            f"y no solo en la teoría?"
            if mejor_charla
            else ""
        )
        return {
            "charla": mejor_charla,
            "razonamiento": razonamiento,
            "recomendacion_valor": recomendacion_valor,
            "pregunta_sugerida": pregunta,
            "fuente": "mock_local",
        }

    # ------------------------------------------------------------------
    # 2) Mapa de posibilidades (grafo JSON para visualizar)
    # ------------------------------------------------------------------
    def generar_mapa_posibilidades(
        self, asistente: Asistente, charlas: List[Charla], recomendacion: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Genera un JSON tipo grafo (nodos + conexiones) que representa al asistente,
        las charlas, y palabras clave detectadas, con un peso de afinidad por conexión.
        No depende del modelo remoto: se calcula localmente a partir del mismo texto
        usado en el matchmaking, así siempre es consistente con la recomendación.
        """
        texto_asistente = f"{asistente.motivo_visita} {asistente.curiosidad}".lower()
        palabras = sorted(set(re.findall(r"[a-záéíóúñ]{4,}", texto_asistente)))[:8]

        nodos = [{"id": "asistente", "label": asistente.nombre or "Asistente", "tipo": "asistente"}]
        conexiones = []

        for charla in charlas:
            score = sum(1 for tag in charla.tema_tags if tag in texto_asistente)
            peso = round(min(1.0, 0.25 + 0.25 * score), 2)
            nodos.append({"id": charla.id, "label": charla.titulo, "tipo": "charla"})
            if score > 0 or (recomendacion.get("charla") and recomendacion["charla"].id == charla.id):
                conexiones.append({"origen": "asistente", "destino": charla.id, "peso": peso})

        for palabra in palabras:
            nodo_id = f"palabra-{palabra}"
            nodos.append({"id": nodo_id, "label": palabra, "tipo": "palabra"})
            conexiones.append({"origen": "asistente", "destino": nodo_id, "peso": 0.5})

        payload = {
            "asistente_id": asistente.guest_id,
            "asistente_nombre": asistente.nombre,
            "charla_recomendada_id": recomendacion["charla"].id if recomendacion.get("charla") else None,
            "razonamiento": recomendacion.get("razonamiento", ""),
            "recomendacion_valor": recomendacion.get("recomendacion_valor", ""),
            "pregunta_sugerida": recomendacion.get("pregunta_sugerida", ""),
            "fuente": recomendacion.get("fuente", ""),
            "mapa": {"nodos": nodos, "conexiones": conexiones},
        }
        return payload

    # ------------------------------------------------------------------
    # 3) Pre-análisis por lotes: procesa a todos los asistentes de una vez
    # ------------------------------------------------------------------
    def preanalizar_todos(
        self,
        asistentes: List[Asistente],
        charlas: List[Charla],
        jev_client: Optional[Any] = None,
        progreso_callback: Optional[Callable[[int, int, Asistente], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Recorre TODOS los asistentes recibidos (normalmente los que ya hicieron
        check-in) y genera, para cada uno, su recomendación + mapa de posibilidades.

        progreso_callback(indice_actual, total, asistente) se invoca antes de procesar
        a cada persona — pensado para actualizar un st.status() en la interfaz.

        Devuelve {guest_id: mapa_dict}, listo para guardarse tal cual en
        data/posibilidades.json (envuelto en {"generado_en": ..., "resultados": ...}).
        """
        resultados: Dict[str, Dict[str, Any]] = {}
        total = len(asistentes)
        for i, asistente in enumerate(asistentes, start=1):
            if progreso_callback:
                progreso_callback(i, total, asistente)

            charla_id_jev = jev_client.elegir_charla(asistente, charlas) if jev_client else None
            recomendacion = self.recomendar_charla(asistente, charlas, charla_id_sugerida=charla_id_jev)
            mapa = self.generar_mapa_posibilidades(asistente, charlas, recomendacion)
            resultados[asistente.guest_id] = mapa

        return resultados

    # ------------------------------------------------------------------
    # 4) Matchmaking persona-a-persona: sugerir encuentros en el espacio ASUS
    # ------------------------------------------------------------------
    def sugerir_encuentros(
        self,
        asistentes: List[Asistente],
        posibilidades: Dict[str, Dict[str, Any]],
        charlas: List[Charla],
        progreso_callback: Optional[Callable[[int, int, Optional[Charla], List[Asistente]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Agrupa a los asistentes con check-in que el pre-análisis ya recomendó para
        la MISMA charla (señal de interés compartido, ya calculada — no se repite
        el análisis) y redacta, por grupo, una invitación breve a conversar en el
        espacio ASUS. Requiere que 'posibilidades' ya tenga datos (correr primero
        el Pre-Análisis Global); si un asistente no está ahí, simplemente no entra
        a ningún grupo.

        Trabaja por GRUPOS, no por pares uno a uno: como máximo una llamada al
        modelo por charla en común (normalmente ≤5), en vez de una por cada par
        de personas — así escala bien aunque haya muchos asistentes.
        """
        grupos_por_charla: Dict[str, List[Asistente]] = defaultdict(list)
        for asistente in asistentes:
            mapa = posibilidades.get(asistente.guest_id)
            if not mapa:
                continue
            charla_id = mapa.get("charla_recomendada_id")
            if charla_id:
                grupos_por_charla[charla_id].append(asistente)

        clusters_validos = [(cid, miembros) for cid, miembros in grupos_por_charla.items() if len(miembros) >= 2]
        sugerencias = []
        total = len(clusters_validos)
        for i, (charla_id, miembros) in enumerate(clusters_validos, start=1):
            charla = next((c for c in charlas if c.id == charla_id), None)
            if progreso_callback:
                progreso_callback(i, total, charla, miembros)
            sugerencias.append(self._redactar_sugerencia_encuentro(charla, miembros))

        return sugerencias

    def _redactar_sugerencia_encuentro(self, charla: Optional[Charla], miembros: List[Asistente]) -> Dict[str, Any]:
        nombres = [m.nombre for m in miembros]
        titulo_charla = charla.titulo if charla else "un interés en común"

        system_prompt = (
            "Eres el Curador Inteligente del evento 'UDP Tech Talks Vol. 1: Mujeres "
            "construyendo el futuro de la tecnología'. " + MARCO_GOBERNANZA + " "
            "Recibes un grupo de asistentes a quienes el sistema ya identificó con un interés "
            "en común (les recomendó la misma charla). Redacta una invitación breve y cálida "
            "(máximo 2 frases) para que se junten a conversar en el espacio de ASUS del evento, "
            "mencionando qué podrían tener en común sin inventar detalles que no te dieron. "
            "Deja claro que es una sugerencia, no una obligación. Responde SOLO con un JSON "
            "válido con las claves: mensaje, tema_comun."
        )
        user_prompt = json.dumps(
            {
                "charla_en_comun": titulo_charla,
                "integrantes": [
                    {"nombre": m.nombre, "motivo_visita": m.motivo_visita, "curiosidad": m.curiosidad}
                    for m in miembros
                ],
            },
            ensure_ascii=False,
        )

        resultado = self._chat(system_prompt, user_prompt, esperar_json=True)
        if resultado and "mensaje" in resultado:
            fuente = f"openrouter:{self.ultimo_modelo_usado}"
            mensaje = resultado.get("mensaje", "")
            tema_comun = resultado.get("tema_comun", titulo_charla)
        else:
            fuente = "mock_local"
            tema_comun = titulo_charla
            mensaje = (
                f"{', '.join(nombres)}: a todos les recomendamos '{titulo_charla}' — podría ser un "
                f"buen tema para romper el hielo si se juntan un rato en el espacio ASUS "
                f"(sugerencia del sistema, no una obligación)."
            )

        return {
            "charla_id": charla.id if charla else None,
            "charla_titulo": titulo_charla,
            "integrantes": [{"guest_id": m.guest_id, "nombre": m.nombre} for m in miembros],
            "tema_comun": tema_comun,
            "mensaje": mensaje,
            "fuente": fuente,
        }

    # ------------------------------------------------------------------
    # 4.5) Vista por charlista: quiénes le fueron recomendados y qué los conecta
    # ------------------------------------------------------------------
    def generar_vista_charlistas(
        self,
        asistentes: List[Asistente],
        posibilidades: Dict[str, Dict[str, Any]],
        charlas: List[Charla],
    ) -> Dict[str, Dict[str, Any]]:
        """
        Reorganiza el pre-análisis ya calculado (posibilidades) desde el punto de
        vista de cada charlista: a quién le recomendaron su charla, con qué
        motivo/curiosidad llegaron, y qué palabras clave comparten entre sí (lo
        que arma las conexiones visibles del mapa). No llama a ningún modelo:
        es una transformación pura de datos ya generados, así que es instantánea.
        """
        asistentes_por_id = {a.guest_id: a for a in asistentes}
        vista: Dict[str, Dict[str, Any]] = {}

        for charla in charlas:
            miembros_ids = [
                gid for gid, mapa in posibilidades.items() if mapa.get("charla_recomendada_id") == charla.id
            ]
            miembros = [asistentes_por_id[gid] for gid in miembros_ids if gid in asistentes_por_id]
            if not miembros:
                continue

            palabras_por_asistente: Dict[str, set] = {}
            for m in miembros:
                texto = f"{m.motivo_visita} {m.curiosidad}".lower()
                palabras = set(re.findall(r"[a-záéíóúñ]{4,}", texto)) - PALABRAS_VACIAS
                palabras_por_asistente[m.guest_id] = palabras

            nodos = [{"id": "charla", "label": charla.titulo, "tipo": "charla"}]
            conexiones = []
            for m in miembros:
                nodos.append({"id": m.guest_id, "label": m.nombre, "tipo": "asistente"})
                conexiones.append({"origen": m.guest_id, "destino": "charla", "peso": 1.0})

            contador_palabra: Dict[str, List[str]] = defaultdict(list)
            for gid, palabras in palabras_por_asistente.items():
                for p in palabras:
                    contador_palabra[p].append(gid)

            conexiones_internas = []
            for palabra, gids in contador_palabra.items():
                if len(gids) < 2:
                    continue
                nodo_id = f"palabra-{palabra}"
                nodos.append({"id": nodo_id, "label": palabra, "tipo": "palabra"})
                for gid in gids:
                    conexiones.append({"origen": gid, "destino": nodo_id, "peso": 0.6})
                conexiones_internas.append(
                    {"palabra": palabra, "integrantes": [asistentes_por_id[g].nombre for g in gids]}
                )

            vista[charla.id] = {
                "charla_id": charla.id,
                "titulo": charla.titulo,
                "charlista": charla.charlista,
                "asistentes": [
                    {
                        "guest_id": m.guest_id,
                        "nombre": m.nombre,
                        "motivo_visita": m.motivo_visita,
                        "curiosidad": m.curiosidad,
                    }
                    for m in miembros
                ],
                "conexiones_internas": conexiones_internas,
                "mapa": {"nodos": nodos, "conexiones": conexiones},
            }

        return vista

    # ------------------------------------------------------------------
    # 4.6) Triage de preguntas para el panel (¿ya está en la presentación,
    #      o hay que priorizarla para el panel en vivo?). No usa IA: es
    #      determinístico y rápido, pensado para la vista pública.
    # ------------------------------------------------------------------
    def triage_pregunta_panel(self, pregunta: str, charlas: List[Charla]) -> Dict[str, Any]:
        texto_pregunta = pregunta.lower()
        palabras_pregunta = set(re.findall(r"[a-záéíóúñ]{4,}", texto_pregunta)) - PALABRAS_VACIAS

        mejor_charla = None
        mejor_score = 0
        for charla in charlas:
            texto_charla = f"{charla.titulo} {' '.join(charla.tema_tags)}".lower()
            palabras_charla = set(re.findall(r"[a-záéíóúñ]{4,}", texto_charla))
            score = len(palabras_pregunta & palabras_charla)
            if score > mejor_score:
                mejor_score = score
                mejor_charla = charla

        if mejor_charla and mejor_score > 0:
            return {
                "resultado": "posible_en_presentacion",
                "charla_relacionada_id": mejor_charla.id,
                "charla_relacionada_titulo": mejor_charla.titulo,
                "mensaje": (
                    f"Es posible que esto ya se toque en '{mejor_charla.titulo}' — igual queda "
                    f"registrada por si quieres profundizarla con la charlista."
                ),
            }

        return {
            "resultado": "prioridad_panel",
            "charla_relacionada_id": None,
            "charla_relacionada_titulo": "",
            "mensaje": "No calza claramente con ninguna charla — se marca como prioritaria para el panel en vivo.",
        }

    # ------------------------------------------------------------------
    # 4.7) Trabajar una idea con IA (prompting guiado, vista pública)
    # ------------------------------------------------------------------
    def trabajar_idea_con_ia(self, idea: str) -> Dict[str, Any]:
        """
        Toma una idea suelta que escribe un/a asistente y la devuelve reformulada
        de forma más concreta, junto con 2-3 preguntas de prompting para seguir
        desarrollándola. Pensado como ejercicio breve de la vista pública, no
        como una respuesta definitiva (coherente con el marco de gobernanza).
        """
        system_prompt = (
            "Eres un facilitador que ayuda a alguien en un evento de tecnología a afilar una "
            "idea suelta usando el pensamiento de prompting (partir de una idea vaga y hacerla "
            "concreta a través de preguntas). " + MARCO_GOBERNANZA + " "
            "Recibes una idea corta. Redacta: una versión más concreta de la idea (1-2 frases), "
            "y 2 o 3 preguntas de seguimiento que la persona podría hacerse (o hacerle a una IA) "
            "para seguir desarrollándola. No la resuelvas por completo — el objetivo es que seas "
            "un punto de partida, no la respuesta final. Responde SOLO con un JSON válido con las "
            "claves: idea_refinada, preguntas_seguimiento (lista de strings)."
        )
        resultado = self._chat(system_prompt, json.dumps({"idea": idea}, ensure_ascii=False), esperar_json=True)
        if resultado and "idea_refinada" in resultado:
            return {
                "idea_original": idea,
                "idea_refinada": resultado.get("idea_refinada", ""),
                "preguntas_seguimiento": resultado.get("preguntas_seguimiento", []),
                "fuente": f"openrouter:{self.ultimo_modelo_usado}",
            }

        return {
            "idea_original": idea,
            "idea_refinada": f"'{idea}' — ¿para quién es esto, específicamente, y qué problema puntual resuelve?",
            "preguntas_seguimiento": [
                "¿Qué pasaría si tuvieras que explicarlo en una sola frase a alguien que no sabe nada del tema?",
                "¿Cuál sería la versión más simple posible de esto que igual sea útil?",
                "¿Qué dato o evidencia te haría cambiar de opinión sobre esta idea?",
            ],
            "fuente": "mock_local",
        }

    # ------------------------------------------------------------------
    # 5) Resumen / noticia de cierre de jornada
    # ------------------------------------------------------------------
    def generar_resumen_evento(
        self,
        asistentes: List[Asistente],
        respuestas_estudiantes: Dict[str, Any],
        historial_matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        checkeados = [a for a in asistentes if a.esta_checkeado()]
        energias = [a.energia_llegada for a in checkeados if a.energia_llegada is not None]
        energia_promedio = round(sum(energias) / len(energias), 1) if energias else None

        system_prompt = (
            "Eres un/a periodista cubriendo el evento 'UDP Tech Talks Vol. 1: Mujeres "
            "construyendo el futuro de la tecnología'. " + MARCO_GOBERNANZA + " "
            "Recibes datos agregados del día (asistencia, energía de llegada, motivos de "
            "visita, matches generados y respuestas a la pregunta ancla sobre educación de "
            "comunidades dev en 2027). Redacta una breve nota de cierre de jornada, cálida y "
            "concreta, de no más de 150 palabras. Responde SOLO con un JSON con las claves: "
            "titular, cuerpo."
        )
        user_prompt = json.dumps(
            {
                "total_asistentes": len(checkeados),
                "energia_promedio": energia_promedio,
                "motivos_visita": [a.motivo_visita for a in checkeados if a.motivo_visita][:20],
                "matches_generados": len(historial_matches),
                "pregunta_ancla": respuestas_estudiantes.get("pregunta_ancla", ""),
                "respuestas_estudiantes": [
                    r["respuesta"] for r in respuestas_estudiantes.get("respuestas", [])
                ][:20],
            },
            ensure_ascii=False,
        )

        resultado = self._chat(system_prompt, user_prompt, esperar_json=True)
        if resultado and "cuerpo" in resultado:
            resultado["fuente"] = f"openrouter:{self.ultimo_modelo_usado}"
            return resultado

        return self._generar_resumen_mock(checkeados, energia_promedio, respuestas_estudiantes, historial_matches)

    def _generar_resumen_mock(
        self,
        checkeados: List[Asistente],
        energia_promedio: Optional[float],
        respuestas_estudiantes: Dict[str, Any],
        historial_matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        n_respuestas = len(respuestas_estudiantes.get("respuestas", []))
        cuerpo = (
            f"Hoy pasaron por la estación {len(checkeados)} personas, con una energía de "
            f"llegada promedio de {energia_promedio if energia_promedio is not None else 's/d'}%. "
            f"El agente generó {len(historial_matches)} recomendaciones de charlas personalizadas "
            f"y se recogieron {n_respuestas} respuestas a la pregunta '"
            f"{respuestas_estudiantes.get('pregunta_ancla', '')}'. Una jornada donde la conversación "
            f"entre datos, IA y comunidad quedó, una vez más, en manos de quienes la construyen "
            f"(resumen generado en modo local, sin conexión al modelo)."
        )
        return {
            "titular": "UDP Tech Talks Vol. 1: así vivimos la jornada",
            "cuerpo": cuerpo,
            "fuente": "mock_local",
        }

    # ------------------------------------------------------------------
    # 6) Health check de conectividad (Panel de Diagnóstico)
    # ------------------------------------------------------------------
    def probar_conexion(self) -> Dict[str, Any]:
        """
        Prueba rápida y barata (max_tokens=5) recorriendo la cascada de modelos,
        para verificar cuál responde hoy sin gastar el flujo de matchmaking.
        """
        if not self.api_key:
            return {
                "ok": False,
                "mensaje": "No hay OPENROUTER_API_KEY configurada — la app seguirá funcionando en modo local.",
            }
        for modelo in self.modelos:
            try:
                resp = requests.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": modelo,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 5,
                    },
                    timeout=10,
                )
                if resp.status_code == 429:
                    continue
                resp.raise_for_status()
                return {"ok": True, "mensaje": f"Conexión exitosa con '{modelo}'."}
            except Exception:
                continue
        return {
            "ok": False,
            "mensaje": "Ningún modelo de la cascada respondió (saturados o con error) — se usará el modo local.",
        }
