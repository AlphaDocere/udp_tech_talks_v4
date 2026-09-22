"""
UDP Tech Talks Vol. 1 — Panel de ADMINISTRACIÓN (V4)
Ejecutar con:  streamlit run src/app.py

Este es el panel operado por el equipo del evento (protegido con clave). Las
vistas para que el asistente interactúe por su cuenta viven en publico/app_publico.py.

Pestañas:
1. Check-in y Asistentes -> registro en puerta (ánimo con iconos + motivo + curiosidad) + tabla en vivo
2. Agenda del Evento      -> charlas y charlistas del evento
3. Demo Agéntica          -> pre-análisis por lotes: matchmaking asistente <-> charla + mapa
4. Encuentros ASUS        -> matchmaking asistente <-> asistente + vista por charlista
5. Preguntas al Panel     -> preguntas enviadas desde la vista pública, con triage
6. Estación Estudiantes   -> pregunta ancla abierta
7. Cierre de Jornada      -> resumen/noticia generado por IA al final del día
8. Cómo funciona          -> esquema visual del proceso + QR de acceso a la vista pública
9. Health Check           -> diagnóstico de archivos y conectividad con OpenRouter/Jev
"""

import sys
import os
import io

# Insertamos la raíz del proyecto (padre de src/) para poder hacer `from src import ...`
# sin importar desde qué carpeta se ejecute `streamlit run`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Carga las variables de OPENROUTER_API_KEY / OPENROUTER_MODEL / ADMIN_PASSWORD /
# PUBLIC_APP_URL desde un archivo .env en la raíz del proyecto, si existe. Si no
# existe, no falla: sigue usando variables de entorno del sistema (o defaults).
RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(RAIZ_PROYECTO, ".env"))

import streamlit as st
import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt

from src import storage
from src.agent_client import AgentClient
from src.jev_client import JevClient
from src.diagrama import figura_flujo_matchmaking, figura_flujo_cierre
from src.models import timestamp_ahora

st.set_page_config(page_title="UDP Tech Talks Vol. 1 — Admin", page_icon="🛠️", layout="wide")

# ---------------------------------------------------------------------------
# Gate de acceso: clave genérica para el panel de administración.
# Si no se configura ADMIN_PASSWORD, el panel queda abierto (útil en desarrollo).
# No es un sistema de autenticación real — solo evita que cualquiera que abra
# la URL del Chromebox entre directo al panel de control del evento.
# ---------------------------------------------------------------------------
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

if ADMIN_PASSWORD and not st.session_state.get("admin_autenticado", False):
    st.title("🛠️ Acceso administración — UDP Tech Talks Vol. 1")
    st.caption("Este panel es solo para el equipo del evento. Los asistentes usan la vista pública.")
    clave_ingresada = st.text_input("Clave de administración", type="password")
    if st.button("Entrar", type="primary"):
        if clave_ingresada == ADMIN_PASSWORD:
            st.session_state.admin_autenticado = True
            st.rerun()
        else:
            st.error("Clave incorrecta.")
    st.stop()


# Estados de ánimo seleccionables en el check-in (icono, etiqueta, energía asociada 0-100).
ESTADOS_ANIMO = [
    ("😴", "Con sueño", 15),
    ("😐", "Neutral", 40),
    ("🙂", "Bien", 65),
    ("😃", "Con energía", 85),
    ("🤩", "A full", 100),
]

# ---------------------------------------------------------------------------
# Estado inicial (se carga una sola vez por sesión)
# ---------------------------------------------------------------------------
if "asistentes" not in st.session_state:
    st.session_state.asistentes = storage.cargar_registrants()
if "charlas" not in st.session_state:
    st.session_state.charlas = storage.cargar_charlas()
if "evento" not in st.session_state:
    st.session_state.evento = storage.cargar_evento().get("evento", {})
if "agente" not in st.session_state:
    st.session_state.agente = AgentClient()
if "jev" not in st.session_state:
    st.session_state.jev = JevClient()
if "posibilidades" not in st.session_state:
    # Se carga lo que ya estuviera guardado en disco de una sesión anterior.
    st.session_state.posibilidades = storage.cargar_posibilidades().get("resultados", {})
if "generando_preanalisis" not in st.session_state:
    st.session_state.generando_preanalisis = False
if "encuentros" not in st.session_state:
    st.session_state.encuentros = storage.cargar_encuentros().get("grupos", [])
if "generando_encuentros" not in st.session_state:
    st.session_state.generando_encuentros = False
if "vista_charlistas" not in st.session_state:
    st.session_state.vista_charlistas = storage.cargar_mapa_charlistas().get("charlas", {})

evento = st.session_state.evento
agente: AgentClient = st.session_state.agente
jev: JevClient = st.session_state.jev

# ---------------------------------------------------------------------------
# Encabezado
# ---------------------------------------------------------------------------
st.title("🚀 " + evento.get("nombre", "UDP Tech Talks"))
col_a, col_b, col_c = st.columns(3)
col_a.metric("📅 Fecha", evento.get("fecha", "-"))
col_b.metric("🕐 Horario", evento.get("horario", "-"))
col_c.metric("📍 Lugar", evento.get("lugar", "-"))

if agente.modo_mock:
    st.info(
        "🔌 Corriendo en **modo local (sin API key de OpenRouter)**: el matchmaking y el "
        "resumen se generan con lógica local, no con el modelo remoto. Define la variable "
        "de entorno `OPENROUTER_API_KEY` para activar el modo con IA real.",
        icon="ℹ️",
    )

tab_checkin, tab_programa, tab_demo, tab_encuentros, tab_preguntas, tab_estudiantes, tab_cierre, tab_esquema, tab_health = st.tabs(
    [
        "🎟️ Check-in y Asistentes",
        "🗓️ Agenda del Evento",
        "🤖 Demo Agéntica",
        "🤝 Encuentros ASUS",
        "❓ Preguntas al Panel",
        "🎓 Estación Estudiantes",
        "📰 Cierre de Jornada",
        "🗺️ Cómo funciona",
        "🩺 Health Check",
    ]
)

# ---------------------------------------------------------------------------
# Pestaña 1: Check-in y Asistentes
# ---------------------------------------------------------------------------
with tab_checkin:
    st.subheader("Registro en puerta")
    st.caption("3 preguntas rápidas: energía de llegada, motivo de la visita y curiosidad para hacer match.")

    asistentes = st.session_state.asistentes
    opciones_luma = {a.guest_id: a.nombre for a in asistentes if a.origen == "luma" and not a.esta_checkeado()}

    # Este radio va FUERA del st.form a propósito: los widgets dentro de un
    # formulario no disparan un rerender hasta que se aprieta el botón de
    # envío, así que si estuviera adentro, cambiar entre "busco mi nombre" y
    # "soy nuevo/a" no actualizaría el campo visible hasta después de enviar.
    modo = st.radio(
        "¿Ya te registraste en Luma?",
        ["Sí, busco mi nombre", "No, soy nuevo/a"],
        horizontal=True,
        key="modo_checkin",
    )

    with st.form("form_checkin", clear_on_submit=True):
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
        animo_sel = st.radio(
            "¿Cómo llegas hoy?", opciones_animo, index=2, horizontal=True, key="animo_checkin"
        )
        indice_animo = opciones_animo.index(animo_sel)
        icono_animo, etiqueta_animo, energia = ESTADOS_ANIMO[indice_animo]
        estado_animo_texto = f"{icono_animo} {etiqueta_animo}"
        motivo = st.text_input("¿Cuál es tu motivo de visita?", placeholder="Ej: conocer casos de IA aplicada")
        curiosidad = st.text_input(
            "Una curiosidad tuya o algo para hacer match", placeholder="Ej: me interesa la IA en salud"
        )

        enviado = st.form_submit_button("Registrar check-in", use_container_width=True)

    if enviado:
        nombre_final = opciones_luma.get(guest_id_sel, nombre_form)
        if not nombre_final:
            st.error("Por favor ingresa tu nombre.")
        else:
            st.session_state.asistentes = storage.registrar_checkin(
                asistentes,
                nombre=nombre_final,
                energia_llegada=energia,
                motivo_visita=motivo,
                curiosidad=curiosidad,
                guest_id=guest_id_sel,
                estado_animo=estado_animo_texto,
            )
            st.success(f"¡Check-in registrado para {nombre_final}! {estado_animo_texto}")

    st.divider()
    st.subheader(f"Asistentes totales ({len(st.session_state.asistentes)})")
    if st.session_state.asistentes:
        df = pd.DataFrame([a.to_dict() for a in st.session_state.asistentes])
        columnas = [
            "nombre",
            "origen",
            "checked_in_at",
            "estado_animo",
            "energia_llegada",
            "motivo_visita",
            "curiosidad",
            "comuna",
        ]
        columnas_presentes = [c for c in columnas if c in df.columns]
        st.dataframe(df[columnas_presentes], use_container_width=True, hide_index=True)
    else:
        st.caption("Todavía no hay asistentes cargados.")

# ---------------------------------------------------------------------------
# Pestaña 2: Agenda del Evento
# ---------------------------------------------------------------------------
with tab_programa:
    st.subheader("Agenda del evento")
    for charla in st.session_state.charlas:
        with st.container(border=True):
            st.markdown(f"### {charla.titulo}")
            st.markdown(f"**{charla.charlista}** — {charla.rol}")
            st.caption(" · ".join(charla.tema_tags))

# ---------------------------------------------------------------------------
# Pestaña 3: Demo Agéntica (pre-análisis por lotes + lectura instantánea)
# ---------------------------------------------------------------------------
with tab_demo:
    st.subheader("Pre-análisis global")
    st.caption(
        "Procesa a todos los asistentes con check-in en un solo lote y guarda los "
        "resultados en data/posibilidades.json. Así, seleccionar a alguien más abajo "
        "es instantáneo: no se llama a la IA en cada clic durante el evento."
    )
    checkeados = [a for a in st.session_state.asistentes if a.esta_checkeado()]
    cubiertos = sum(1 for a in checkeados if a.guest_id in st.session_state.posibilidades)

    col_boton, col_estado = st.columns([1, 2])
    with col_boton:
        if st.button(
            "🚀 Generar / Actualizar Pre-Análisis Global con IA",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.generando_preanalisis or not checkeados,
        ):
            st.session_state.generando_preanalisis = True
            total = len(checkeados)

            with st.status("Generando pre-análisis con IA…", expanded=True) as status:
                def _reportar_progreso(indice, total_, asistente_actual):
                    status.write(f"🔎 Analizando a **{asistente_actual.nombre}** ({indice}/{total_})…")

                resultados = agente.preanalizar_todos(
                    checkeados, st.session_state.charlas, jev_client=jev, progreso_callback=_reportar_progreso
                )
                status.update(
                    label=f"✅ Pre-análisis completo: {total} asistentes procesados.", state="complete"
                )

            storage.guardar_posibilidades({"generado_en": timestamp_ahora(), "resultados": resultados})
            st.session_state.posibilidades = resultados
            st.session_state.generando_preanalisis = False
            st.rerun()

    with col_estado:
        if not checkeados:
            st.info("Todavía no hay asistentes con check-in.")
        elif cubiertos == 0:
            st.warning("Ningún asistente tiene pre-análisis todavía. Genera el lote con el botón de la izquierda.")
        elif cubiertos < len(checkeados):
            st.warning(
                f"{cubiertos}/{len(checkeados)} asistentes tienen pre-análisis. "
                "Hay check-ins nuevos: vuelve a generar el lote para cubrirlos a todos."
            )
        else:
            st.success(f"{cubiertos}/{len(checkeados)} asistentes con pre-análisis listo. Todo al día ✅")

    st.divider()
    st.subheader("Consultar un/a asistente")

    if not checkeados:
        st.caption("Registra a alguien en 'Check-in' para poder consultarlo aquí.")
    else:
        opciones = {a.guest_id: a.nombre for a in checkeados}
        guest_id_demo = st.selectbox(
            "Elige un/a asistente", options=list(opciones.keys()), format_func=lambda gid: opciones[gid]
        )
        asistente_sel = next(a for a in checkeados if a.guest_id == guest_id_demo)

        col1, col2 = st.columns([1, 2])
        with col1:
            st.markdown("**Perfil**")
            st.write(f"{asistente_sel.estado_animo or '—'} ({asistente_sel.energia_llegada}%)")
            st.write(f"🎯 Motivo: {asistente_sel.motivo_visita or '—'}")
            st.write(f"💡 Curiosidad: {asistente_sel.curiosidad or '—'}")

        mapa = st.session_state.posibilidades.get(asistente_sel.guest_id)

        with col2:
            if mapa:
                st.caption("⚡ Leído al instante desde el pre-análisis (sin llamar a la IA ahora).")
            else:
                st.warning(
                    "Esta persona todavía no tiene pre-análisis (probablemente hizo check-in después "
                    "del último lote). Puedes analizarla individualmente:"
                )
                if st.button(
                    "🔍 Analizar solo a esta persona",
                    use_container_width=True,
                    disabled=st.session_state.generando_preanalisis,
                ):
                    st.session_state.generando_preanalisis = True
                    with st.spinner(f"Analizando a {asistente_sel.nombre}…"):
                        charla_id_jev = jev.elegir_charla(asistente_sel, st.session_state.charlas)
                        recomendacion = agente.recomendar_charla(
                            asistente_sel, st.session_state.charlas, charla_id_sugerida=charla_id_jev
                        )
                        mapa = agente.generar_mapa_posibilidades(
                            asistente_sel, st.session_state.charlas, recomendacion
                        )
                        st.session_state.posibilidades[asistente_sel.guest_id] = mapa
                        storage.guardar_posibilidades(
                            {"generado_en": timestamp_ahora(), "resultados": st.session_state.posibilidades}
                        )
                    st.session_state.generando_preanalisis = False
                    st.rerun()

        if mapa:
            charla_id_rec = mapa.get("charla_recomendada_id")
            charla_rec = next((c for c in st.session_state.charlas if c.id == charla_id_rec), None)

            st.success(
                f"**Charla recomendada:** {charla_rec.titulo} — {charla_rec.charlista}"
                if charla_rec
                else "No se pudo recomendar una charla."
            )
            st.markdown(f"**Razonamiento del agente:** {mapa.get('razonamiento', '')}")
            st.markdown(f"**Recomendación de valor:** {mapa.get('recomendacion_valor', '')}")
            st.markdown(f"**Pregunta reflexiva para el panel:** _{mapa.get('pregunta_sugerida', '')}_")
            st.caption(
                f"Fuente: {mapa.get('fuente', '—')} · Esta es una hipótesis del agente, "
                "no una verdad cerrada: siéntete libre de cuestionarla."
            )

            st.markdown("#### 🗺️ Mapa de posibilidades")
            nodos = mapa["mapa"]["nodos"]
            conexiones = mapa["mapa"]["conexiones"]

            G = nx.Graph()
            colores = {"asistente": "#8e44ad", "charla": "#2980b9", "palabra": "#27ae60"}
            for nodo in nodos:
                G.add_node(nodo["id"], label=nodo["label"], tipo=nodo["tipo"])
            for con in conexiones:
                G.add_edge(con["origen"], con["destino"], peso=con["peso"])

            fig, ax = plt.subplots(figsize=(8, 5))
            pos = nx.spring_layout(G, seed=42, k=0.9)
            node_colors = [colores.get(G.nodes[n]["tipo"], "#7f8c8d") for n in G.nodes]
            nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=1200, ax=ax)
            nx.draw_networkx_edges(G, pos, alpha=0.5, ax=ax)
            nx.draw_networkx_labels(
                G, pos, labels={n: G.nodes[n]["label"] for n in G.nodes}, font_size=8, ax=ax
            )
            ax.axis("off")
            st.pyplot(fig)

# ---------------------------------------------------------------------------
# Pestaña 4: Encuentros ASUS (matchmaking persona-a-persona)
# ---------------------------------------------------------------------------
with tab_encuentros:
    st.subheader("Sugerir encuentros entre asistentes")
    st.caption(
        "Agrupa a quienes el pre-análisis ya recomendó para la misma charla (interés en "
        "común) y sugiere que se junten a conversar en el espacio ASUS. Requiere que el "
        "Pre-Análisis Global de la pestaña anterior ya esté generado."
    )

    checkeados = [a for a in st.session_state.asistentes if a.esta_checkeado()]
    hay_preanalisis = bool(st.session_state.posibilidades)

    if not hay_preanalisis:
        st.warning("Primero genera el Pre-Análisis Global en la pestaña '🤖 Demo Agéntica'.")
    else:
        if st.button(
            "🤝 Sugerir encuentros entre asistentes",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.generando_encuentros or not checkeados,
        ):
            st.session_state.generando_encuentros = True

            with st.status("Buscando personas con intereses en común…", expanded=True) as status:
                def _reportar_progreso_encuentro(indice, total_, charla, miembros):
                    titulo = charla.titulo if charla else "interés en común"
                    nombres = ", ".join(m.nombre for m in miembros)
                    status.write(f"🔎 Grupo {indice}/{total_} — {titulo}: {nombres}")

                grupos = agente.sugerir_encuentros(
                    checkeados,
                    st.session_state.posibilidades,
                    st.session_state.charlas,
                    progreso_callback=_reportar_progreso_encuentro,
                )
                status.update(label=f"✅ {len(grupos)} grupo(s) de encuentro sugeridos.", state="complete")

            storage.guardar_encuentros({"generado_en": timestamp_ahora(), "grupos": grupos})
            st.session_state.encuentros = grupos
            st.session_state.generando_encuentros = False
            st.rerun()

        st.divider()

        if not st.session_state.encuentros:
            st.caption(
                "Todavía no hay grupos sugeridos (o nadie comparte charla recomendada con otra persona todavía)."
            )
        else:
            st.subheader(f"Grupos sugeridos ({len(st.session_state.encuentros)})")
            for grupo in st.session_state.encuentros:
                with st.container(border=True):
                    nombres = " · ".join(m["nombre"] for m in grupo["integrantes"])
                    st.markdown(f"**{nombres}**")
                    st.caption(f"Interés en común: {grupo.get('tema_comun', '')}")
                    st.write(grupo.get("mensaje", ""))
                    st.caption(f"Fuente: {grupo.get('fuente', '—')}")

    st.divider()
    st.subheader("Vista por charlista")
    st.caption(
        "Para cada charla, quiénes fueron recomendadas hacia ella, con qué motivo/curiosidad "
        "llegaron, y qué palabras clave las conectan entre sí. No usa la IA — es una "
        "reorganización instantánea del pre-análisis que ya generaste arriba."
    )

    if not hay_preanalisis:
        st.caption("Genera primero el Pre-Análisis Global en la pestaña '🤖 Demo Agéntica'.")
    else:
        if st.button("🎤 Generar vista por charlista", use_container_width=True):
            with st.spinner("Armando el mapa por charlista…"):
                vista = agente.generar_vista_charlistas(
                    checkeados, st.session_state.posibilidades, st.session_state.charlas
                )
            storage.guardar_mapa_charlistas({"generado_en": timestamp_ahora(), "charlas": vista})
            st.session_state.vista_charlistas = vista
            st.rerun()

        if not st.session_state.vista_charlistas:
            st.caption("Todavía no se ha generado la vista por charlista.")
        else:
            for charla_id, datos in st.session_state.vista_charlistas.items():
                with st.container(border=True):
                    st.markdown(f"#### {datos['titulo']} — {datos['charlista']}")
                    st.caption(f"{len(datos['asistentes'])} asistente(s) recomendadas hacia esta charla")

                    for a in datos["asistentes"]:
                        st.write(f"- **{a['nombre']}** — {a['motivo_visita'] or '—'} / {a['curiosidad'] or '—'}")

                    if datos["conexiones_internas"]:
                        st.markdown("**Conexiones encontradas:**")
                        for c in datos["conexiones_internas"]:
                            nombres_c = ", ".join(c["integrantes"])
                            st.write(f"🔗 {nombres_c} — comparten interés en *'{c['palabra']}'*")
                    else:
                        st.caption("No se detectaron palabras clave compartidas entre estas asistentes.")

                    nodos = datos["mapa"]["nodos"]
                    conexiones_grafo = datos["mapa"]["conexiones"]
                    G = nx.Graph()
                    colores = {"charla": "#2980b9", "asistente": "#8e44ad", "palabra": "#27ae60"}
                    for nodo in nodos:
                        G.add_node(nodo["id"], label=nodo["label"], tipo=nodo["tipo"])
                    for con in conexiones_grafo:
                        G.add_edge(con["origen"], con["destino"], peso=con["peso"])

                    fig, ax = plt.subplots(figsize=(7, 4.5))
                    pos = nx.spring_layout(G, seed=42, k=0.9)
                    node_colors = [colores.get(G.nodes[n]["tipo"], "#7f8c8d") for n in G.nodes]
                    nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=900, ax=ax)
                    nx.draw_networkx_edges(G, pos, alpha=0.5, ax=ax)
                    nx.draw_networkx_labels(
                        G, pos, labels={n: G.nodes[n]["label"] for n in G.nodes}, font_size=7, ax=ax
                    )
                    ax.axis("off")
                    st.pyplot(fig)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Pestaña 5: Preguntas al Panel (enviadas desde la vista pública)
# ---------------------------------------------------------------------------
with tab_preguntas:
    st.subheader("Preguntas enviadas por asistentes")
    st.caption(
        "Se llenan desde la vista pública (publico/app_publico.py). Cada pregunta viene con un "
        "triage automático: si calza con una charla ya programada, o si conviene priorizarla "
        "para el panel en vivo."
    )
    datos_preguntas = storage.cargar_preguntas_panel()
    preguntas = datos_preguntas.get("preguntas", [])

    if not preguntas:
        st.caption("Todavía no han llegado preguntas.")
    else:
        prioritarias = [p for p in preguntas if p.get("resultado") == "prioridad_panel"]
        en_presentacion = [p for p in preguntas if p.get("resultado") != "prioridad_panel"]

        st.markdown(f"#### 🔴 Prioritarias para el panel ({len(prioritarias)})")
        for p in reversed(prioritarias):
            with st.container(border=True):
                st.write(f"**{p['pregunta']}**")
                st.caption(f"— {p['nombre']} · {p['timestamp']}")

        st.markdown(f"#### 🟢 Posiblemente ya cubiertas en la presentación ({len(en_presentacion)})")
        for p in reversed(en_presentacion):
            with st.container(border=True):
                st.write(f"**{p['pregunta']}**")
                st.caption(
                    f"— {p['nombre']} · {p['timestamp']} · relacionada con: "
                    f"{p.get('charla_relacionada_titulo', '—')}"
                )

# ---------------------------------------------------------------------------
# Pestaña 6: Estación Estudiantes
# ---------------------------------------------------------------------------
with tab_estudiantes:
    respuestas_data = storage.cargar_respuestas_estudiantes()
    st.subheader("Estación abierta")
    st.markdown(f"### 💬 {respuestas_data['pregunta_ancla']}")

    with st.form("form_estudiante", clear_on_submit=True):
        nombre_est = st.text_input("Tu nombre (opcional)")
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
# Pestaña 7: Cierre de Jornada
# ---------------------------------------------------------------------------
with tab_cierre:
    st.subheader("Resumen de cierre")
    st.caption("Al finalizar la jornada, el agente redacta una breve nota con lo vivido en la estación.")

    if st.button("📰 Generar resumen del día", type="primary"):
        respuestas_data = storage.cargar_respuestas_estudiantes()
        with st.spinner("Redactando el resumen de cierre…"):
            resumen = agente.generar_resumen_evento(
                st.session_state.asistentes, respuestas_data, list(st.session_state.posibilidades.values())
            )
        storage.guardar_resumen_evento(resumen)
        st.session_state["ultimo_resumen"] = resumen

    resumen_guardado = st.session_state.get("ultimo_resumen") or storage.cargar_resumen_evento()
    if resumen_guardado:
        st.markdown(f"## {resumen_guardado.get('titular', '')}")
        st.write(resumen_guardado.get("cuerpo", ""))
        st.caption(f"Fuente: {resumen_guardado.get('fuente', '—')}")
    else:
        st.caption("Todavía no se ha generado un resumen.")

# ---------------------------------------------------------------------------
# Pestaña 8: Cómo funciona (esquema visual para mostrar en el evento)
# ---------------------------------------------------------------------------
with tab_esquema:
    st.subheader("Así funciona la estación")
    st.caption(
        "Ninguna recomendación es una verdad cerrada: el agente propone, tú decides. "
        "Este esquema muestra cómo se llega a cada resultado que ves en pantalla."
    )
    col_izq, col_der = st.columns(2)
    with col_izq:
        st.pyplot(figura_flujo_matchmaking())
    with col_der:
        st.pyplot(figura_flujo_cierre())

    st.divider()
    st.subheader("📱 Acceso a la vista pública")
    public_url = os.environ.get("PUBLIC_APP_URL", "")
    if not public_url:
        st.info(
            "Configura `PUBLIC_APP_URL` en tu `.env` con la URL donde corre "
            "`publico/app_publico.py` (por ejemplo la URL de un forwarded port) "
            "para mostrar aquí el código QR que los asistentes escanean al llegar."
        )
    else:
        try:
            import qrcode

            img = qrcode.make(public_url)
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            st.image(buffer.getvalue(), caption=public_url, width=240)
        except Exception as e:
            st.warning(f"No se pudo generar el QR: {e}")

# ---------------------------------------------------------------------------
# Pestaña 9: Health Check (Panel de Diagnóstico)
# ---------------------------------------------------------------------------
with tab_health:
    st.subheader("Diagnóstico del sistema")
    st.caption("Verifica de un clic que los archivos de datos estén sanos y que la API responda.")

    if st.button("🔍 Verificar archivos de datos", use_container_width=True):
        resultados = storage.verificar_integridad()
        for archivo, info in resultados.items():
            icono = "✅" if info["ok"] else "❌"
            st.write(f"{icono} **{archivo}** — {info['detalle']}")

    st.divider()

    st.markdown(f"**Modo actual del agente:** {'🔌 Local (mock)' if agente.modo_mock else '🌐 OpenRouter'}")
    st.caption("Cascada de modelos (en orden de intento):")
    st.code("\n".join(f"{i+1}. {m}" for i, m in enumerate(agente.modelos)), language=None)
    if st.button("📡 Probar conexión con OpenRouter", use_container_width=True):
        with st.spinner("Probando la cascada de modelos…"):
            resultado = agente.probar_conexion()
        if resultado["ok"]:
            st.success(resultado["mensaje"])
        else:
            st.warning(resultado["mensaje"])

    st.divider()
    st.markdown(
        f"**Jev (Vercel AI Gateway):** {'🟢 Activo — se usa para elegir charla y moderar respuestas' if jev.activo else '⚪ Inactivo (no hay AI_GATEWAY_API_KEY) — la app funciona igual sin él'}"
    )
