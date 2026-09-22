# UDP Tech Talks Vol. 1 — Estación interactiva con IA (V4)

Esta versión separa la app en dos piezas:

- **`src/app.py`** — panel de **administración** (protegido con clave genérica),
  usado por el equipo del evento.
- **`publico/app_publico.py`** — **vista pública**, para que cada asistente
  interactúe desde su propio celular (se accede escaneando el QR que el admin
  muestra en la pestaña "Cómo funciona").

## Novedades de la V4

- **Panel de administración protegido**: si configuras `ADMIN_PASSWORD` en el
  `.env`, `src/app.py` pide una clave antes de mostrar cualquier pestaña. Si la
  dejas vacía, el panel queda abierto (útil solo en desarrollo). No es un
  sistema de autenticación real — es una barrera simple para que no cualquiera
  que abra la URL del Chromebox entre al panel de control.
- **Vista pública nueva** (`publico/app_publico.py`), sin clave, pensada para el
  celular de cada asistente: Bienvenida, Mi Check-in (autoregistro), Agenda,
  Mi Recomendación (lee el pre-análisis que ya generó el admin), ¿Con quién
  conectar? (lee los encuentros ya sugeridos), Estación Estudiantes, Pregunta
  para el Panel, y Trabaja tu idea con IA. No dispara ninguna llamada masiva a
  la IA — esas quedan solo del lado del admin.
- **Pregunta para el Panel** (idea de Heileen Goodson): el asistente escribe una
  pregunta; un triage local (sin IA, instantáneo) revisa si calza con alguna
  charla programada o si conviene priorizarla para el panel en vivo. El admin
  las ve todas, ordenadas, en la nueva pestaña "❓ Preguntas al Panel".
- **Trabaja tu idea con IA** (idea de Heileen Goodson): un mini ejercicio de
  prompting — el asistente escribe una idea suelta y la IA (o el modo local si
  no hay API key) la devuelve más concreta junto con 2-3 preguntas para seguir
  desarrollándola, sin darle una respuesta cerrada.
- **QR de acceso**: en la pestaña "Cómo funciona" del admin, configurando
  `PUBLIC_APP_URL` en el `.env` se genera un código QR hacia la vista pública
  (por ejemplo, apuntando a un forwarded port de VS Code).

## Cómo correrla

Necesitas **dos procesos** corriendo en paralelo (dos terminales), uno para
cada app — comparten los mismos datos en `data/`:

```bash
cd udp_tech_talks_v4
pip install -r requirements.txt

# Terminal 1: panel de administración (equipo del evento)
streamlit run src/app.py --server.port 8501

# Terminal 2: vista pública (asistentes, vía QR)
streamlit run publico/app_publico.py --server.port 8502
```

Se abren en `http://localhost:8501` (admin) y `http://localhost:8502` (público).
Para el evento: el admin queda en el Chromebox/kiosko, y el puerto 8502 se
expone (forwarded port o similar) para que el QR apunte ahí.

## Variables de entorno (.env)

Además de `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` y `AI_GATEWAY_API_KEY` (ver
más abajo), la V4 agrega:

```bash
ADMIN_PASSWORD="una-clave-simple"     # protege src/app.py; vacío = sin protección
PUBLIC_APP_URL="https://tu-url-publica"  # solo para generar el QR en el admin
```

## Modo con IA real (OpenRouter)

Por defecto la app corre en **modo local (mock)**: el matchmaking y el resumen
final se generan con reglas simples, sin depender de internet. Para activar el
modelo real:

```bash
export OPENROUTER_API_KEY="tu-api-key"
# opcional, por defecto usa un modelo gratuito:
export OPENROUTER_MODEL="meta-llama/llama-3.1-8b-instruct:free"
streamlit run src/app.py
```

Si la llamada a OpenRouter falla en cualquier momento (sin señal, rate limit,
etc.), la app cae automáticamente al modo local sin interrumpir la demo.

## Opcional: Jev (Vercel AI Gateway)

Jev es un modelo *evaluador* (no generativo): en vez de redactar texto, responde
preguntas tipadas con probabilidades. Se usa acá en dos puntos, siempre opcional:

- Elegir con más precisión qué charla recomendar (antes de que el agente redacte
  el razonamiento y la pregunta).
- Moderar las respuestas de la Estación Estudiantes antes de que se guarden/
  muestren en pantalla pública.

Para activarlo, agrega en tu `.env`:

```bash
AI_GATEWAY_API_KEY="tu-api-key-de-vercel-ai-gateway"
```

Si no la configuras, la app sigue funcionando exactamente igual (matchmaking por
palabras clave y sin moderación automática).

## Estructura

```
udp_tech_talks_v4/
├── requirements.txt
├── .env.example              # Copiar a .env con tus claves (OpenRouter / Jev / Admin / QR)
├── .gitignore
├── data/
│   ├── event_data.json         # Charlas y charlistas oficiales
│   ├── luma_registrants.csv    # Export de Luma (ejemplo con datos ficticios)
│   ├── registrants.json        # Se genera solo, unifica Luma + check-ins en vivo
│   ├── respuestas_estudiantes.json  # Se genera solo, pregunta ancla
│   ├── posibilidades.json      # Se genera solo, pre-análisis por lotes (asistente <-> charla)
│   ├── encuentros.json         # Se genera solo, matchmaking asistente <-> asistente
│   ├── mapa_charlistas.json    # Se genera solo, vista por charlista (quién + conexiones)
│   ├── preguntas_panel.json    # Se genera solo, preguntas públicas + triage
│   └── resumen_evento.json     # Se genera solo, noticia de cierre
├── src/                     # Panel de ADMINISTRACIÓN (protegido con clave)
│   ├── app.py               # Dashboard Streamlit (9 pestañas)
│   ├── models.py            # Modelos Pydantic: Asistente (con estado_animo) y Charla
│   ├── storage.py           # Lectura/escritura de todos los JSON/CSV + verificar_integridad()
│   ├── agent_client.py      # Cascada OpenRouter + mock + pre-análisis + encuentros +
│   │                        # vista por charlista + triage de preguntas + prompting de ideas
│   ├── jev_client.py        # Cliente opcional de Jev (Vercel AI Gateway)
│   └── diagrama.py          # Diagramas de flujo para la pestaña "Cómo funciona"
└── publico/                 # Vista PÚBLICA (sin clave, para el celular del asistente)
    └── app_publico.py       # Bienvenida, Check-in, Agenda, Mi Recomendación,
                              # ¿Con quién conectar?, Estudiantes, Pregunta al Panel, Ideas
```

## Reemplazar el CSV de ejemplo

`data/luma_registrants.csv` trae 3 filas ficticias solo para probar el parseo.
Antes del evento, reemplázalo por el export real de Luma (mismas columnas) y
borra `data/registrants.json` para que se regenere desde el CSV nuevo.

## Pendiente / próximos pasos sugeridos

- Ajustar los `tema_tags` de cada charla en `event_data.json` si quieres afinar
  el matchmaking local.
- Si se agrega una API key de OpenRouter, probar el modo real antes del evento
  con conexión de respaldo (datos móviles) por si el wifi del lugar falla.
