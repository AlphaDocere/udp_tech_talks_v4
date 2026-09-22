"""
UDP Tech Talks Vol. 1 — Vista PÚBLICA para asistentes (V4)
Ejecutar con:  streamlit run publico/app_publico.py

Pensada para abrirse desde el celular de cada asistente (por eso el QR de
entrada que se muestra en el panel de admin) o en una estación abierta al
público. No tiene clave: cualquiera puede entrar. No expone el pre-análisis
por lotes ni ninguna llamada masiva a la IA — esas quedan solo en el admin.

Secciones:
1. Bienvenida             -> info del evento
2. Mi Check-in            -> autoregistro (ánimo con iconos + motivo + curiosidad)
3. Agenda del Evento      -> charlas y charlistas
4. Mi Recomendación       -> lee (no genera) lo que el admin ya pre-analizó
5. ¿Con quién conectar?   -> lee (no genera) los encuentros ya sugeridos
6. Estación Estudiantes   -> pregunta ancla abierta, con moderación (Jev si está activo)
7. Pregunta para el Panel -> idea de Heileen Goodson: triage presentación vs. panel
8. Trabaja tu idea con IA -> idea de Heileen Goodson: prompting guiado de una idea
"""

import sys
import os

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ_PROYECTO)

from dotenv import load_dotenv

load_dotenv(os.path.join(RAIZ_PROYECTO, ".env"))

import streamlit as st

from src import storage
from src.agent_client import AgentClient
from src.jev_client import JevClient

ESTADOS_ANIMO = [
    ("😴", "Con sueño", 15),
    ("😐", "Neutral", 40),
    ("🙂", "Bien", 65),
    ("😃", "Con energía", 85),
    ("🤩", "A full", 100),
]

st.set_page_config(page_title="UDP Tech Talks Vol. 1", page_icon="🚀", layout="centered")

if "agente" not in st.session_state:
    st.session_state.agente = AgentClient()
if "jev" not in st.session_state:
    st.session_state.jev = JevClient()

agente: AgentClient = st.session_state.agente
jev: JevClient = st.session_state.jev
evento = storage.cargar_evento().get("evento", {})

st.title("🚀 " + evento.get("nombre", "UDP Tech Talks"))
st.caption(f"{evento.get('fecha', '')} · {evento.get('horario', '')} · {evento.get('lugar', '')}")

(
    tab_bienvenida,
    tab_checkin,
    tab_agenda,
    tab_recomendacion,
    tab_conectar,
    tab_estudiantes,
    tab_panel,
    tab_idea,
) = st.tabs(
    [
        "🪧 Bienvenida",
        "🎟️ Mi Check-in",
        "🗓️ Agenda",
        "🤖 Mi Recomendación",
        "🤝 ¿Con quién conectar?",
        "🎓 Estación Estudiantes",
        "❓ Pregunta al Panel",
        "💡 Trabaja tu idea",
    ]
)

# ---------------------------------------------------------------------------
# 1) Bienvenida
# ---------------------------------------------------------------------------
with tab_bienvenida:
    st.subheader("¡Bienvenida/o!")
    st.write(
        "Este espacio es tuyo durante el evento. Regístrate en **Mi Check-in**, revisa la "
        "**Agenda**, y en unos minutos podrás ver en **Mi Recomendación** qué charla te "
        "sugerimos según lo que nos cuentes — junto con quiénes más comparten tu interés."
    )
    st.info(
        "Ninguna recomendación es una verdad cerrada: es una hipótesis pensada para ayudarte "
        "a decidir, no para decidir por ti."
    )

# ---------------------------------------------------------------------------
# 2) Mi Check-in
# ---------------------------------------------------------------------------
with tab_checkin:
    st.subheader("Regístrate")
    asistentes = storage.cargar_registrants()
    opciones_luma = {a.guest_id: a.nombre for a in asistentes if a.origen == "luma" and not a.esta_checkeado()}

    modo = st.radio(
        "¿Ya te registraste en Luma?",
        ["Sí, busco mi nombre", "No, soy nuevo/a"],
        horizontal=True,
        key="modo_checkin_publico",
    )

    with st.form("form_checkin_publico", clear_on_submit=True):
        guest_id_sel = ""
        nombre_form = ""
        if modo == "Sí, busco mi nombre" and opciones_luma:
            guest_id_sel = st.selectbox(
                "Selecciona tu nombre",
                options=list(opciones_luma.keys()),
                format_func=lambda gid: opciones_luma[gid],
            )
        elif modo == "Sí, busco mi nombre" and not opciones_luma:
            st.info("No quedan nombres pendientes de Luma por buscar — puedes registrarte como nuevo/a.")
            nombre_form = st.text_input("Nombre completo")
        else:
            nombre_form = st.text_input("Nombre completo")

        opciones_animo = [f"{icono} {etiqueta}" for icono, etiqueta, _ in ESTADOS_ANIMO]
        animo_sel = st.radio("¿Cómo llegas hoy?", opciones_animo, index=2, horizontal=True, key="animo_publico")
        indice_animo = opciones_animo.index(animo_sel)
        icono_animo, etiqueta_animo, energia = ESTADOS_ANIMO[indice_animo]
        estado_animo_texto = f"{icono_animo} {etiqueta_animo}"

        motivo = st.text_input("¿Cuál es tu motivo de visita?", placeholder="Ej: conocer casos de IA aplicada")
        curiosidad = st.text_input(
            "Una curiosidad tuya o algo para hacer match", placeholder="Ej: me interesa la IA en salud"
        )

        enviado = st.form_submit_button("Registrar mi check-in", use_container_width=True)

    if enviado:
        nombre_final = opciones_luma.get(guest_id_sel, nombre_form)
        if not nombre_final:
            st.error("Por favor ingresa tu nombre.")
        else:
            storage.registrar_checkin(
                asistentes,
                nombre=nombre_final,
                energia_llegada=energia,
                motivo_visita=motivo,
                curiosidad=curiosidad,
                guest_id=guest_id_sel,
                estado_animo=estado_animo_texto,
            )
            st.success(f"¡Listo, {nombre_final}! {estado_animo_texto} Ya puedes ver la Agenda.")

# ---------------------------------------------------------------------------
# 3) Agenda del Evento
# ---------------------------------------------------------------------------
with tab_agenda:
    st.subheader("Agenda del evento")
    for charla in storage.cargar_charlas():
        with st.container(border=True):
            st.markdown(f"### {charla.titulo}")
            st.markdown(f"**{charla.charlista}** — {charla.rol}")
            st.caption(" · ".join(charla.tema_tags))

# ---------------------------------------------------------------------------
# 4) Mi Recomendación (solo lectura — el pre-análisis lo genera el admin)
# ---------------------------------------------------------------------------
with tab_recomendacion:
    st.subheader("Mi recomendación")
    st.caption("Escribe el nombre con el que hiciste check-in para ver tu recomendación.")
    nombre_busqueda = st.text_input("Tu nombre", key="buscar_recomendacion")

    if nombre_busqueda:
        asistentes = storage.cargar_registrants()
        coincidencias = [a for a in asistentes if nombre_busqueda.strip().lower() in a.nombre.lower()]

        if not coincidencias:
            st.warning("No te encontramos. Revisa que hayas hecho check-in en la pestaña anterior.")
        else:
            posibilidades = storage.cargar_posibilidades().get("resultados", {})
            charlas = storage.cargar_charlas()
            for a in coincidencias:
                mapa = posibilidades.get(a.guest_id)
                with st.container(border=True):
                    st.markdown(f"**{a.nombre}**")
                    if not a.esta_checkeado():
                        st.caption("Aún no completas tu check-in.")
                    elif not mapa:
                        st.caption("Tu recomendación todavía no está lista — vuelve a intentarlo en un rato.")
                    else:
                        charla_id = mapa.get("charla_recomendada_id")
                        charla = next((c for c in charlas if c.id == charla_id), None)
                        if charla:
                            st.success(f"Te recomendamos: **{charla.titulo}** — {charla.charlista}")
                        st.write(mapa.get("razonamiento", ""))
                        st.markdown(f"💡 {mapa.get('recomendacion_valor', '')}")
                        st.markdown(f"🎤 Pregunta que podrías hacer: _{mapa.get('pregunta_sugerida', '')}_")

# ---------------------------------------------------------------------------
# 5) ¿Con quién conectar? (solo lectura — el admin genera los grupos)
# ---------------------------------------------------------------------------
with tab_conectar:
    st.subheader("¿Con quién podrías conectar?")
    st.caption("Escribe tu nombre para ver si te sugerimos juntarte con alguien en el espacio ASUS.")
    nombre_busqueda_2 = st.text_input("Tu nombre", key="buscar_encuentro")

    if nombre_busqueda_2:
        grupos = storage.cargar_encuentros().get("grupos", [])
        encontrados = [
            g for g in grupos if any(nombre_busqueda_2.strip().lower() in m["nombre"].lower() for m in g["integrantes"])
        ]
        if not encontrados:
            st.info("Todavía no tienes sugerencias de encuentro. Puede que aún no se haya generado, o que nadie más comparta tu interés por ahora.")
        else:
            for g in encontrados:
                with st.container(border=True):
                    otros = [m["nombre"] for m in g["integrantes"] if nombre_busqueda_2.strip().lower() not in m["nombre"].lower()]
                    st.markdown(f"**Con:** {', '.join(otros)}")
                    st.caption(f"Interés en común: {g.get('tema_comun', '')}")
                    st.write(g.get("mensaje", ""))

# ---------------------------------------------------------------------------
# 6) Estación Estudiantes
# ---------------------------------------------------------------------------
with tab_estudiantes:
    respuestas_data = storage.cargar_respuestas_estudiantes()
    st.subheader("Estación abierta")
    st.markdown(f"### 💬 {respuestas_data['pregunta_ancla']}")

    with st.form("form_estudiante_publico", clear_on_submit=True):
        nombre_est = st.text_input("Tu nombre (opcional)", key="nombre_estudiante_publico")
        respuesta_est = st.text_area("Tu respuesta")
        enviar_est = st.form_submit_button("Enviar respuesta", use_container_width=True)

    if enviar_est and respuesta_est.strip():
        if jev.moderar_texto(respuesta_est):
            respuestas_data = storage.agregar_respuesta_estudiante(nombre_est, respuesta_est)
            st.success("¡Gracias por tu respuesta!")
        else:
            st.warning(
                "Tu respuesta no se pudo publicar en la pantalla pública (no pasó el filtro "
                "de contenido apropiado). Puedes intentar reformularla."
            )

    st.divider()
    st.subheader(f"Respuestas recibidas ({len(respuestas_data['respuestas'])})")
    for r in reversed(respuestas_data["respuestas"][-20:]):
        st.markdown(f"> {r['respuesta']}")
        st.caption(f"— {r['nombre']}")

# ---------------------------------------------------------------------------
# 7) Pregunta para el Panel (idea de Heileen Goodson)
# ---------------------------------------------------------------------------
with tab_panel:
    st.subheader("¿Tienes una pregunta para alguna charlista?")
    st.caption(
        "La revisamos: si calza con lo que ya se va a presentar, queda registrada igual; si no, "
        "la priorizamos para el panel en vivo."
    )
    with st.form("form_pregunta_panel", clear_on_submit=True):
        nombre_p = st.text_input("Tu nombre (opcional)", key="nombre_panel_publico")
        pregunta_p = st.text_area("Tu pregunta")
        enviar_p = st.form_submit_button("Enviar pregunta", use_container_width=True)

    if enviar_p and pregunta_p.strip():
        charlas = storage.cargar_charlas()
        triage = agente.triage_pregunta_panel(pregunta_p, charlas)
        storage.agregar_pregunta_panel(nombre_p, pregunta_p, triage)
        if triage["resultado"] == "prioridad_panel":
            st.success("¡Gracias! La marcamos como prioritaria para el panel en vivo. 🔴")
        else:
            st.success(f"¡Gracias! {triage['mensaje']} 🟢")

# ---------------------------------------------------------------------------
# 8) Trabaja tu idea con IA (idea de Heileen Goodson)
# ---------------------------------------------------------------------------
with tab_idea:
    st.subheader("Trabaja tu idea con IA")
    st.caption(
        "Escribe una idea suelta — la IA no te da la respuesta final, te devuelve una versión "
        "más concreta y un par de preguntas para que sigas tú desarrollándola (prompting)."
    )
    idea_input = st.text_area("Tu idea", placeholder="Ej: una app que ayude a...")

    if st.button("🤖 Trabajar mi idea", type="primary", use_container_width=True, disabled=not idea_input.strip()):
        with st.spinner("Afilando tu idea…"):
            resultado_idea = agente.trabajar_idea_con_ia(idea_input)
        st.session_state["ultima_idea"] = resultado_idea

    if "ultima_idea" in st.session_state:
        r = st.session_state["ultima_idea"]
        st.markdown(f"**Idea más concreta:** {r['idea_refinada']}")
        st.markdown("**Preguntas para seguir desarrollándola:**")
        for p in r["preguntas_seguimiento"]:
            st.write(f"- {p}")
        st.caption(f"Fuente: {r['fuente']} · Esto es un punto de partida, no la respuesta final.")
