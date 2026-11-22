# Gevik Clip

**Gevik Clip** es un estudio de escritorio **100% local** en Windows (identidad negro obsidiana y plateado metálico **`G`**) para capturar streams en vivo (Twitch, Kick y YouTube Live), generar clips verticales **9:16** con subtítulos animados sincronizados con precisión exacta, traducir subtítulos **EN ↔ ES** en 1 clic usando tus suscripciones IA locales (**Claude Code**, **Codex** y **Gemini**), editar visualmente el encuadre y descargar MP4 listos para **YouTube Shorts** y **TikTok**.

> **Nota sobre nombres internos**: el producto y la interfaz se llaman **Gevik Clip** (`assets/icons/gevik-clip.svg`, `Gevik Clip.lnk`). Internamente se conserva el paquete Python `src/clipforge/` y el punto de entrada `clipforge.py` para mantener compatibilidad con `.venv` y scripts existentes (ver [ADR-001](docs/decisions/0001-local-first-desktop-architecture.md)).

---

## Inicio Rápido (Windows)

Requiere **Python 3.11 o 3.12 (64 bits)**.

```powershell
# 1. Crear entorno virtual e instalar dependencias locales
py -3.12 -m venv .venv
$py = '.\.venv\Scripts\python.exe'
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt

# 2. Crear el acceso directo con el logo negro y plateado "G" en tu Escritorio
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_desktop_shortcut.ps1

# 3. Abrir Gevik Clip directamente
& $py clipforge.py app
```

---

## Funcionalidades Principales

1. **📺 Live y Auto-Clip**:
   - Reproduce Twitch, Kick o YouTube Live dentro de la app mientras grabas el stream completo (1h–2h+) en `workspace/live_recordings/`.
   - Detecta picos de reacción para sacar clips automáticos (30s / 45s / 60s) e ignora bloques comerciales mudos de Twitch.
2. **⚡ Generar Clips**:
   - Calcula automáticamente cuántos clips sacar según la duración del stream, saltando comerciales mudos.
   - Permite recortar tu webcam visualmente sobre un fotograma (estilo Recortes de Windows) y validar el encuadre en el 1er clip antes de generar el resto.
   - **Sincronización exacta (0 ms de desfase)**: recorte dentro del filtergraph (`trim`/`atrim` + `setpts`) y alineación por clip (ver [ADR-002](docs/decisions/0002-zero-drift-filtergraph-subtitle-sync.md)).
3. **🎬 Gestor de Clips, Traducción IA y Mini-Editor**:
   - **Gestor visual**: nombres legibles por proyecto, miniaturas reales 9:16, estados (`Por revisar`, `Aprobado`, `Descartado`), vaciado de descartados y **descarga directa de MP4 en 1 clic**.
   - **Traducción 1-clic (solo subtítulos)**: usa **Claude Code**, **Codex (ChatGPT)** o **Gemini** ya instalados en tu PC para traducir todo el clip con coherencia gamer sin tocar el audio original (ver [ADR-003](docs/decisions/0003-local-subscription-ai-subtitles.md)).
   - **Mini-Editor integrado**: recorte rápido `IN aquí` / `OUT aquí`, 6 tarjetas visuales de estilos de subtítulos (`Amarillo Viral`, `Cian Gaming`, `Impacto Naranja`, `Gevik Plata`, `Blanco Limpio`, `Sin subtítulos`), altura exacta de subtítulos (`12%–90%`), tamaño de cámara split (`20%–52%`) con guías visuales sobre el video, sincronía fina (`-2000..+2000 ms`) y volumen/limpieza de audio.
4. **📁 Biblioteca Local y Gestión de Espacio**:
   - Muestra el espacio ocupado (`MB`/`GB`), marca los lives ya procesados (`✓ Procesado`) y permite borrar lives completos y clips en vivo directamente desde la interfaz para liberar disco de forma segura.
5. **⚙️ Estabilidad de App Profesional**:
   - Instancia única (evita abrir servidores duplicados), cola FIFO con botón **Cancelar**, ejecución en baja prioridad para no afectar juegos/streams, liberación automática de RAM de Whisper y cierre total limpio (`⏻ Cerrar Gevik Clip`).

---

## Comandos Principales

| Comando | Descripción |
|---|---|
| `.\.venv\Scripts\python.exe clipforge.py app` | Abre **Gevik Clip** en ventana de escritorio local |
| `powershell -ExecutionPolicy Bypass -File .\scripts\create_desktop_shortcut.ps1` | Genera `assets/icons/gevik-clip.ico` y crea `Gevik Clip.lnk` en el Escritorio |
| `.\.venv\Scripts\python.exe clipforge.py live "<URL>" --clip-seconds 45` | Captura un stream desde consola con recorte instantáneo |
| `.\.venv\Scripts\python.exe clipforge.py process "<video.mp4>" --layout split-cam --caption-style highlight` | Genera clips 9:16 desde CLI |
| `.\.venv\Scripts\python.exe clipforge.py rerender "<lote>" clip-01 --srt "<captions.es.srt>" --caption-style silver` | Re-renderiza un clip puntual con nuevos subtítulos o estilo |
| `.\.venv\Scripts\python.exe -m unittest discover -v` | Ejecuta la suite completa de pruebas unitarias e integración |

---

## Arquitectura y Documentación

- **Arquitectura del sistema**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Hoja de ruta**: [docs/ROADMAP.md](docs/ROADMAP.md)
- **Decisiones de arquitectura (ADRs)**:
  - [ADR-001: Arquitectura de Escritorio Local-First y Rebrand a Gevik Clip](docs/decisions/0001-local-first-desktop-architecture.md)
  - [ADR-002: Sincronización Exacta de Subtítulos (Cero Desfase) en Grabaciones VFR](docs/decisions/0002-zero-drift-filtergraph-subtitle-sync.md)
  - [ADR-003: Traducción 1-Clic de Subtítulos con Suscripciones IA Locales](docs/decisions/0003-local-subscription-ai-subtitles.md)
- **Arquitectura agéntica compartida**: [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), [GEMINI.md](GEMINI.md) y [.agents/README.md](.agents/README.md)

### Carpetas locales de trabajo (`workspace/`, ignoradas por git)

```text
workspace/
├── live_recordings/   # Grabaciones completas de lives (1h–2h+) por sesión
├── live_clips/        # Clips manuales y automáticos sacados en vivo
├── rendered_clips/    # Proyectos de clips 9:16 listos (video.mp4, thumb.jpg, captions)
├── sources/           # Videos importados manualmente
└── logs/              # Telemetría persistente en disco (clipforge.log, events.jsonl, errors.jsonl)
```

---

## Motores IA Locales para Subtítulos

Gevik Clip detecta automáticamente qué herramientas CLI tienes instaladas en tu PC:

- **Claude Code** (`claude.exe`): listo automáticamente si iniciaste sesión en Claude Code.
- **Codex / ChatGPT** (`codex.cmd`): listo automáticamente si iniciaste sesión en Codex CLI.
- **Gemini** (`gemini`): instala el CLI oficial (`npm i -g @google/gemini-cli`) e inicia sesión con tu cuenta de Google (o define `GEMINI_API_KEY`).

---

## Carga Opcional a YouTube Data API v3 (CLI)

Si más adelante quieres automatizar subidas privadas/programadas por CLI con OAuth de Google Cloud:

```powershell
$py = '.\.venv\Scripts\python.exe'
$lote = '.\workspace\rendered_clips\lote-XXXXXXXX'
& $py clipforge.py approve $lote clip-01 --title 'Título del short #shorts' --caption 'Descripción' --platform youtube
& $py clipforge.py youtube $lote --client 'client_secret.json' --channel-id 'UC_TU_CANAL' --clip clip-01 --kids no
```

Nunca guardes `client_secret*.json` ni tokens dentro del repositorio. Conserva `youtube.sqlite3` para evitar subidas duplicadas.

---

## Verificación Local

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Las pruebas verifican detección de cortes y comerciales mudos, sincronización exacta en filtergraph, estilos `.ass`, Mini-Editor (Trim IN/OUT, posición vertical Y%, tamaño de cámara split), traducción automática con preservación de timestamps, gestor de clips, borrado seguro en Biblioteca, registro de procesos y servidor local de escritorio.
