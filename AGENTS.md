# ClipForge — Agent Instructions

Este archivo es la fuente de instrucciones compartida para **Gemini Antigravity**, **Claude Code** y **OpenAI Codex**.

## Objetivo del proyecto

ClipForge es un sistema **100% local y personal** para convertir grabaciones largas (gameplay como GTA 6 y streams con cámara/reacción) en clips verticales (9:16) con subtítulos bilingües (**EN ↔ ES**), aprobados localmente y listos para YouTube Shorts y TikTok (más Reels opcionales).

## Mapa rápido del código

- `src/clipforge/cli.py`: CLI principal (`download`, `process`, `review`, `approve`, `queue`, `translate-pack`, `translate-apply`, `youtube`, `youtube-resolve`).
- `src/clipforge/config.py`: rutas locales y perfiles de canal (`.agents/memory/channel-profiles.json`).
- `src/clipforge/pipeline/highlights.py`: detección de candidatos, parseo y generación de SRT.
- `src/clipforge/pipeline/media.py`: FFmpeg, detección facial OpenCV, transcripción `faster-whisper` y render vertical 1080×1920.
- `src/clipforge/pipeline/translation.py`: empaquetado JSON, validación de legibilidad y aplicación de subtítulos traducidos preservando timestamps exactos.
- `src/clipforge/publishing/publication.py`: huella SHA-256, cola CSV, registro SQLite idempotente y carga OAuth a YouTube.
- `src/clipforge/desktop/launcher.py`: base para la futura app de escritorio local con acceso directo e ícono.
- `tests/`: pruebas unitarias (`python -m unittest discover -v`).

## Arquitectura agéntica (`.agents/`)

Consulta las carpetas en `.agents/` antes de ejecutar flujos complejos:

- **Reglas**: `.agents/rules/architecture.md`, `.agents/rules/translation-style.md`, `.agents/rules/safety-and-rights.md`.
- **Roles**: `.agents/roles/` (`director.md`, `clip-editor.md`, `translator-es-en.md`, `qa-reviewer.md`, `publisher.md`).
- **Skills**: `.agents/skills/` (`vod-to-clips`, `bilingual-subtitles`, `multi-agent-review`, `publish-workflow`).
- **Flujos**: `.agents/workflows/vod-pipeline.md`.
- **Memoria editable**: `.agents/memory/glossary-gaming.md` y `.agents/memory/channel-profiles.json`.
- **Handoffs temporales** (ignorados por git): `.agents/handoffs/`.

## Principios obligatorios

1. **Simplicidad y operación local**: no añadas Docker, servicios cloud ni dependencias pesadas de la lista de 22 herramientas sin una necesidad comprobada (ver `docs/ARCHITECTURE.md`).
2. **Integridad de timestamps**: al traducir subtítulos EN↔ES nunca inventes ni desplaces tiempos; trabaja con `translate-pack` y `translate-apply`.
3. **Derechos y secretos**: exige procedencia (`--rights`), nunca guardes secretos OAuth en el repo ni los imprimas en logs.
4. **Verificación**: tras modificar código Python, ejecuta `python -m unittest discover -v`.
