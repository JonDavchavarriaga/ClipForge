# Hoja de Ruta — Gevik Clip

## Fase 1 — Núcleo modular y arquitectura agéntica (Completada)

- [x] Estructura modular `src/clipforge/` + `tests/` + `pyproject.toml` + `.agents/` compartida para Antigravity, Claude Code y Codex.
- [x] Pipeline local: detección de reacciones, transcripción `faster-whisper`, seguimiento facial OpenCV, render 1080×1920 y carga opcional idempotente a YouTube.

## Fase 2 — `split-cam`, subtítulos dinámicos `.ass` y sincronía exacta (Completada)

- [x] Modo `split-cam` con recorte visual de cámara sobre fotograma (estilo Recortes de Windows) y prueba rápida en el 1er clip (`preview_first`).
- [x] Catálogo visual de 6 estilos `.ass` en tarjetas (`Amarillo Viral`, `Cian Gaming`, `Impacto Naranja`, `Gevik Plata`, `Blanco Limpio`, `Sin subtítulos`).
- [x] Sincronización exacta (0 ms de desfase) mediante `trim`/`atrim` dentro del filtergraph y alineación por clip (ver [ADR-002](decisions/0002-zero-drift-filtergraph-subtitle-sync.md)).
- [x] Detección y salto automático de comerciales mudos en Twitch y escalado automático de cantidad de clips en lives largos (1h–2h+).

## Fase 3 — App de Escritorio **Gevik Clip**, Traducción 1-Clic y Mini-Editor (Completada)

- [x] Identidad visual **Gevik Clip** (negro obsidiana y plateado metálico **`G`**), `assets/icons/gevik-clip.svg`, `gevik-clip.ico` y acceso directo `Gevik Clip.lnk` en el Escritorio.
- [x] Ventana de Live en vivo (Twitch / Kick / YouTube Live) con grabación completa 1h–2h+ y auto-clip por picos de reacción.
- [x] Traducción automática 1-clic de subtítulos (sin modificar audio original) usando suscripciones locales **Claude Code**, **Codex** y **Gemini** (ver [ADR-003](decisions/0003-local-subscription-ai-subtitles.md)).
- [x] Gestor profesional de clips con nombres legibles, miniaturas reales 9:16, estados (`Por revisar` / `Aprobado` / `Descartado`), vaciado de descartados y descarga directa MP4 en 1 clic.
- [x] Mini-Editor integrado: recorte `IN`/`OUT`, altura exacta de subtítulos (`12%–90%`), tamaño de cámara split (`20%–52%`) con guías visuales en vivo, sincronía fina (`-2000..+2000 ms`) y audio.
- [x] Gestión de espacio en **Biblioteca**: tamaño en MB/GB, marca `✓ Procesado` y borrado seguro de lives completos y clips en vivo.
- [x] Estabilidad profesional: instancia única, `ProcessRegistry` con baja prioridad, botón **Cancelar** en la cola, descarga automática de `WhisperModel` inactivo y cierre limpio total.

## Fase 4 — Mejoras opcionales futuras (Bajo demanda)

- [ ] Limpieza avanzada de voz (`DeepFilterNet` / `Demucs`) como opción activable solo para streams con audio muy contaminado.
- [ ] Diarización (`pyannote.audio`) para podcasts o streams con varios hablantes.
- [ ] Detección de picos visuales en gameplay silencioso (ej. jugadas de GTA 6 sin grito en micrófono).
