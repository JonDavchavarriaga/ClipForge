# Gevik Clip — Agent Instructions

Este archivo es la fuente única de instrucciones compartida para **Gemini Antigravity**, **Claude Code** y **OpenAI Codex**.

## Objetivo e identidad del proyecto

**Gevik Clip** (identidad **negro obsidiana y plateado metálico**, monograma **`G`**) es una aplicación de escritorio **100% local y personal** en Windows para capturar streams en vivo (Twitch, Kick, YouTube Live), generar clips verticales (**9:16**) de gameplay (como GTA 6) y streams con cámara/reacción, traducir **únicamente los subtítulos** (**EN ↔ ES**, sin tocar el audio original) usando las suscripciones IA locales del usuario, editar visualmente el encuadre en el Mini-Editor y descargar los MP4 listos para YouTube Shorts y TikTok.

> **Nota técnica**: el paquete Python interno se mantiene en `src/clipforge/` (y el punto de entrada `clipforge.py`) para preservar `.venv` y compatibilidad de imports (ver [ADR-001](docs/decisions/0001-local-first-desktop-architecture.md)). Toda la UI, assets (`assets/icons/gevik-clip.*`), acceso directo (`Gevik Clip.lnk`) y documentación usan exclusivamente la marca **Gevik Clip**.

## Mapa rápido del código

- `src/clipforge/cli.py`: CLI principal (`app`, `live`, `process`, `rerender`, `review`, `approve`, `translate-pack`, `translate-apply`, `youtube`, `youtube-resolve`) y flujo `preview_first` de calibración de cámara.
- `src/clipforge/config.py`: rutas locales y perfiles de canal (`.agents/memory/channel-profiles.json`).
- `src/clipforge/storage.py` y `logging_utils.py`: estructura `workspace/` (`live_recordings/`, `live_clips/`, `rendered_clips/`, `sources/`, `logs/`) y logs persistentes en disco (no mostrar visor de logs en la UI; `ensure_safe_stdio()` protege `pythonw.exe`).
- `src/clipforge/process_manager.py`: `ProcessRegistry`, ejecución en baja prioridad en Windows (`BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW`), cancelación de tareas y lock de instancia única (`.gevik_instance.json`).
- `src/clipforge/pipeline/highlights.py`: detección de reacciones, auto-conteo para lives de 1h–2h+, exclusión de comerciales mudos y SRT.
- `src/clipforge/pipeline/media.py`: FFmpeg con recorte exacto dentro del filtergraph (`trim`/`atrim` + `setpts=PTS-STARTPTS` antes de `ass=` para 0 ms de desfase), OpenCV, `faster-whisper` (vía `load_wav_float32` sin `PyAV`, cacheado con descarga en inactividad) y miniaturas `thumb.jpg`.
- `src/clipforge/pipeline/subtitles.py`: catálogo visual de 6 estilos `.ass` (`pysubs2`), altura vertical `y_pct` (`12%–90%`) y `offset_ms`.
- `src/clipforge/pipeline/live.py`: buffer circular en vivo, grabación completa 1h–2h+ y auto-clip por picos de audio ignorando comerciales mudos.
- `src/clipforge/pipeline/translation.py` y `auto_translate.py`: empaquetado JSON EN↔ES, traducción 1-clic con suscripciones locales (**Claude Code**, **Codex**, **Gemini**) solo para subtítulos y preservación estricta de timestamps.
- `src/clipforge/pipeline/editor.py`: Mini-Editor local (Trim IN/OUT, cámara `split-cam` y `cam_split_pct`, altura `subtitle_y_pct`, `subtitle_offset_ms`, volumen y limpieza de audio).
- `src/clipforge/publishing/publication.py`: huella SHA-256, aprobación, ledger SQLite idempotente y carga opcional a YouTube OAuth.
- `src/clipforge/desktop/`: app de escritorio local (`launcher.py`, `server.py`, `jobs.py`, `icon.py` y `static/`).
- `tests/`: suite completa (`.\.venv\Scripts\python.exe -m unittest discover -v`).

## Decisiones de arquitectura (ADRs)

Lee estos ADRs antes de modificar el núcleo de render, traducción IA o ciclo de vida:
- [docs/decisions/0001-local-first-desktop-architecture.md](docs/decisions/0001-local-first-desktop-architecture.md)
- [docs/decisions/0002-zero-drift-filtergraph-subtitle-sync.md](docs/decisions/0002-zero-drift-filtergraph-subtitle-sync.md)
- [docs/decisions/0003-local-subscription-ai-subtitles.md](docs/decisions/0003-local-subscription-ai-subtitles.md)

## Arquitectura agéntica (`.agents/`)

- **Reglas**: `.agents/rules/architecture.md`, `.agents/rules/translation-style.md`, `.agents/rules/safety-and-rights.md`.
- **Roles**: `.agents/roles/` (`director.md`, `clip-editor.md`, `translator-es-en.md`, `qa-reviewer.md`, `publisher.md`).
- **Skills**: `.agents/skills/` (`vod-to-clips`, `bilingual-subtitles`, `multi-agent-review`, `publish-workflow`).
- **Flujos**: `.agents/workflows/vod-pipeline.md`.
- **Memoria editable**: `.agents/memory/glossary-gaming.md` y `.agents/memory/channel-profiles.json`.
- **Handoffs temporales** (ignorados por git): `.agents/handoffs/`.

## Principios obligatorios

1. **Simplicidad y operación 100% local**: no añadas Docker, servicios cloud ni dependencias pesadas sin necesidad comprobada (ver `docs/ARCHITECTURE.md`).
2. **Sincronización exacta (regla crítica de FFmpeg)**: nunca coloques `-ss` después de `-i` cuando el comando queme `ass=` o `subtitles=`. Usa siempre `trim`/`atrim` + `setpts=PTS-STARTPTS` antes de `ass=` y `extract_clip_wav` (ver ADR-002).
3. **Subtítulos IA sin alterar audio**: la traducción 1-clic usa suscripciones locales (`claude.exe`, `codex.cmd`, `gemini`) únicamente sobre texto/subtítulos preservando timestamps exactos; nunca modifica el audio original ni invoca shims Unix sin extensión en Windows (ver ADR-003).
4. **Seguridad y secretos**: nunca guardes credenciales OAuth ni claves en el repo ni las imprimas en logs; valida rutas de borrado estrictamente dentro de `workspace/`.
5. **Verificación**: tras modificar código Python ejecuta `.\.venv\Scripts\python.exe -m unittest discover -v`, y tras tocar JS ejecuta `node --check .\src\clipforge\desktop\static\app.js`.
