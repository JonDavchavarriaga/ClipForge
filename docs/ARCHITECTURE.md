# Arquitectura de Gevik Clip

**Gevik Clip** sigue un diseño **local-first**, modular y liviano en Windows: núcleo en Python + FFmpeg + `faster-whisper` + OpenCV + `pysubs2`, servido en una app de escritorio local sin Docker ni infraestructura cloud, y coordinado con las suscripciones IA locales del usuario.

> Consulta el razonamiento detrás de las decisiones clave en [docs/decisions/](decisions/).

---

## Estructura del repositorio

```text
ClipForge/                                    # Raíz del proyecto (paquete interno clipforge)
├── AGENTS.md / CLAUDE.md / GEMINI.md         # Instrucciones compartidas para Antigravity, Claude Code y Codex
├── .agents/                                  # Reglas, roles, skills, workflows y memoria bilingüe
├── docs/
│   ├── ARCHITECTURE.md                       # Este documento
│   ├── ROADMAP.md                            # Estado actual y fases futuras
│   └── decisions/                            # Architecture Decision Records (ADRs)
│       ├── 0001-local-first-desktop-architecture.md
│       ├── 0002-zero-drift-filtergraph-subtitle-sync.md
│       └── 0003-local-subscription-ai-subtitles.md
├── assets/icons/
│   ├── gevik-clip.svg                        # Logotipo vectorial negro obsidiana y plateado ('G')
│   └── gevik-clip.ico                        # Ícono multi-resolución Windows generado desde icon.py
├── scripts/
│   └── create_desktop_shortcut.ps1           # Crea Gevik Clip.lnk en el Escritorio de Windows
├── src/clipforge/
│   ├── cli.py                                # CLI + flujo de lotes, preview-first y calibración de cámara
│   ├── config.py                             # Rutas locales y perfiles de canal (es_main / en_main)
│   ├── storage.py                            # Estructura de workspace/ (recordings, clips, rendered, logs)
│   ├── logging_utils.py                      # Logs rotativos en disco + guardas pythonw (stdout/stderr None)
│   ├── process_manager.py                    # ProcessRegistry, prioridad baja en Windows y lock de instancia única
│   ├── pipeline/
│   │   ├── highlights.py                     # Detección de reacciones, auto-conteo, comerciales mudos y SRT
│   │   ├── media.py                          # FFmpeg (filtergraph trim/atrim 0ms), OpenCV, Whisper cacheado, thumbs
│   │   ├── subtitles.py                      # Catálogo visual de estilos .ass (pysubs2), altura Y% y offset ms
│   │   ├── live.py                           # Buffer circular en vivo, grabación 1h–2h+ y auto-clip por picos
│   │   ├── translation.py                    # Empaquetado JSON EN↔ES y validación estricta de timestamps
│   │   ├── auto_translate.py                 # Traducción 1-clic con Claude Code, Codex y Gemini (solo subtítulos)
│   │   └── editor.py                         # Mini-Editor local (Trim IN/OUT, cámara split%, altura Y%, audio)
│   ├── publishing/
│   │   └── publication.py                    # Huella SHA-256, aprobación, SQLite idempotente y YouTube OAuth
│   └── desktop/
│       ├── icon.py                           # Generador del ícono negro/plateado 'G' (.ico)
│       ├── jobs.py                           # Cola FIFO con progreso, cancelación y descarga de Whisper inactivo
│       ├── server.py                         # Backend HTTP localhost con token, cabeceras de seguridad y streaming Range
│       ├── launcher.py                       # Instancia única, ventana nativa Edge/Chrome --app y cierre limpio
│       └── static/                           # Frontend negro obsidiana / plateado (index.html, styles.css, app.js)
├── tests/                                    # Suite unitaria e integración (unittest discover)
└── clipforge.py                              # Punto de entrada CLI / Desktop
```

---

## Mapa de herramientas open-source (por niveles)

Para mantener un entorno rápido y estable en Windows, las herramientas se adoptan de forma escalonada:

### Nivel 1 — Núcleo activo hoy en Gevik Clip

| Herramienta | Rol en Gevik Clip |
|---|---|
| **yt-dlp** | Captura de streams en vivo (Twitch/Kick/YouTube) y descargas opcionales |
| **FFmpeg + libass** | Recorte sin deriva (`trim`/`atrim`), composición `split-cam`, audio, miniaturas y quemado `.ass` |
| **faster-whisper** | Transcripción local en CPU (vía PCM `numpy.float32`, sin `PyAV`, con descarga en inactividad) |
| **OpenCV** | Detección facial para `face` / `split-cam` automático y generación del ícono `.ico` |
| **pysubs2** | Generación de plantillas `.ass` dinámicas palabra a palabra con catálogo visual |
| **OBS Studio** | Captura local opcional de gameplay (GTA 6) y micrófono multipista |

### Nivel 2 — Extensiones opcionales bajo demanda (solo si un caso real lo exige)

| Herramienta | Cuándo activarla |
|---|---|
| **DeepFilterNet** | Si `afftdn` de FFmpeg no basta para ruido extremo de teclado/ventilador |
| **Demucs** | Si grabas juego + voz en una sola pista muy mezclada y necesitas aislar voz |
| **pyannote.audio** | Podcasts o streams con múltiples hablantes que requieran diarización |
| **Norfair / Supervision** | Seguimiento multi-persona avanzado cuando varios sujetos se cruzan |
| **Real-ESRGAN / RIFE** | Escalado o interpolación puntual en clips concretos de baja calidad |

---

## Arquitectura Multi-Agente Local (`.agents/`)

- **Gemini Antigravity**: dirección de arquitectura, implementación y pruebas en el workspace.
- **Claude Code (`claude.exe -p --output-format text`)**: traducción natural EN↔ES de subtítulos y revisión editorial/código.
- **OpenAI Codex (`codex.cmd exec --skip-git-repo-check --ephemeral -s read-only -`)**: traducción natural EN↔ES de subtítulos y auditoría técnica.
- **Gemini CLI (`gemini`, opcional)**: tercer motor seleccionable en 1 clic al iniciar sesión con cuenta de Google.
- **Contrato común**: [AGENTS.md](../AGENTS.md), [CLAUDE.md](../CLAUDE.md), [GEMINI.md](../GEMINI.md) y [.agents/README.md](../.agents/README.md).
