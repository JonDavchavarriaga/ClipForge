# Arquitectura de ClipForge

ClipForge sigue un diseño **local-first**, modular y escalonado: primero un núcleo rápido y mantenible en Python + FFmpeg + `faster-whisper`, y luego extensiones opcionales solo cuando un caso real de GTA 6 o stream lo justifique.

## Estructura del repositorio

```text
ClipForge/
├── AGENTS.md / CLAUDE.md / GEMINI.md   # Entradas compartidas para Codex, Claude Code y Antigravity
├── .agents/                            # Reglas, roles, skills, workflows y memoria bilingüe
├── src/clipforge/
│   ├── cli.py                          # CLI principal (ingesta, proceso, traducción, aprobación, YouTube)
│   ├── config.py                       # Rutas locales y perfiles de canal (es_main / en_main)
│   ├── pipeline/
│   │   ├── highlights.py               # Detección de momentos, rangos y SRT
│   │   ├── media.py                    # FFmpeg, OpenCV face-tracking, faster-whisper y render 9:16
│   │   └── translation.py              # Paquetes JSON EN↔ES, QA de subtítulos y exportación SRT
│   ├── publishing/
│   │   └── publication.py              # SHA-256, cola CSV segura, ledger SQLite y YouTube Data API
│   └── desktop/
│       └── launcher.py                 # Base para la futura app de escritorio local con acceso directo
├── tests/                              # Pruebas unitarias offline + prueba de render FFmpeg
├── assets/icons/clipforge.svg          # Logo vectorial base del proyecto
├── scripts/create_desktop_shortcut.ps1 # Script para crear el acceso directo en el escritorio de Windows
├── docs/                               # Arquitectura y roadmap
└── clipforge.py                        # Wrapper de compatibilidad en raíz
```

## Mapa de las 22 herramientas open-source (por niveles)

En lugar de instalar las 22 herramientas a la vez (lo que rompería el entorno en Windows y multiplicaría el tiempo de render), se organizan en 3 niveles:

### Nivel 1 — Núcleo activo (MVP funcionando hoy)

| # | Herramienta | Rol en ClipForge |
|---|---|---|
| 01 | **yt-dlp** | Descarga de fuentes autorizadas con procedencia (`download`) |
| 03 | **OpenCV** | Seguimiento facial suavizado (`--layout face`) |
| 04 | **OBS Studio** | Captura local de gameplay (GTA 6) y cámara multipista |
| 07 | **Whisper (`faster-whisper`)** | Transcripción local con timestamps por palabra |
| 16 | **libass (vía FFmpeg)** | Quemado de subtítulos en video vertical 1080×1920 |

### Nivel 2 — Siguiente salto de automatización (Fase 2)

| # | Herramienta | Cuándo activarla |
|---|---|---|
| 09 | **stable-ts** | Cuando quieras sincronía palabra por palabra estilo karaoke sin derivas |
| 15 | **pysubs2** | Cuando pases de SRT plano a plantillas `.ass` animadas (palabras resaltadas por color) |
| 13 | **DeepFilterNet** | Si `afftdn` de FFmpeg no basta para limpiar micrófono con ruido de teclado/ventilador |
| 14 | **Demucs** | Si grabas juego + voz en una sola pista y necesitas aislar tu voz para transcribir/subtitular |
| 08 | **whisper.cpp** | Alternativa ligera sin dependencias Python pesadas (útil si empaquetas binario autónomo) |
| 05 | **AI Shorts Generator** | Referencia de heurísticas para scoring de momentos con LLM local/externo |

### Nivel 3 — Especializado / Bajo demanda (Fase 3+)

| # | Herramienta | Cuándo activarla |
|---|---|---|
| 10 | **pyannote.audio** | Podcasts o streams con varios hablantes donde necesites diarización (quién habla cuándo) |
| 11 | **Supervision** | Overlays de detección o análisis visual en gameplay |
| 12 | **Norfair** | Tracking robusto multi-persona cuando dos sujetos se cruzan en cámara |
| 02 | **OpenCut** | Edición manual rápida en línea de tiempo web cuando un clip merezca retoque fino |
| 18 | **Kdenlive** | Edición manual avanzada con keyframes en escritorio |
| 19 | **Shotcut** | Mezcla manual alternativa de pistas y filtros |
| 17 | **Editly** | Ensamblado declarativo en Node para plantillas complejas (intros/outros) |
| 06 | **Mediabunny** | Exportación/conversión dentro del navegador si la UI local migra a editor web |
| 22 | **PyAV** | Decodificación frame-a-frame de alto rendimiento en Python si OpenCV/FFmpeg quedan cortos |
| 20 | **Real-ESRGAN** | Escalado puntual solo para clips de baja resolución (alto costo de GPU/CPU) |
| 21 | **RIFE** | Interpolación a 60 fps solo para jugadas específicas donde valga el tiempo de render |

## Arquitectura Multi-Agente Local

- **Gemini Antigravity**: director y desarrollador principal sobre el repo.
- **Claude Code (`claude -p`)**: localización EN↔ES natural, revisión de tono/jerga gamer y hooks.
- **OpenAI Codex (`codex exec -`)**: auditoría de código, validación de edge cases y segunda opinión de subtítulos.
- **Contrato común**: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` y `.agents/`.
